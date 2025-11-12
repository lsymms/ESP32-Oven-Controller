"""High-level oven controller entry point."""

import time

from display_manager import DisplayContext, DisplayManager, Layout
from hardware import create_hardware
from settings_store import SettingsStore

print("Oven controller starting...")


class OvenController:
    """Stateful oven controller that orchestrates hardware and UI."""

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
    TEMP_UPDATE_RATE = 0.5
    CONTROL_UPDATE_RATE = 1.0
    LONG_PRESS_TIME = 0.8

    BROIL_HIGH_TEMP = 550.0
    BROIL_LOW_TEMP = 450.0

    BROIL_LEVEL_LOW = 0
    BROIL_LEVEL_HIGH = 1
    BROIL_LEVEL_LABELS = {
        BROIL_LEVEL_LOW: "L-LO",
        BROIL_LEVEL_HIGH: "L-HI",
    }

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

    def __init__(self):
        self.settings = SettingsStore(self.SETTINGS_FILE)
        self.display_brightness = self._clamp_brightness(
            self.settings.get("brightness")
        )
        if self.settings.set("brightness", self.display_brightness):
            # Persist clamped brightness immediately so that the stored value
            # always reflects the applied brightness level.
            self.settings.save_if_dirty()

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

        self.hardware = create_hardware(self.display_brightness)
        self.display_manager = DisplayManager(self.hardware.displays)
        self._register_layouts()
        self.display_manager.apply_brightness(self.display_brightness)

        self.current_state = self.STATE_OFF
        self.selected_mode = self.STATE_BAKE
        self.set_temp = self.DEFAULT_SET_TEMP
        self.step_index = 0
        self.current_step = self.STEP_SEQUENCE[self.step_index]
        self.oven_temp = self._read_oven_temp()
        self.last_main_mode = self.STATE_OFF
        self.broil_level = self.BROIL_LEVEL_HIGH

        self.settings_index = 0
        self.settings_options = self._build_settings_options()

        self.last_button_press_time = None
        self.long_press_handled = False
        self.last_display_update = 0.0
        self.last_temp_update = 0.0
        self.last_control_update = 0.0
        self.last_encoder_pos = self.hardware.encoder.position

        self._reset_pid()

        self._set_pixel_for_state(self.current_state)
        self._render_display(force=True)

    def run(self):
        print("Init complete; entering main loop.")
        while True:
            now = time.monotonic()
            self._update_main_mode()
            self._poll_encoder()
            self._poll_button(now)
            self._update_temperature(now)
            self._update_control(now)
            self._update_display(now)
            time.sleep(0.002)

    # ------------------------------------------------------------------
    # Hardware interactions
    # ------------------------------------------------------------------

    def _register_layouts(self):
        def bake_step(context):
            return self.STEP_LABELS.get(context.step, "+-??")

        def mode_label(context):
            return context.mode_label(context.mode_sel)

        def show_temp(context):
            return context.fmt_temp(context.oven_temp)

        def show_set_temp(context):
            return context.fmt_temp(context.set_temp)

        def show_broil_level(context):
            return context.broil_label(context.broil_level)

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
                bl="    ",
                br=show_temp,
            ),
        )

        self.display_manager.register_layout(
            self.STATE_BAKE,
            Layout(
                tl="BAKE",
                tr=show_set_temp,
                bl=bake_step,
                br=show_temp,
            ),
        )

        self.display_manager.register_layout(
            self.STATE_BROIL,
            Layout(
                tl="BROI",
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

    def _render_display(self, force=False):
        (
            setting_label_top,
            setting_label_bottom,
            setting_value,
        ) = self._current_setting_display()

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
        )
        if force:
            # Reset cached display values so that the first render prints to all.
            self.hardware.displays.reset_cache()
        self.display_manager.render(context)

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

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _update_main_mode(self):
        if self.current_state in (self.STATE_OFF, self.STATE_BAKE, self.STATE_BROIL):
            self.last_main_mode = self.current_state

    def _poll_encoder(self):
        position = self.hardware.encoder.position
        delta = position - self.last_encoder_pos
        if delta == 0:
            return
        self.last_encoder_pos = position

        if self.current_state == self.STATE_MODE_SELECT:
            index = self.MODE_LIST.index(self.selected_mode)
            step_dir = 1 if delta > 0 else -1
            index = (index + step_dir) % len(self.MODE_LIST)
            self.selected_mode = self.MODE_LIST[index]
        elif self.current_state == self.STATE_BAKE:
            self.set_temp = self._clamp(
                self.set_temp + (delta * self.current_step),
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
        button.update()

        if button.fell:
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
            if not self.long_press_handled:
                if self.current_state in (self.STATE_BAKE, self.STATE_OFF, self.STATE_BROIL):
                    self.step_index = (self.step_index + 1) % len(self.STEP_SEQUENCE)
                    self.current_step = self.STEP_SEQUENCE[self.step_index]
                elif self.current_state == self.STATE_MODE_SELECT:
                    if self.selected_mode == self.STATE_SETTINGS:
                        self._transition_to(self.STATE_SETTINGS)
                    else:
                        self._transition_to(self.selected_mode)
                elif self.current_state == self.STATE_SETTINGS:
                    self._advance_setting_option()
                    self._render_display()
            self.last_button_press_time = None
            self.long_press_handled = False

    def _update_temperature(self, now):
        if (now - self.last_temp_update) < self.TEMP_UPDATE_RATE:
            return
        self.last_temp_update = now
        self.oven_temp = self._read_oven_temp()
        if self.oven_temp >= (self.BROIL_HIGH_TEMP + 50):
            self._transition_to(self.STATE_ALARM)
            self.hardware.set_elements(False, False)

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
            if self.oven_temp < target:
                self.hardware.set_elements(False, True)
            else:
                self.hardware.set_elements(False, False)
        else:
            self.hardware.set_elements(False, False)

    def _update_display(self, now):
        if (now - self.last_display_update) < self.DISPLAY_UPDATE_RATE:
            return
        self.last_display_update = now
        self._render_display()

    def _transition_to(self, state):
        previous_state = self.current_state
        self.current_state = state
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

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _fmt_temp(self, value):
        try:
            numeric = int(round(value))
        except (TypeError, ValueError):
            return "Err "
        numeric = self._clamp(numeric, 0, 999)
        return "{:03d}F".format(numeric)[-4:]

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

    def _normalize_setting_label(self, text):
        cleaned = str(text or "")
        cleaned = cleaned.replace("\n", " ")
        cleaned = cleaned.upper()
        return cleaned.ljust(8)[:8]

    def _mode_label(self, state):
        if state == self.STATE_OFF:
            return "OFF "
        if state == self.STATE_BAKE:
            return "BAKE"
        if state == self.STATE_BROIL:
            return "BROI"
        if state == self.STATE_SETTINGS:
            return "SEt "
        return "    "

    def _broil_label(self, level):
        return self.BROIL_LEVEL_LABELS.get(level, self.BROIL_LEVEL_LABELS[self.BROIL_LEVEL_HIGH])

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

    def _read_oven_temp(self):
        # Placeholder for real MAX6675 reading.
        return 325.0

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
        ]

    def _on_brightness_changed(self, value):
        self.display_manager.apply_brightness(value)

    def _adjust_current_setting(self, delta):
        if not self.settings_options or delta == 0:
            return
        option = self.settings_options[self.settings_index]
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
        self.settings.set(option["key"], new_value)

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
        if self.pid_last_time is None:
            dt = 0.0
        else:
            dt = now - self.pid_last_time
        if dt < 0:
            dt = 0.0

        if dt > 0:
            self.pid_integral += error * dt
            self.pid_integral = self._clamp(
                self.pid_integral, -self.PID_INTEGRAL_LIMIT, self.PID_INTEGRAL_LIMIT
            )
            derivative = (error - self.pid_last_error) / dt
        else:
            derivative = 0.0

        output = (
            (self.pid_kp * error)
            + (self.pid_ki * self.pid_integral)
            + (self.pid_kd * derivative)
        )
        output = self._clamp(output, self.PID_OUTPUT_MIN, self.PID_OUTPUT_MAX)
        self.pid_last_output = output
        self.pid_last_error = error
        self.pid_last_time = now

        if self.pid_cycle_start is None:
            self.pid_cycle_start = now
            elapsed = 0.0
        else:
            elapsed = now - self.pid_cycle_start
            if elapsed >= self.PID_CYCLE_TIME:
                self.pid_cycle_start = now
                elapsed = 0.0

        self.pid_cycle_on_time = output * self.PID_CYCLE_TIME
        bottom_on = elapsed < self.pid_cycle_on_time
        self.hardware.set_elements(bottom_on, False)

    def _apply_direct_heating(self, on, now):
        self.hardware.set_elements(on, False)
        self._reset_pid()
        self.pid_last_time = now
        self.pid_last_error = self.set_temp - self.oven_temp

    def _reset_pid(self):
        self.pid_integral = 0.0
        self.pid_last_error = 0.0
        self.pid_last_time = None
        self.pid_cycle_start = None
        self.pid_cycle_on_time = 0.0
        self.pid_last_output = 0.0


controller = OvenController()
controller.run()
