# code.py — Oven controller skeleton
# - Always show something on all 4 displays
# - BAKE: encoder edits set temp, BL shows step (+-25/+-5/+-1)
# - OFF & BROIL: set temp not edited or shown (irrelevant there)
# - Long press: enter MODE_SELECT while holding
# - MODE_SELECT:
#       - If starting from OFF  -> initial selection = BAKE
#       - If starting from BAKE/BROIL -> initial selection = OFF
#       - Short press confirms; selecting SETTINGS remembers previous main mode
# - SETTINGS:
#       - Long press exits back to previous main mode (OFF/BAKE/BROIL)
# - Up to 4x HT16K33 14-seg @ 0x70..0x73
# - I2C: SDA=GPIO47, SCL=GPIO39
# - Onboard NeoPixel for state
# - K-type via MAX6675 stubbed

import time
import errno
import board
import busio
import digitalio
import rotaryio
import neopixel

from adafruit_debouncer import Debouncer
from adafruit_ht16k33.segments import Seg14x4

from settings_store import DEFAULT_SETTINGS, load_settings, save_settings

print("Oven controller starting...")

# ----------------------------
# Pin / hardware config
# ----------------------------

# I2C
I2C_SDA = board.GPIO47
I2C_SCL = board.GPIO39

# Rotary encoder + button
ENCODER_PIN_A = board.GPIO6
ENCODER_PIN_B = board.GPIO5
BUTTON_PIN = board.GPIO4  # active low with pull-up

# Relays
BOTTOM_RELAY_PIN = board.GPIO10  # bottom element
TOP_RELAY_PIN = board.GPIO11     # top element

# Onboard NeoPixel
NEOPIXEL_PIN = board.NEOPIXEL
NEOPIXEL_BRIGHTNESS = 0.05  # 0.0–1.0

# Displays
ADDR_TL = 0x70  # top-left
ADDR_TR = 0x71  # top-right
ADDR_BL = 0x72  # bottom-left
ADDR_BR = 0x73  # bottom-right

# ----------------------------
# Behavior config
# ----------------------------

MIN_SET_TEMP = 90
MAX_SET_TEMP = 550
DEFAULT_SET_TEMP = 350

STEP_SEQUENCE = (25, 5, 1)
STEP_LABELS = {
    25: "+-25",
    5:  "+-5 ",
    1:  "+-1 ",
}

DISPLAY_UPDATE_RATE = 1 / 15.0   # ~15 Hz
TEMP_UPDATE_RATE = 0.5           # seconds
CONTROL_UPDATE_RATE = 1.0        # seconds
LONG_PRESS_TIME = 0.8            # seconds

TEMP_BAND = 2.0                  # ±°F band for BAKE hold
BROIL_MAX_TEMP = 550.0           # broil cap

# ----------------------------
# Settings persistence
# ----------------------------

SETTINGS_FILE = "oven_settings.json"
BRIGHTNESS_MIN = 0.0
BRIGHTNESS_MAX = 1.0
BRIGHTNESS_STEP = 0.05


def clamp_brightness(value):
    try:
        brightness_value = float(value)
    except (TypeError, ValueError):
        return BRIGHTNESS_MIN
    if brightness_value < BRIGHTNESS_MIN:
        brightness_value = BRIGHTNESS_MIN
    if brightness_value > BRIGHTNESS_MAX:
        brightness_value = BRIGHTNESS_MAX
    return brightness_value


settings_data = load_settings(SETTINGS_FILE)
display_brightness = clamp_brightness(
    settings_data.get("brightness", DEFAULT_SETTINGS["brightness"])
)
settings_dirty = False

if display_brightness != settings_data.get("brightness"):
    settings_data["brightness"] = display_brightness
    settings_dirty = True


def save_settings_if_dirty():
    global settings_dirty
    if not settings_dirty:
        return
    try:
        save_settings(settings_data, SETTINGS_FILE)
        settings_dirty = False
        print("Settings saved.")
    except OSError as error:
        # A read-only filesystem is a common scenario when running the
        # firmware from a development host. In that case, retrying will
        # never succeed, so clear the dirty flag to avoid spamming the log.
        if error.errno in (errno.EROFS, errno.EPERM, errno.EACCES):
            print("Failed to save settings (read-only filesystem):", error)
            settings_dirty = False
        else:
            print("Failed to save settings:", error)

# ----------------------------
# States
# ----------------------------

STATE_OFF = 0
STATE_BAKE = 1
STATE_BROIL = 2
STATE_SETTINGS = 3
STATE_MODE_SELECT = 4
STATE_ALARM = 5

MODE_LIST = (STATE_OFF, STATE_BAKE, STATE_BROIL, STATE_SETTINGS)

# ----------------------------
# Hardware init
# ----------------------------

i2c = busio.I2C(scl=I2C_SCL, sda=I2C_SDA, frequency=100000)

def init_display(address, label):
    try:
        d = Seg14x4(i2c, address=address)
        d.brightness = display_brightness  # dim but on (0.0–1.0)
        d.fill(0)
        print("Display", label, "found at", hex(address))
        return d
    except Exception as e:
        print("Display", label, "NOT found at", hex(address), "->", e)
        return None


def apply_display_brightness(value):
    for disp in (disp_tl, disp_tr, disp_bl, disp_br):
        if disp is not None:
            try: 
                disp.brightness = value
            except OSError as error:
                print("Display brightness set error:", error)



def _print4(disp, text):
    if disp is None:
        return
    s = (str(text) + "    ")[:4]
    try:
        disp.print(s)
        print("Updated display to " + s)
    except OSError as e:
        # Don't crash on I2C hiccups.
        print("Display I2C error:", e)
        print("Attempting to reinitialize displays")
        disp_tl = init_display(ADDR_TL, "TL")
        disp_tr = init_display(ADDR_TR, "TR")
        disp_bl = init_display(ADDR_BL, "BL")
        disp_br = init_display(ADDR_BR, "BR")
        
def set_all_displays(tl, tr, bl, br):
    global last_tl, last_tr, last_bl, last_br

    if tl != last_tl:
        print("Updating TopLeft display to " + tl)
        _print4(disp_tl, tl)
        last_tl = tl

    if tr != last_tr:
        print("Updating TopRight display to " + tr)
        _print4(disp_tr, tr)
        last_tr = tr

    if bl != last_bl:
        print("Updating BotLeft display to " + bl)
        _print4(disp_bl, bl)
        last_bl = bl

    if br != last_br:
        print("Updating BotRight display to " + br)
        _print4(disp_br, br)
        last_br = br
        
disp_tl = init_display(ADDR_TL, "TL")
disp_tr = init_display(ADDR_TR, "TR")
disp_bl = init_display(ADDR_BL, "BL")
disp_br = init_display(ADDR_BR, "BR")
last_tl = "...."
last_tr = "...."
last_bl = "...."
last_br = "...."

apply_display_brightness(display_brightness)
save_settings_if_dirty()


# Encoder
try:
    enc = rotaryio.IncrementalEncoder(ENCODER_PIN_A, ENCODER_PIN_B, divisor=4)
    print("IncrementalEncoder initialized with divisor=4")
except TypeError:
    enc = rotaryio.IncrementalEncoder(ENCODER_PIN_A, ENCODER_PIN_B)
    print("IncrementalEncoder initialized without divisor")
last_encoder_pos = enc.position

# Button
button_io = digitalio.DigitalInOut(BUTTON_PIN)
button_io.switch_to_input(pull=digitalio.Pull.UP)
button = Debouncer(button_io)

# Relays
bottom_relay = digitalio.DigitalInOut(BOTTOM_RELAY_PIN)
bottom_relay.switch_to_output(value=False)

top_relay = digitalio.DigitalInOut(TOP_RELAY_PIN)
top_relay.switch_to_output(value=False)

# NeoPixel
pixel = neopixel.NeoPixel(
    NEOPIXEL_PIN,
    1,
    brightness=NEOPIXEL_BRIGHTNESS,
    auto_write=True,
)

# ----------------------------
# Temp stub (MAX6675 placeholder)
# ----------------------------

def read_oven_temp():
    # Replace with real MAX6675 reading when wired
    return 325.0

# ----------------------------
# Display helpers
# ----------------------------

def _fmt_temp(val):
    try:
        v = int(round(val))
    except (TypeError, ValueError):
        return "Err "
    if v < 0:
        v = 0
    if v > 999:
        v = 999
    return "{:03d}F".format(v)[-4:]


def _fmt_brightness(val):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "Err "
    if v < BRIGHTNESS_MIN:
        v = BRIGHTNESS_MIN
    if v > BRIGHTNESS_MAX:
        v = BRIGHTNESS_MAX
    percent = int(round(v * 100))
    if percent < 0:
        percent = 0
    if percent > 100:
        percent = 100
    return f"{percent:>3d}%"

def _mode_label(state):
    if state == STATE_OFF:
        return "OFF "
    if state == STATE_BAKE:
        return "BAKE"
    if state == STATE_BROIL:
        return "BRoL"
    if state == STATE_SETTINGS:
        return "SEt "
    return "    "



def show_layout(state, set_temp, oven_temp, step, mode_sel=None, brightness=None):
    """
    Map logical state -> 4 displays.
    - Always show something on each display.
    - BR: oven temp (or Err in ALARM).
    - BAKE: TR = set temp.
    - OFF/BROIL: TR does NOT show BAKE set temp.
    - BL: persistent step indicator in main modes; mode name in MODE_SELECT; 'ALRM' in ALARM.
    """

    # TL: mode/context
    if state == STATE_OFF:
        tl = "OFF "
    elif state == STATE_BAKE:
        tl = "BAKE"
    elif state == STATE_BROIL:
        tl = "BRoL"
    elif state == STATE_SETTINGS:
        tl = "SEt "
    elif state == STATE_MODE_SELECT:
        tl = "MODE"
    elif state == STATE_ALARM:
        tl = "ALRM"
    else:
        tl = "    "

    # TR: depends on state
    if state == STATE_BAKE:
        tr = _fmt_temp(set_temp)
    elif state == STATE_BROIL:
        tr = "MAX "  # broil cap indicator
    elif state == STATE_MODE_SELECT:
        tr = _mode_label(mode_sel)
    elif state == STATE_SETTINGS:
        tr = _fmt_brightness(brightness)
    elif state == STATE_ALARM:
        tr = "Err "
    else:
        tr = "    "

    # BL: step / context / alarm
    if state == STATE_BAKE:
        bl = STEP_LABELS.get(step, "+-??")
    elif state == STATE_SETTINGS:
        bl = "brt "
    elif state == STATE_ALARM:
        bl = "ALRM"
    else:
        bl = "    "

    # BR: oven temp pinned (or Err in ALARM)
    if state == STATE_ALARM:
        br = "Err "
    else:
        br = _fmt_temp(oven_temp)

    set_all_displays(tl, tr, bl, br)

# ----------------------------
# NeoPixel color per state
# ----------------------------

def set_pixel_for_state(state):
    if state == STATE_OFF:
        pixel[0] = (0, 0, 0)
    elif state == STATE_BAKE:
        pixel[0] = (0, 40, 0)
    elif state == STATE_BROIL:
        pixel[0] = (40, 0, 0)
    elif state == STATE_SETTINGS:
        pixel[0] = (40, 40, 0)
    elif state == STATE_MODE_SELECT:
        pixel[0] = (0, 0, 40)
    elif state == STATE_ALARM:
        pixel[0] = (40, 0, 40)
    else:
        pixel[0] = (5, 5, 5)

# ----------------------------
# Relays
# ----------------------------

def set_elements(bottom_on, top_on):
    bottom_relay.value = bool(bottom_on)
    top_relay.value = bool(top_on)

# ----------------------------
# Main state
# ----------------------------

current_state = STATE_OFF
selected_mode = STATE_BAKE  # for MODE_SELECT

set_temp = DEFAULT_SET_TEMP       # BAKE set temp
step_index = 0
current_step = STEP_SEQUENCE[step_index]

oven_temp = read_oven_temp()

last_button_press_time = None
long_press_handled = False

last_display_update = 0.0
last_temp_update = 0.0
last_control_update = 0.0

# Track most recent "main" mode (OFF/BAKE/BROIL) for Settings return behavior
last_main_mode = STATE_OFF

set_pixel_for_state(current_state)
show_layout(
    current_state,
    set_temp,
    oven_temp,
    current_step,
    mode_sel=selected_mode,
    brightness=display_brightness,
)

print("Init complete; entering main loop.")

# ----------------------------
# Main loop
# ----------------------------

while True:
    now = time.monotonic()

    # Keep last_main_mode in sync whenever we're in a main mode
    if current_state in (STATE_OFF, STATE_BAKE, STATE_BROIL):
        last_main_mode = current_state

    # --- 1) Encoder + button ---
    button.update()
    pos = enc.position
    delta = pos - last_encoder_pos

    if delta != 0:
        last_encoder_pos = pos

        if current_state == STATE_MODE_SELECT:
            # Scroll through modes
            idx = MODE_LIST.index(selected_mode)
            step_dir = 1 if delta > 0 else -1
            idx = (idx + step_dir) % len(MODE_LIST)
            selected_mode = MODE_LIST[idx]

        elif current_state == STATE_BAKE:
            # Only BAKE uses set_temp
            new_temp = set_temp + (delta * current_step)
            if new_temp < MIN_SET_TEMP:
                new_temp = MIN_SET_TEMP
            if new_temp > MAX_SET_TEMP:
                new_temp = MAX_SET_TEMP
            set_temp = new_temp

        elif current_state == STATE_SETTINGS:
            new_brightness = clamp_brightness(
                display_brightness + (delta * BRIGHTNESS_STEP)
            )
            if new_brightness != display_brightness:
                display_brightness = new_brightness
                settings_data["brightness"] = display_brightness
                settings_dirty = True
                apply_display_brightness(display_brightness)

        # OFF/BROIL/ALARM: encoder does NOT change BAKE set_temp here

    # Button pressed
    if button.fell:
        last_button_press_time = now
        long_press_handled = False

    # Long-press detection WHILE holding
    if (
        last_button_press_time is not None
        and not long_press_handled
        and not button.value  # still held (active low)
        and (now - last_button_press_time) >= LONG_PRESS_TIME
    ):
        # SETTINGS: long press exits to previous main mode
        if current_state == STATE_SETTINGS:
            save_settings_if_dirty()
            current_state = last_main_mode
            set_pixel_for_state(current_state)

        # Not already in MODE_SELECT: enter MODE_SELECT with special rules
        elif current_state != STATE_MODE_SELECT:
            if current_state == STATE_OFF:
                # From OFF, start with BAKE
                selected_mode = STATE_BAKE
            elif current_state in (STATE_BAKE, STATE_BROIL):
                # From BAKE or BROIL, start with OFF
                selected_mode = STATE_OFF
            else:
                # Fallback: start from current
                selected_mode = current_state

            current_state = STATE_MODE_SELECT
            set_pixel_for_state(current_state)

        # If already MODE_SELECT and long-held, ignore (no nested behavior)
        long_press_handled = True

    # Button released
    if button.rose and last_button_press_time is not None:
        # If long press was already handled, do nothing on release
        if not long_press_handled:
            # Short press behavior
            if current_state in (STATE_BAKE, STATE_OFF, STATE_BROIL):
                # Cycle step size; shown persistently in BL
                step_index = (step_index + 1) % len(STEP_SEQUENCE)
                current_step = STEP_SEQUENCE[step_index]

            elif current_state == STATE_MODE_SELECT:
                # Confirm selected mode
                if selected_mode == STATE_SETTINGS:
                    # Enter settings; remember last main mode (already tracked)
                    current_state = STATE_SETTINGS
                else:
                    # Switch to chosen mode
                    current_state = selected_mode
                set_pixel_for_state(current_state)

            elif current_state == STATE_SETTINGS:
                save_settings_if_dirty()

        # Reset press tracking
        last_button_press_time = None
        long_press_handled = False

    # --- 2) Temperature sampling (stub) ---
    if (now - last_temp_update) >= TEMP_UPDATE_RATE:
        last_temp_update = now
        oven_temp = read_oven_temp()
        if oven_temp >= (BROIL_MAX_TEMP + 50):
            current_state = STATE_ALARM
            set_elements(False, False)
            set_pixel_for_state(current_state)

    # --- 3) Control loop (simple placeholder) ---
    if (now - last_control_update) >= CONTROL_UPDATE_RATE:
        last_control_update = now

        if current_state == STATE_BAKE:
            if oven_temp < (set_temp - TEMP_BAND):
                set_elements(True, True)
            elif oven_temp > (set_temp + TEMP_BAND):
                set_elements(False, False)
            # In band: keep previous state for now

        elif current_state == STATE_BROIL:
            if oven_temp < BROIL_MAX_TEMP:
                set_elements(False, True)  # top only
            else:
                set_elements(False, False)

        elif current_state in (STATE_OFF, STATE_SETTINGS, STATE_MODE_SELECT, STATE_ALARM):
            set_elements(False, False)

    # --- 4) Display update ---
    if (now - last_display_update) >= DISPLAY_UPDATE_RATE:
        last_display_update = now
        show_layout(
            state=current_state,
            set_temp=set_temp,
            oven_temp=oven_temp,
            step=current_step,
            mode_sel=selected_mode,
            brightness=display_brightness,
        )

    time.sleep(0.002)
