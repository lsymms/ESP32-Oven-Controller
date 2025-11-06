# code.py — CircuitPython equivalent for:
# - ESP32-S3 dev board
# - HT16K33 14-seg 4-char display (0x70)
# - Rotary encoder + push button
#
# Required libraries in /lib:
#   adafruit_ht16k33
#   adafruit_debouncer
#
# Pin map (from the Arduino sketch):
#   I2C:  SDA=IO47, SCL=IO48
#   Encoder A=IO6, B=IO5
#   Button  =IO4  (active low, uses pull-up)

import time
import board
import busio
import digitalio
import rotaryio

from adafruit_debouncer import Debouncer
from adafruit_ht16k33.segments import Seg14x4

# ----------------------------
# Config
# ----------------------------
I2C_SDA = board.GPIO47
I2C_SCL = board.GPIO48

ENCODER_PIN_A = board.GPIO6
ENCODER_PIN_B = board.GPIO5
BUTTON_PIN    = board.GPIO4

I2C_ADDR = 0x70
BRIGHTNESS = 0  # 0..15

TICKS_PER_DETENT = 2
MIN_VALUE = -10
MAX_VALUE = 50

MSG_BLOCK_MS = 0.50  # seconds

# ----------------------------
# Hardware init
# ----------------------------
i2c = busio.I2C(scl=I2C_SCL, sda=I2C_SDA, frequency=100000)
display = Seg14x4(i2c, address=I2C_ADDR)
display.brightness = BRIGHTNESS
display.fill(0)

enc = rotaryio.IncrementalEncoder(ENCODER_PIN_A, ENCODER_PIN_B, divisor=2)  # or 4 on many encoders

last_encoder_pos = enc.position

button_pin = digitalio.DigitalInOut(BUTTON_PIN)
button_pin.switch_to_input(pull=digitalio.Pull.UP)
button = Debouncer(button_pin)

# ----------------------------
# State
# ----------------------------
boundary_event = 0   # 1: max hit, -1: min hit, 0: none
toggle_display_until = 0.0  # monotonic timestamp until which we show a message

# ----------------------------
# Helpers
# ----------------------------
def clear_display():
    display.fill(0)

def write_fixed(text):
    # left-justify to 4 chars without str.ljust
    s = (str(text) + "    ")[:4]
    display.print(s)

def marquee(text, delay_s=0.10):
    pad = "    " + str(text) + "    "
    for i in range(len(pad) - 3):
        display.print(pad[i:i+4])
        time.sleep(delay_s)

def current_display_value():
    # Convert encoder ticks to detents and clamp
    raw = enc.position
    val = raw // TICKS_PER_DETENT
    if val > MAX_VALUE:
        return MAX_VALUE
    if val < MIN_VALUE:
        return MIN_VALUE
    return val

def clamp_encoder_to_bounds():
    global boundary_event
    # Clamp raw ticks so displayed detents stay within MIN..MAX
    # Compute bounds in ticks space:
    min_ticks = MIN_VALUE * TICKS_PER_DETENT
    max_ticks = MAX_VALUE * TICKS_PER_DETENT
    if enc.position > max_ticks:
        enc.position = max_ticks
        boundary_event = 1
    elif enc.position < min_ticks: 
        enc.position = min_ticks
        boundary_event = -1

def block_display(msg: str, seconds: float):
    global toggle_display_until
    now = time.monotonic()
    if now > toggle_display_until:
        toggle_display_until = now + seconds
        write_fixed(msg)

# ----------------------------
# Main loop
# ----------------------------
# Optional hello
write_fixed("INIT") 
time.sleep(0.3)

while True:
    # Debounce button
    button.update()

    # Read encoder movement & clamp to bounds
    pos = enc.position
    if pos != last_encoder_pos:
        last_encoder_pos = pos
        clamp_encoder_to_bounds()

    # Button pressed => show "togl" for a moment
    if button.fell:  # active-low -> fell = pressed
        block_display("TOGL", MSG_BLOCK_MS)

    # Boundary messages
    if boundary_event != 0:
        if boundary_event == 1:
            block_display("MAX ", MSG_BLOCK_MS)
        elif boundary_event == -1:
            block_display("MIN  ", MSG_BLOCK_MS)
        boundary_event = 0

    # Normal display when not blocked
    if time.monotonic() >= toggle_display_until:
        val = current_display_value()
        write_fixed(str(val))

    time.sleep(0.01)
