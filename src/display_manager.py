"""Declarative display layout helpers."""

from collections import namedtuple

from hardware import DisplayBundle


Layout = namedtuple("Layout", "tl tr bl br")


class DisplayContext:
    """Container for values used to render the four displays."""

    def __init__(
        self,
        *,
        state,
        set_temp,
        oven_temp,
        step,
        mode_sel,
        brightness,
        fmt_temp,
        fmt_brightness,
        mode_label,
        broil_level,
        broil_label,
        setting_label_top,
        setting_label_bottom,
        setting_value,
    ):
        self.state = state
        self.set_temp = set_temp
        self.oven_temp = oven_temp
        self.step = step
        self.mode_sel = mode_sel
        self.brightness = brightness
        self.fmt_temp = fmt_temp
        self.fmt_brightness = fmt_brightness
        self.mode_label = mode_label
        self.broil_level = broil_level
        self.broil_label = broil_label
        self.setting_label_top = setting_label_top
        self.setting_label_bottom = setting_label_bottom
        self.setting_value = setting_value


class DisplayManager:
    """Render layouts to the physical displays."""

    def __init__(self, displays):
        if not isinstance(displays, DisplayBundle):
            raise TypeError("DisplayManager expects a DisplayBundle")
        self._displays = displays
        self._layouts = {}

    def register_layout(self, state, layout):
        self._layouts[state] = layout

    def render(self, context):
        layout = self._layouts.get(context.state)
        if layout is None:
            return
        texts = {
            "tl": self._resolve(layout.tl, context),
            "tr": self._resolve(layout.tr, context),
            "bl": self._resolve(layout.bl, context),
            "br": self._resolve(layout.br, context),
        }
        self._displays.show_texts(texts)

    def _resolve(self, value, context):
        if callable(value):
            return value(context)
        return value

    def apply_brightness(self, value):
        self._displays.apply_brightness(value)
