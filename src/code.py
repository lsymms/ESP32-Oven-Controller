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

    TEMP_BAND = 2.0
    BROIL_MAX_TEMP = 550.0

    BRIGHTNESS_MIN = 0.0
    BRIGHTNESS_MAX = 1.0
    BRIGHTNESS_STEP = 0.05

    def __init__(self):
        self.settings = SettingsStore(self.SETTINGS_FILE)
        self.display_brightness = self._clamp_brightness(
            self.settings.get("brightness")
        )
        if self.settings.set("brightness", self.display_brightness):
            # Persist clamped brightness immediately so that the stored value
            # always reflects the applied brightness level.
            self.settings.save_if_dirty()

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

        self.last_button_press_time = None
        self.long_press_handled = False
        self.last_display_update = 0.0
        self.last_temp_update = 0.0
        self.last_control_update = 0.0
        self.last_encoder_pos = self.hardware.encoder.position

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

        def show_brightness(context):
            return context.fmt_brightness(context.brightness)

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
                tl="BRoL",
                tr="MAX ",
                bl="    ",
                br=show_temp,
            ),
        )

        self.display_manager.register_layout(
            self.STATE_SETTINGS,
            Layout(
                tl="SEt ",
                tr=show_brightness,
                bl="brt ",
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
        elif self.current_state == self.STATE_SETTINGS:
            new_brightness = self._clamp_brightness(
                self.display_brightness + (delta * self.BRIGHTNESS_STEP)
            )
            if new_brightness != self.display_brightness:
                self.display_brightness = new_brightness
                self.settings.set("brightness", self.display_brightness)
                self.display_manager.apply_brightness(self.display_brightness)

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
                    self._save_settings()
            self.last_button_press_time = None
            self.long_press_handled = False

    def _update_temperature(self, now):
        if (now - self.last_temp_update) < self.TEMP_UPDATE_RATE:
            return
        self.last_temp_update = now
        self.oven_temp = self._read_oven_temp()
        if self.oven_temp >= (self.BROIL_MAX_TEMP + 50):
            self._transition_to(self.STATE_ALARM)
            self.hardware.set_elements(False, False)

    def _update_control(self, now):
        if (now - self.last_control_update) < self.CONTROL_UPDATE_RATE:
            return
        self.last_control_update = now

        if self.current_state == self.STATE_BAKE:
            if self.oven_temp < (self.set_temp - self.TEMP_BAND):
                self.hardware.set_elements(True, True)
            elif self.oven_temp > (self.set_temp + self.TEMP_BAND):
                self.hardware.set_elements(False, False)
        elif self.current_state == self.STATE_BROIL:
            if self.oven_temp < self.BROIL_MAX_TEMP:
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
        self.current_state = state
        self._set_pixel_for_state(state)
        if state == self.STATE_SETTINGS:
            # Ensure brightness is immediately applied when entering settings.
            self.display_manager.apply_brightness(self.display_brightness)
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

    def _mode_label(self, state):
        if state == self.STATE_OFF:
            return "OFF "
        if state == self.STATE_BAKE:
            return "BAKE"
        if state == self.STATE_BROIL:
            return "BRoL"
        if state == self.STATE_SETTINGS:
            return "SEt "
        return "    "

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


controller = OvenController()
controller.run()
