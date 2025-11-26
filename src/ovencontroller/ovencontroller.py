"""High-level oven controller entry point."""

import time

import microcontroller

from .display_manager import DisplayContext, DisplayManager, Layout
from .hardware import create_hardware
from .pid_controller import PIDController
from .scroll_queue import ScrollQueue
from .settings_store import SettingsStore
from .simulated_oven import SimulatedOven
from .updater import OTAUpdater
from .logger import logger



class OvenController:
    """Stateful oven controller that orchestrates hardware and UI."""

    VERSION_URL = "http://192.168.1.22:8000/version.txt"
    MANIFEST_URL = "http://192.168.1.22:8000/manifest.json"
    FILE_BASE_URL = "http://192.168.1.22:8000/"
    LOCAL_VERSION = "/ovencontroller/version.txt"
    SETTINGS_PATH = "/settings.toml"

    SETTINGS_FILE = "oven_settings.json"

    STATE_OFF = 0
    STATE_BAKE = 1
    STATE_BROIL = 2
    STATE_SETTINGS = 3
    STATE_MODE_SELECT = 4
    STATE_ALARM = 5

    MODE_LIST = (STATE_OFF, STATE_BAKE, STATE_BROIL, STATE_SETTINGS)

    MIN_SET_TEMP = 90
    MAX_SET_TEMP = 550
    DEFAULT_SET_TEMP = 350
    STEP_SEQUENCE = (25, 5, 1)
    STEP_LABELS = {
        25: "+-25",
        5: "+-5 ",
        1: "+-1 ",
    }

    DISPLAY_UPDATE_RATE = 1 / 15.0
    TEMP_READ_RATE = 0.01
    TEMP_DISPLAY_RATE = 1.0
    CONTROL_UPDATE_RATE = 1.0
    LONG_PRESS_TIME = 0.8
    MODE_SELECT_TIMEOUT = 10.0
    TEMP_SMOOTHING_WINDOW = 500

    BROIL_HIGH_TEMP = 550.0
    BROIL_LOW_TEMP = 450.0

    BROIL_LEVEL_LOW = 0
    BROIL_LEVEL_HIGH = 1
    BROIL_LEVEL_LABELS = {
        BROIL_LEVEL_LOW: "L-LO",
        BROIL_LEVEL_HIGH: "L-HI",
    }
    BROIL_LEVEL_LABELS_SIM = {
        BROIL_LEVEL_LOW: "M-LO",
        BROIL_LEVEL_HIGH: "M-HI",
    }

    HEAT_MODE_OFF = "off"
    HEAT_MODE_DIRECT = "direct"
    HEAT_MODE_PID = "pid"

    BRIGHTNESS_MIN = 0.0
    BRIGHTNESS_MAX = 1.0
    BRIGHTNESS_STEP = 0.05

    DEFAULT_PID_KP = 0.05
    DEFAULT_PID_KI = 0.001
    DEFAULT_PID_KD = 0.1
    DEFAULT_PID_WINDOW_DELTA = 15.0

    PID_GAIN_MIN = 0.0
    PID_GAIN_MAX = 9.99
    PID_WINDOW_DELTA_MIN = 1.0
    PID_WINDOW_DELTA_MAX = 99.0
    PID_OUTPUT_MIN = 0.0
    PID_OUTPUT_MAX = 1.0
    PID_INTEGRAL_LIMIT = 1000.0
    PID_CYCLE_TIME = 5.0
    DUTY_MIN_SET_TEMP = 90.0
    DUTY_FULL_SET_TEMP = 200.0
    DUTY_MIN = 0.10
    DUTY_MIN_ON_TIME = 0.25
    DUTY_MIN_OFF_TIME = 0.25

    DECIMAL_FLASH_PERIOD = 0.25
    SCROLL_SPEED_DEFAULT = 0.2
    SCROLL_SPEED_MIN = 0.05
    SCROLL_SPEED_MAX = 1.0

    def __init__(self):
        logger.info("Oven controller starting...")
        logger.info("intializing hardware")
        self.hardware = create_hardware(0.0)

        logger.info("retrieving settings from json file")
        self.settings = SettingsStore(self.SETTINGS_FILE)
        
        self.display_brightness = self._clamp_brightness(
            self.settings.get("brightness")
        )
        if self.settings.set("brightness", self.display_brightness):
            # Persist clamped brightness immediately so that the stored value
            # always reflects the applied brightness level.
            self.settings.save_if_dirty()

        self.scroll_speed = self._load_setting_float(
            "scroll_speed",
            self.SCROLL_SPEED_DEFAULT,
            self.SCROLL_SPEED_MIN,
            self.SCROLL_SPEED_MAX,
        )

        self.pid_kp = self._load_setting_float(
            "pid_kp", self.DEFAULT_PID_KP, self.PID_GAIN_MIN, self.PID_GAIN_MAX
        )
        self.pid_ki = self._load_setting_float(
            "pid_ki", self.DEFAULT_PID_KI, self.PID_GAIN_MIN, self.PID_GAIN_MAX
        )
        self.pid_kd = self._load_setting_float(
            "pid_kd", self.DEFAULT_PID_KD, self.PID_GAIN_MIN, self.PID_GAIN_MAX
        )
        self.pid_window_delta = self._load_setting_float(
            "pid_window_delta",
            self.DEFAULT_PID_WINDOW_DELTA,
            self.PID_WINDOW_DELTA_MIN,
            self.PID_WINDOW_DELTA_MAX,
        )

        logger.info("Initializing PID Controller")
        self.pid = PIDController(
            self.pid_kp,
            self.pid_ki,
            self.pid_kd,
            output_min=self.PID_OUTPUT_MIN,
            output_max=self.PID_OUTPUT_MAX,
            integral_limit=self.PID_INTEGRAL_LIMIT,
            cycle_time=self.PID_CYCLE_TIME,
        )

        logger.info("Initializing Display Manager")
        self.display_manager = DisplayManager(self.hardware.displays)
        self._register_layouts()
        self.display_manager.apply_brightness(self.display_brightness)

        logger.info("Initializing State System")
        self.current_state = self.STATE_OFF
        self.selected_mode = self.STATE_BAKE
        self.set_temp = self.DEFAULT_SET_TEMP
        self.step_index = 0
        self.current_step = self.STEP_SEQUENCE[self.step_index]
        self.bottom_element_on = False
        self.top_element_on = False
        self._simulator = SimulatedOven()
        self.temp_from_simulator = True
        self.oven_temp = self._read_oven_temp()
        self.last_main_mode = self.STATE_OFF
        self.broil_level = self.BROIL_LEVEL_HIGH

        self.settings_index = 0
        self.settings_options = self._build_settings_options()
        self.mode_select_start = None
        self.scroll_queue = ScrollQueue()
        self.updater = OTAUpdater(
            settings_path=self.SETTINGS_PATH,
            version_url=self.VERSION_URL,
            manifest_url=self.MANIFEST_URL,
            file_base_url=self.FILE_BASE_URL,
            local_version_path=self.LOCAL_VERSION,
            target_folder="/ovencontroller",
            status_callback=self._set_update_status,
            message_callback=self._start_scroll_message,
        )
        self._current_version = self.updater.read_local_version()
        self._set_version_text(self._current_version)
        self.update_status = self._current_version or "TE N"
        self.update_choice = "TE N"
        self.force_update_choice = "TE N"
        self.last_activity = time.monotonic()
        self.idle_display_active = False
        self._temp_samples = []
        self._last_temp_log_time = 0.0
        self._last_logged_set_temp = None
        self._last_logged_oven_temp = None

        self.last_button_press_time = None
        self.long_press_handled = False
        self.last_display_update = 0.0
        self.last_temp_read = 0.0
        self.last_temp_display = 0.0
        self.last_control_update = 0.0
        now = time.monotonic()
        self._duty_state_on = False
        self._duty_next_transition = now
        self._last_duty_value = None
        self.last_encoder_pos = self.hardware.seesaw_rotary_encoder.position

        self._reset_pid()

        self.heating_mode = self.HEAT_MODE_OFF
        self.decimal_flash_visible = False
        self.last_decimal_flash_toggle = time.monotonic()
        self.decimal_flash_active = False

        self._set_pixel_for_state(self.current_state)
        self._render_display(force=True)

    def run(self):
        logger.info("Init complete; entering main loop.")
        try:
            while True:
                now = time.monotonic()
                self._update_main_mode()
                self._poll_encoder()
                self._poll_button(now)
                self._update_mode_select_timeout(now)
                self._update_temperature(now)
                self._update_control(now)
                self._check_idle_display(now)
                self._update_display(now)
                self._log_temperature_samples(now)
                if self.scroll_queue.ready_to_reset():
                    self.scroll_queue.clear_reset()
                    microcontroller.reset()
                time.sleep(0.002)
        except Exception as error:
            import traceback

            logger.error("Run loop exception:", error)
            try:
                formatted = traceback.format_exception(
                    type(error), error, error.__traceback__
                )
                for line in formatted:
                    logger.error(line.strip())
                traceback.print_exception(type(error), error, error.__traceback__)
            except Exception as trace_error:  # noqa: BLE001
                logger.error("Failed to print traceback:", trace_error)
            try:
                self._start_scroll_message(f"ERROR: {error}")
            except Exception as scroll_error:  # noqa: BLE001
                logger.error("Failed to queue scroll message for error:", scroll_error)
            raise

    # ------------------------------------------------------------------
    # Hardware interactions
    # ------------------------------------------------------------------

    def _register_layouts(self):
        def bake_step(context):
            return self.STEP_LABELS.get(context.step, "+-??")

        def mode_label(context):
            return context.mode_label(context.mode_sel)

        def bake_label(context):
            return context.mode_label(context.mode_sel)

        def broil_label(context):
            return context.mode_label(context.mode_sel)

        def show_temp(context):
            return context.fmt_temp(context.oven_temp)

        def show_set_temp(context):
            return context.fmt_temp(context.set_temp)

        def show_broil_level(context):
            return context.broil_label(context.broil_level)

        def show_version(context):
            return context.version_text

        def show_setting_label_top(context):
            return context.setting_label_top or "    "

        def show_setting_label_bottom(context):
            return context.setting_label_bottom or "    "

        def show_setting_value(context):
            return context.setting_value or "    "

        self.display_manager.register_layout(
            self.STATE_OFF,
            Layout(
                tl="OFF ",
                tr="    ",
                bl=show_version,
                br=show_temp,
            ),
        )

        self.display_manager.register_layout(
            self.STATE_BAKE,
            Layout(
                tl=bake_label,
                tr=show_set_temp,
                bl=bake_step,
                br=show_temp,
            ),
        )

        self.display_manager.register_layout(
            self.STATE_BROIL,
            Layout(
                tl=broil_label,
                tr=show_broil_level,
                bl="    ",
                br=show_temp,
            ),
        )

        self.display_manager.register_layout(
            self.STATE_SETTINGS,
            Layout(
                tl=show_setting_label_top,
                tr=show_setting_value,
                bl=show_setting_label_bottom,
                br=show_temp,
            ),
        )

        self.display_manager.register_layout(
            self.STATE_MODE_SELECT,
            Layout(
                tl="MODE",
                tr=mode_label,
                bl="    ",
                br=show_temp,
            ),
        )

        self.display_manager.register_layout(
            self.STATE_ALARM,
            Layout(
                tl="ALRM",
                tr="Err ",
                bl="ALRM",
                br="Err ",
            ),
        )

    def _render_display(self, force=False, now=None):
        if now is None:
            now = time.monotonic()
        flash_tr, flash_br = self._update_decimal_flash(now)
        (
            setting_label_top,
            setting_label_bottom,
            setting_value,
        ) = self._current_setting_display()

        scroll_overrides = self.scroll_queue.overrides(now)

        if self.idle_display_active:
            if force:
                self.hardware.displays.reset_cache()
            self.display_manager.render_texts(
                {"tl": "    ", "tr": "   .", "bl": "    ", "br": "    "}
            )
            return

        context = DisplayContext(
            state=self.current_state,
            set_temp=self.set_temp,
            oven_temp=self.oven_temp,
            step=self.current_step,
            mode_sel=self.selected_mode,
            brightness=self.display_brightness,
            fmt_temp=self._fmt_temp,
            fmt_brightness=self._fmt_brightness,
            mode_label=self._mode_label,
            broil_level=self.broil_level,
            broil_label=self._broil_label,
            setting_label_top=setting_label_top,
            setting_label_bottom=setting_label_bottom,
            setting_value=setting_value,
            flash_tr_decimals=flash_tr,
            flash_br_decimals=flash_br,
            decimal_visible=self.decimal_flash_visible,
            temp_from_simulator=self.temp_from_simulator,
            scroll_overrides=scroll_overrides,
            version_text=self._version_text,
        )
        if force:
            # Reset cached display values so that the first render prints to all.
            self.hardware.displays.reset_cache()
        self.display_manager.render(context)

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _state_name(self, state):
        names = {
            self.STATE_OFF: "OFF",
            self.STATE_BAKE: "BAKE",
            self.STATE_BROIL: "BROIL",
            self.STATE_SETTINGS: "SETT",
            self.STATE_MODE_SELECT: "MODE",
            self.STATE_ALARM: "ALRM",
        }
        return names.get(state, f"STATE_{state}")

    def _describe_setting_option(self, option):
        label_top = option.get("label_top") or ""
        label_bottom = option.get("label_bottom") or ""
        combined = (label_top + label_bottom).strip()
        if combined:
            return combined
        label = option.get("label")
        if label:
            return str(label).strip()
        key = option.get("key")
        if key:
            return str(key).strip()
        attr = option.get("attr")
        if attr:
            return str(attr).strip()
        return "setting"

    def _log_state_transition(self, previous_state, new_state):
        if previous_state == new_state:
            return
        logger.info(
            "Mode change:",
            self._state_name(previous_state),
            "->",
            self._state_name(new_state),
        )

    def _log_setting_change(self, option, value):
        label = self._describe_setting_option(option)
        logger.info("Setting change:", label, "->", value)

    def _log_temperature_samples(self, now):
        if (now - self._last_temp_log_time) < 5.0:
            return
        set_temp = getattr(self, "set_temp", None)
        oven_temp = getattr(self, "oven_temp", None)
        if (
            set_temp == self._last_logged_set_temp
            and oven_temp == self._last_logged_oven_temp
        ):
            return
        self._last_temp_log_time = now
        self._last_logged_set_temp = set_temp
        self._last_logged_oven_temp = oven_temp
        logger.info(
            "Temp snapshot -> set:",
            self._format_temp_for_log(set_temp),
            "oven:",
            self._format_temp_for_log(oven_temp),
        )

    @staticmethod
    def _format_temp_for_log(value):
        if value is None:
            return "n/a"
        try:
            return f"{float(value):.1f}F"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _pad_four(text):
        if text is None:
            text = ""
        text = str(text)
        buffer = []
        glyphs = 0
        for char in text:
            if char == "." and not buffer:
                # Skip leading decimals so they don't orphan.
                continue
            buffer.append(char)
            if char != ".":
                glyphs += 1
            if glyphs >= 4:
                break
        while glyphs < 4:
            buffer.append(" ")
            glyphs += 1
        return "".join(buffer)

    def _set_pixel_for_state(self, state):
        if state == self.STATE_OFF:
            color = (0, 0, 0)
        elif state == self.STATE_BAKE:
            color = (0, 40, 0)
        elif state == self.STATE_BROIL:
            color = (40, 0, 0)
        elif state == self.STATE_SETTINGS:
            color = (40, 40, 0)
        elif state == self.STATE_MODE_SELECT:
            color = (0, 0, 40)
        elif state == self.STATE_ALARM:
            color = (40, 0, 40)
        else:
            color = (5, 5, 5)
        self.hardware.pixel[0] = color

    def _set_elements(self, bottom_on, top_on, *, reason=None):
        bottom_on = bool(bottom_on)
        top_on = bool(top_on)
        if (
            bottom_on != self.bottom_element_on
            or top_on != self.top_element_on
        ):
            state_parts = [
                f"bottom={'ON' if bottom_on else 'OFF'}",
                f"top={'ON' if top_on else 'OFF'}",
            ]
            prefix = f"Setting elements ({reason})" if reason else "Setting elements"
            logger.info(prefix + ":", ", ".join(state_parts))
        self.bottom_element_on = bottom_on
        self.top_element_on = top_on
        self.hardware.set_elements(bottom_on, top_on)

    def _set_heating_mode(self, mode):
        if mode == self.heating_mode:
            return
        logger.info(
            "Heating mode change:",
            self.heating_mode.upper(),
            "->",
            mode.upper(),
        )
        self.heating_mode = mode

    def _update_decimal_flash(self, now):
        flash_tr, flash_br = self._compute_decimal_targets()
        active = flash_tr or flash_br
        if not active:
            if self.decimal_flash_active:
                self.decimal_flash_visible = False
            self.decimal_flash_active = False
            self.last_decimal_flash_toggle = now
            return flash_tr, flash_br
        if not self.decimal_flash_active:
            self.decimal_flash_active = True
            self.decimal_flash_visible = True
            self.last_decimal_flash_toggle = now
            return flash_tr, flash_br
        if (now - self.last_decimal_flash_toggle) >= self.DECIMAL_FLASH_PERIOD:
            self.decimal_flash_visible = not self.decimal_flash_visible
            self.last_decimal_flash_toggle = now
        return flash_tr, flash_br

    def _compute_decimal_targets(self):
        flash_tr = (
            self.top_element_on
            and self.current_state in (self.STATE_BAKE, self.STATE_BROIL)
        )
        flash_br = self.bottom_element_on
        return flash_tr, flash_br

    def _start_scroll_message(self, message):
        sanitized = str(message or "").upper()
        if not sanitized.strip():
            return
        self.scroll_queue.queue_message(sanitized, self.scroll_speed)

    def _set_update_status(self, value):
        text = str(value or "")
        if self.update_status == text:
            return
        self.update_status = text
        if self.current_state == self.STATE_SETTINGS:
            self._render_display()
        logger.info("Update status ->", self.update_status)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _update_main_mode(self):
        if self.current_state in (self.STATE_OFF, self.STATE_BAKE, self.STATE_BROIL):
            self.last_main_mode = self.current_state

    def _poll_encoder(self):
        try:
            position = self.hardware.seesaw_rotary_encoder.position
        except OSError as error:
            logger.error("i/o error reading encoder ->", error)
            time.sleep(5)
            if self.hardware.recover_seesaw():
                try:
                    position = self.hardware.seesaw_rotary_encoder.position
                except OSError as retry_error:
                    logger.error(
                        "encoder read retry failed after recovery ->", retry_error
                    )
                    raise
            else:
                raise
        delta = position - self.last_encoder_pos
        if delta == 0:
            return
        self.last_encoder_pos = position
        self._record_activity()

        if self.current_state == self.STATE_MODE_SELECT:
            index = self.MODE_LIST.index(self.selected_mode)
            step_dir = 1 if delta > 0 else -1
            index = (index + step_dir) % len(self.MODE_LIST)
            self.selected_mode = self.MODE_LIST[index]
            self.mode_select_start = time.monotonic()
        elif self.current_state == self.STATE_BAKE:
            step = self.current_step
            if step in (5, 25):
                step_dir = 1 if delta > 0 else -1
                new_temp = int(round(self.set_temp))
                for _ in range(abs(delta)):
                    if step_dir > 0:
                        new_temp = self._next_multiple(new_temp, step)
                    else:
                        new_temp = self._previous_multiple(new_temp, step)
            else:
                new_temp = self.set_temp + (delta * step)
            self.set_temp = self._clamp(
                new_temp,
                self.MIN_SET_TEMP,
                self.MAX_SET_TEMP,
            )
        elif self.current_state == self.STATE_BROIL:
            step_dir = 1 if delta > 0 else -1
            new_level = self.broil_level + step_dir
            new_level = self._clamp(new_level, self.BROIL_LEVEL_LOW, self.BROIL_LEVEL_HIGH)
            if new_level != self.broil_level:
                self.broil_level = new_level
        elif self.current_state == self.STATE_SETTINGS:
            self._adjust_current_setting(delta)

    def _poll_button(self, now):
        button = self.hardware.button
        try:
            button.update()
        except OSError as error:
            logger.error("i/o error reading button ->", error)
            time.sleep(5)
            if self.hardware.recover_seesaw():
                button = self.hardware.button
                try:
                    button.update()
                except OSError as retry_error:
                    logger.error("button read retry failed after recovery ->", retry_error)
                    raise
            else:
                raise

        if button.fell:
            self._record_activity()
            self.last_button_press_time = now
            self.long_press_handled = False

        if (
            self.last_button_press_time is not None
            and not self.long_press_handled
            and not button.value
            and (now - self.last_button_press_time) >= self.LONG_PRESS_TIME
        ):
            if self.current_state == self.STATE_SETTINGS:
                self._save_settings()
                self._transition_to(self.last_main_mode)
            elif self.current_state != self.STATE_MODE_SELECT:
                if self.current_state == self.STATE_OFF:
                    self.selected_mode = self.STATE_BAKE
                elif self.current_state in (self.STATE_BAKE, self.STATE_BROIL):
                    self.selected_mode = self.STATE_OFF
                else:
                    self.selected_mode = self.current_state
                self._transition_to(self.STATE_MODE_SELECT)
            self.long_press_handled = True

        if button.rose and self.last_button_press_time is not None:
            self._record_activity()
            if not self.long_press_handled:
                if self.current_state in (self.STATE_BAKE, self.STATE_OFF, self.STATE_BROIL):
                    self.step_index = (self.step_index + 1) % len(self.STEP_SEQUENCE)
                    self.current_step = self.STEP_SEQUENCE[self.step_index]
                elif self.current_state == self.STATE_MODE_SELECT:
                    self._apply_selected_mode()
                elif self.current_state == self.STATE_SETTINGS:
                    self._advance_setting_option()
                    self._render_display()
            self.last_button_press_time = None
            self.long_press_handled = False

    def _update_temperature(self, now):
        if (now - self.last_temp_read) < self.TEMP_READ_RATE:
            return
        self.last_temp_read = now
        latest_temp = self._read_oven_temp()
        self._record_temp_sample(latest_temp)
        if (now - self.last_temp_display) < self.TEMP_DISPLAY_RATE:
            return
        self.last_temp_display = now
        smoothed = self._smoothed_temp()
        if smoothed is not None:
            self.oven_temp = smoothed
        elif latest_temp is not None:
            self.oven_temp = latest_temp
        if self.oven_temp >= (self.BROIL_HIGH_TEMP + 50):
            self._transition_to(self.STATE_ALARM)
            self._set_heating_mode(self.HEAT_MODE_OFF)
            self._set_elements(False, False, reason="alarm")

    def _update_control(self, now):
        if (now - self.last_control_update) < self.CONTROL_UPDATE_RATE:
            return
        self.last_control_update = now

        if self.current_state == self.STATE_BAKE:
            self._update_bake_control(now)
        elif self.current_state == self.STATE_BROIL:
            target = (
                self.BROIL_HIGH_TEMP
                if self.broil_level == self.BROIL_LEVEL_HIGH
                else self.BROIL_LOW_TEMP
            )
            self._set_heating_mode(self.HEAT_MODE_DIRECT)
            if self.oven_temp < target:
                if self.broil_level == self.BROIL_LEVEL_LOW:
                    duty_on = self._duty_active(0.5)
                    self._set_elements(False, duty_on, reason="broil control (low duty)")
                else:
                    self._duty_active(1.0)
                    self._set_elements(False, True, reason="broil control")
            else:
                self._duty_active(0.0)
                self._set_elements(False, False, reason="broil control")
        else:
            self._set_heating_mode(self.HEAT_MODE_OFF)
            self._set_elements(False, False)

    def _update_display(self, now):
        if (now - self.last_display_update) < self.DISPLAY_UPDATE_RATE:
            return
        self.last_display_update = now
        self._render_display(now=now)

    def _transition_to(self, state):
        previous_state = self.current_state
        self.current_state = state
        self._log_state_transition(previous_state, state)
        if state == self.STATE_MODE_SELECT:
            self.mode_select_start = time.monotonic()
        else:
            self.mode_select_start = None
        self._set_pixel_for_state(state)
        if state == self.STATE_SETTINGS:
            # Ensure brightness is immediately applied when entering settings.
            self.display_manager.apply_brightness(self.display_brightness)
            self.settings_index = 0
        if state == self.STATE_BAKE:
            self._reset_pid()
        if previous_state == self.STATE_BAKE and state != self.STATE_BAKE:
            self._reset_pid()
        self._render_display()

    def _save_settings(self):
        if self.settings.dirty:
            self.settings.save_if_dirty()

    def _update_mode_select_timeout(self, now):
        if self.current_state != self.STATE_MODE_SELECT:
            return
        if self.mode_select_start is None:
            self.mode_select_start = now
            return
        if (now - self.mode_select_start) < self.MODE_SELECT_TIMEOUT:
            return
        logger.info("Mode select timeout reached; applying selected mode.")
        self._apply_selected_mode()

    def _apply_selected_mode(self):
        if self.selected_mode == self.STATE_SETTINGS:
            self._transition_to(self.STATE_SETTINGS)
        else:
            self._transition_to(self.selected_mode)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _fmt_temp(self, value):
        try:
            numeric = int(round(value))
        except (TypeError, ValueError):
            return "Err "
        numeric = self._clamp(numeric, 0, 999)
        suffix = "S" if getattr(self, "temp_from_simulator", True) else "F"
        return "{:03d}{}".format(numeric, suffix)[-4:]

    def _fmt_brightness(self, value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "Err "
        numeric = self._clamp(numeric, self.BRIGHTNESS_MIN, self.BRIGHTNESS_MAX)
        percent = int(round(numeric * 100))
        percent = self._clamp(percent, 0, 100)
        return f"{percent:>3d}"

    def _fmt_pid_gain(self, value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "Err "
        numeric = self._clamp(numeric, self.PID_GAIN_MIN, self.PID_GAIN_MAX)
        return f"{numeric:>4.2f}"

    def _fmt_pid_window(self, value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "Err "
        numeric = self._clamp(
            numeric, self.PID_WINDOW_DELTA_MIN, self.PID_WINDOW_DELTA_MAX
        )
        return f"{numeric:>4.1f}"

    def _fmt_scroll_speed(self, value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "Err "
        numeric = self._clamp(numeric, self.SCROLL_SPEED_MIN, self.SCROLL_SPEED_MAX)
        return f"{numeric:>4.2f}"

    def _normalize_setting_label(self, text):
        cleaned = str(text or "")
        cleaned = cleaned.replace("\n", " ")
        cleaned = cleaned.upper()
        if len(cleaned) >= 8:
            return cleaned[:8]
        return cleaned + (" " * (8 - len(cleaned)))

    def _mode_label(self, state):
        if state == self.STATE_OFF:
            return "OFF "
        if state == self.STATE_BAKE:
            if getattr(self, "temp_from_simulator", True):
                return "BSIM"
            return "BAKE"
        if state == self.STATE_BROIL:
            if getattr(self, "temp_from_simulator", True):
                return "BRSI"
            return "BROI"
        if state == self.STATE_SETTINGS:
            return "SEt "
        return "    "

    def _broil_label(self, level):
        if getattr(self, "temp_from_simulator", True):
            return self.BROIL_LEVEL_LABELS_SIM.get(
                level, self.BROIL_LEVEL_LABELS_SIM[self.BROIL_LEVEL_HIGH]
            )
        return self.BROIL_LEVEL_LABELS.get(
            level, self.BROIL_LEVEL_LABELS[self.BROIL_LEVEL_HIGH]
        )
        

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _clamp_brightness(self, value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = self.BRIGHTNESS_MIN
        return self._clamp(numeric, self.BRIGHTNESS_MIN, self.BRIGHTNESS_MAX)

    @staticmethod
    def _clamp(value, lower, upper):
        if value < lower:
            return lower
        if value > upper:
            return upper
        return value

    @staticmethod
    def _next_multiple(value, step):
        if step <= 0:
            return value
        return ((int(value) + step) // step) * step

    @staticmethod
    def _previous_multiple(value, step):
        if step <= 0:
            return value
        value_int = int(value)
        remainder = value_int % step
        if remainder == 0:
            return value_int - step
        return value_int - remainder

    def _read_oven_temp(self):
        """Return the best available oven temperature in Fahrenheit."""

        temp_f = None
        temp_reading = self.hardware.read_thermocouple()
        if temp_reading is not None:
            try:
                temp_c = float(temp_reading)
            except (TypeError, ValueError):
                temp_c = None
            if temp_c is not None and temp_c > 0:
                temp_f = (temp_c * 9.0 / 5.0) + 32.0

        if temp_f is not None:
            self.temp_from_simulator = False
            return temp_f

        self.temp_from_simulator = True
        return self._simulator.read(
            set_temp=self.set_temp,
            bottom_on=self.bottom_element_on,
            top_on=self.top_element_on,
        )

    def _record_temp_sample(self, value):
        if value is None:
            return
        self._temp_samples.append(value)
        if len(self._temp_samples) > self.TEMP_SMOOTHING_WINDOW:
            self._temp_samples.pop(0)

    def _smoothed_temp(self):
        if not self._temp_samples:
            return None
        return sum(self._temp_samples) / len(self._temp_samples)

    def _load_setting_float(self, key, default, lower, upper):
        try:
            value = float(self.settings.get(key))
        except (TypeError, ValueError):
            value = default
        value = self._clamp(value, lower, upper)
        if self.settings.set(key, value):
            self.settings.save_if_dirty()
        return value

    def _build_settings_options(self):
        return [
            {
                "key": "brightness",
                "label": "DISPBRT ",
                "label_top": "DISP",
                "label_bottom": "BRT ",
                "attr": "display_brightness",
                "step": self.BRIGHTNESS_STEP,
                "min": self.BRIGHTNESS_MIN,
                "max": self.BRIGHTNESS_MAX,
                "formatter": self._fmt_brightness,
                "round": 2,
                "on_change": self._on_brightness_changed,
            },
            {
                "key": "pid_kp",
                "label": "PID PROP",
                "label_top": "PID ",
                "label_bottom": "PROP",
                "attr": "pid_kp",
                "step": 0.01,
                "min": self.PID_GAIN_MIN,
                "max": self.PID_GAIN_MAX,
                "formatter": self._fmt_pid_gain,
                "round": 2,
                "on_change": lambda _value: self._reset_pid(),
            },
            {
                "key": "pid_ki",
                "label": "PID INT",
                "label_top": "PID ",
                "label_bottom": "INTG",
                "attr": "pid_ki",
                "step": 0.01,
                "min": self.PID_GAIN_MIN,
                "max": self.PID_GAIN_MAX,
                "formatter": self._fmt_pid_gain,
                "round": 2,
                "on_change": lambda _value: self._reset_pid(),
            },
            {
                "key": "pid_kd",
                "label": "PID DER",
                "label_top": "PID ",
                "label_bottom": "DERV",
                "attr": "pid_kd",
                "step": 0.01,
                "min": self.PID_GAIN_MIN,
                "max": self.PID_GAIN_MAX,
                "formatter": self._fmt_pid_gain,
                "round": 2,
                "on_change": lambda _value: self._reset_pid(),
            },
            {
                "key": "pid_window_delta",
                "label": "TEMPBAND",
                "label_top": "TEMP",
                "label_bottom": "BAND",
                "attr": "pid_window_delta",
                "step": 0.5,
                "min": self.PID_WINDOW_DELTA_MIN,
                "max": self.PID_WINDOW_DELTA_MAX,
                "formatter": self._fmt_pid_window,
                "round": 1,
                "on_change": lambda _value: self._reset_pid(),
            },
            {
                "key": "scroll_speed",
                "label_top": "SCRL",
                "label_bottom": "SPEED",
                "attr": "scroll_speed",
                "step": 0.05,
                "min": self.SCROLL_SPEED_MIN,
                "max": self.SCROLL_SPEED_MAX,
                "formatter": self._fmt_scroll_speed,
                "round": 2,
            },
            {
                "key": "_update",
                "label_top": "UPDA",
                "label_bottom": "VERS",
                "attr": "update_choice",
                "choices": ["TE N", "TE Y"],
                "formatter": lambda _value, inst=self: inst.update_status,
                "on_change": self._on_update_choice_changed,
            },
            {
                "key": "_update_force",
                "label_top": "UPDA",
                "label_bottom": "FORC",
                "attr": "force_update_choice",
                "choices": ["TE N", "TE Y"],
                "formatter": lambda value: value,
                "on_change": self._on_force_update_choice_changed,
            },
        ]

    def _on_brightness_changed(self, value):
        self.display_manager.apply_brightness(value)

    def _adjust_current_setting(self, delta):
        if not self.settings_options or delta == 0:
            return
        option = self.settings_options[self.settings_index]
        key = option.get("key", "")
        action = option.get("action")
        if action is not None:
            action(delta)
            return
        choices = option.get("choices")
        if choices:
            current_value = getattr(self, option["attr"])
            try:
                index = choices.index(current_value)
            except ValueError:
                index = 0
            step_dir = 1 if delta > 0 else -1
            index = (index + step_dir) % len(choices)
            new_value = choices[index]
            setattr(self, option["attr"], new_value)
            callback = option.get("on_change")
            if callback is not None:
                callback(new_value)
            self._log_setting_change(option, new_value)
            if key and not key.startswith("_"):
                self.settings.set(key, new_value)
            return
        current = getattr(self, option["attr"])
        new_value = current + (delta * option["step"])
        new_value = self._clamp(new_value, option["min"], option["max"])
        round_digits = option.get("round")
        if round_digits is not None:
            new_value = round(new_value, round_digits)
        if new_value == current:
            return
        setattr(self, option["attr"], new_value)
        callback = option.get("on_change")
        if callback is not None:
            callback(new_value)
        self._log_setting_change(option, new_value)
        if key and not key.startswith("_"):
            self.settings.set(key, new_value)

    def _advance_setting_option(self):
        if not self.settings_options:
            return
        self.settings_index = (self.settings_index + 1) % len(self.settings_options)

    def _current_setting_display(self):
        if self.current_state != self.STATE_SETTINGS or not self.settings_options:
            return ("", "", "")
        option = self.settings_options[self.settings_index]
        value = getattr(self, option["attr"])
        formatter = option.get("formatter")
        if callable(formatter):
            formatted_value = formatter(value)
        else:
            formatted_value = f"{value!s:>4}"

        label_top = option.get("label_top")
        label_bottom = option.get("label_bottom")
        if label_top is None and label_bottom is None:
            label_text = option.get("label", "")
            label_text = self._normalize_setting_label(label_text)
            label_top = label_text[:4]
            label_bottom = label_text[4:8]
        else:
            label_top = self._normalize_setting_label(label_top or "")[:4]
            label_bottom = self._normalize_setting_label(label_bottom or "")[:4]

        return (label_top, label_bottom, formatted_value)

    def _record_activity(self):
        self.last_activity = time.monotonic()
        if self.idle_display_active:
            self.idle_display_active = False
            self._render_display(force=True)

    def _check_idle_display(self, now):
        if (
            self.current_state == self.STATE_OFF
            and (now - self.last_activity) >= 60
        ):
            if not self.idle_display_active:
                self.idle_display_active = True
                self._render_display(force=True)
        else:
            if self.idle_display_active:
                self.idle_display_active = False
                self._render_display(force=True)

    def _set_version_text(self, version):
        full = (version or "0.0.0").upper()
        display = self._pad_four(full)
        self._version_text = display
        logger.info(f"Version text set to '{display}' (full '{full}')")

    def _handle_update_setting(self, delta, *, force=False):
        if delta == 0:
            return
        self.update_choice = "TE N"
        self.force_update_choice = "TE N"
        self._set_update_status("CHK ")
        self._render_display()
        new_version = self.updater.check_for_update(
            current_version=self._current_version,
            force=force,
        )
        if new_version:
            self.scroll_queue.request_reset()
            self._current_version = new_version
            self._set_version_text(new_version)
            self._set_update_status(new_version)
        else:
            self._current_version = self.updater.read_local_version()
            self._set_version_text(self._current_version)
            self._set_update_status(self._current_version or "CURR")
        self._render_display()

    def _on_update_choice_changed(self, value):
        self.update_choice = value
        if value.endswith("Y"):
            self._handle_update_setting(1)

    def _on_force_update_choice_changed(self, value):
        self.force_update_choice = value
        if value.endswith("Y"):
            self._handle_update_setting(1, force=True)

    def _update_bake_control(self, now):
        lower_bound = self.set_temp - self.pid_window_delta
        upper_bound = self.set_temp + self.pid_window_delta

        if self.oven_temp <= lower_bound:
            self._apply_direct_heating(True, now)
            return
        if self.oven_temp >= upper_bound:
            self._apply_direct_heating(False, now)
            return

        error = self.set_temp - self.oven_temp
        self._set_heating_mode(self.HEAT_MODE_PID)
        output, _ = self.pid.update(error, now)
        duty_cap = self._duty_cap_for_temp()
        duty = self._clamp(output, 0.0, duty_cap)
        heater_on = self._duty_active(duty)
        self._set_elements(heater_on, False, reason="pid control")

    def _apply_direct_heating(self, request_on, now):
        self._set_heating_mode(self.HEAT_MODE_DIRECT)
        if not request_on:
            self._set_elements(False, False, reason="direct heating")
            self._reset_pid(now=now, error=self.set_temp - self.oven_temp)
            return
        duty = self._duty_cap_for_temp()
        heater_on = self._duty_active(duty)
        self._set_elements(heater_on, heater_on, reason="direct heating")
        if not heater_on:
            self._reset_pid(now=now, error=self.set_temp - self.oven_temp)

    def _reset_pid(self, *, now=None, error=0.0):
        self.pid.configure(kp=self.pid_kp, ki=self.pid_ki, kd=self.pid_kd)
        self.pid.reset(now=now, error=error)
        current = time.monotonic()
        self._duty_state_on = False
        self._duty_next_transition = current
        self._last_duty_value = None

    def _duty_cap_for_temp(self):
        set_point = self.set_temp or 0
        if set_point <= self.DUTY_MIN_SET_TEMP:
            return self.DUTY_MIN
        if set_point >= self.DUTY_FULL_SET_TEMP:
            return 1.0
        span = self.DUTY_FULL_SET_TEMP - self.DUTY_MIN_SET_TEMP
        scale = (set_point - self.DUTY_MIN_SET_TEMP) / span
        return self.DUTY_MIN + scale * (1.0 - self.DUTY_MIN)

    def _duty_active(self, duty):
        now = time.monotonic()
        if duty <= 0.0:
            self._duty_state_on = False
            self._duty_next_transition = now
            self._last_duty_value = None
            return False
        if duty >= 1.0:
            self._duty_state_on = True
            self._duty_next_transition = None
            self._last_duty_value = 1.0
            return True

        if (
            self._last_duty_value is None
            or abs(self._last_duty_value - duty) > 0.01
            or self._duty_next_transition is None
        ):
            self._duty_state_on = False
            off_time = max(
                self.DUTY_MIN_OFF_TIME,
                self.PID_CYCLE_TIME * max(0.0, 1.0 - duty),
            )
            self._duty_next_transition = now + off_time
            self._last_duty_value = duty
            return False

        if now >= self._duty_next_transition:
            if self._duty_state_on:
                off_time = max(
                    self.DUTY_MIN_OFF_TIME,
                    self.PID_CYCLE_TIME * max(0.0, 1.0 - duty),
                )
                self._duty_state_on = False
                self._duty_next_transition = now + off_time
            else:
                on_time = max(
                    self.DUTY_MIN_ON_TIME,
                    self.PID_CYCLE_TIME * duty,
                )
                self._duty_state_on = True
                self._duty_next_transition = now + on_time

        return self._duty_state_on
        self.pid_cycle_start = time.monotonic()


def run():
    controller = OvenController()
    controller.run()


if __name__ == "__main__":
    run()
