"""Hardware initialization helpers for the oven controller."""

import time

import board
import busio
import digitalio
import neopixel
import rotaryio

import adafruit_mcp9600

from adafruit_debouncer import Debouncer
from adafruit_ht16k33.segments import Seg14x4


class RelayPair:
    """Simple helper to toggle the top/bottom relays together."""

    def __init__(self, bottom_pin, top_pin):
        self._bottom = digitalio.DigitalInOut(bottom_pin)
        self._top = digitalio.DigitalInOut(top_pin)
        self._bottom.switch_to_output(value=False)
        self._top.switch_to_output(value=False)

    def set(self, bottom_on, top_on):
        self._bottom.value = bool(bottom_on)
        self._top.value = bool(top_on)


class DisplayBundle:
    """Manage a group of HT16K33 displays on the same I²C bus."""

    def __init__(self, i2c, addresses, brightness):
        self._i2c = i2c
        self._addresses = addresses
        self._displays = {}
        self._last_values = {key: "" for key in addresses}
        self._brightness = brightness
        self._init_all()

    def _init_display(self, key, address):
        label = key.upper()
        try:
            display = Seg14x4(self._i2c, address=address)
            display.brightness = self._brightness
            display.fill(0)
            self._displays[key] = display
            print("Display", label, "found at", hex(address))
        except Exception as error:  # noqa: BLE001 - hardware errors are opaque
            self._displays[key] = None
            print("Display", label, "NOT found at", hex(address), "->", error)

    def _init_all(self):
        for key, address in self._addresses.items():
            self._init_display(key, address)

    def apply_brightness(self, value):
        self._brightness = value
        for display in self._displays.values():
            if display is None:
                continue
            try:
                display.brightness = value
            except OSError as error:
                print("Display brightness set error:", error)

    def show_texts(self, mapping):
        for key, text in mapping.items():
            formatted = self._format_text(text)
            previous = self._last_values.get(key)
            if previous == formatted:
                continue
            self._print_text(key, formatted)
            self._last_values[key] = formatted

    def reset_cache(self):
        self._last_values = {key: "" for key in self._addresses}

    def _format_text(self, text):
        raw = "" if text is None else str(text)
        buffer = []
        glyph_count = 0

        for char in raw:
            if char == ".":
                if not buffer:
                    # Skip leading decimals that have no glyph to attach to.
                    continue
                buffer.append(char)
                continue

            if glyph_count >= 4:
                break

            buffer.append(char)
            glyph_count += 1

        while glyph_count < 4:
            buffer.append(" ")
            glyph_count += 1

        return "".join(buffer)

    def _print_text(self, key, text):
        display = self._displays.get(key)
        if display is None:
            return
        try:
            display.print(text)
            print("Updated", key.upper(), "display to", text)
        except OSError as error:
            print("Display I2C error on", key.upper(), ":", error)
            # Attempt to reinitialise just this display so that future
            # updates have a chance to recover.
            address = self._addresses[key]
            self._init_display(key, address)


class Hardware:
    """Aggregate of all hardware peripherals used by the controller."""

    def __init__(self, *,
                 encoder_a,
                 encoder_b,
                 button_pin,
                 bottom_relay_pin,
                 top_relay_pin,
                 neopixel_pin,
                 neopixel_brightness,
                 display_i2c_scl,
                 display_i2c_sda,
                 stemma_i2c_scl,
                 stemma_i2c_sda,
                 display_addresses,
                 display_brightness,
                 thermocouple_address=0x67):
        self.display_i2c = busio.I2C(
            scl=display_i2c_scl, sda=display_i2c_sda, frequency=100000
        )
        self._log_i2c_scan(self.display_i2c, label="display bus")
        address_map = {
            "tl": display_addresses["tl"],
            "tr": display_addresses["tr"],
            "bl": display_addresses["bl"],
            "br": display_addresses["br"],
        }
        self.displays = DisplayBundle(self.display_i2c, address_map, display_brightness)
        self.thermocouple = None
        self.thermocouple_address = thermocouple_address

        try:
            self.encoder = rotaryio.IncrementalEncoder(encoder_a, encoder_b, divisor=4)
            print("IncrementalEncoder initialized with divisor=4")
        except TypeError:
            self.encoder = rotaryio.IncrementalEncoder(encoder_a, encoder_b)
            print("IncrementalEncoder initialized without divisor")

        button_io = digitalio.DigitalInOut(button_pin)
        button_io.switch_to_input(pull=digitalio.Pull.UP)
        self.button = Debouncer(button_io)

        self.relays = RelayPair(bottom_relay_pin, top_relay_pin)

        self.pixel = neopixel.NeoPixel(
            neopixel_pin,
            1,
            brightness=neopixel_brightness,
            auto_write=True,
        )
        self.stemma_i2c = busio.I2C(
            scl=stemma_i2c_scl, sda=stemma_i2c_sda, frequency=100000
        )
        self._log_i2c_scan(self.stemma_i2c, label="stemma bus")


    def _log_i2c_scan(self, bus, *, label):
        try:
            while not bus.try_lock():
                pass
            devices = bus.scan()
        except Exception as error:  # noqa: BLE001 - bus issues vary
            print(f"I2C scan failed on {label}:", error)
            devices = None
        finally:
            try:
                bus.unlock()
            except Exception:
                pass
        if devices:
            formatted = ", ".join(hex(address) for address in devices)
            print(f"I2C devices detected on {label}: {formatted}")
        else:
            print(f"I2C scan found no devices on {label}.")

    def set_elements(self, bottom_on, top_on):
        self.relays.set(bottom_on, top_on)

    def read_thermocouple(self):
        if self.thermocouple is None:
            self._init_thermocouple() 
        if self.thermocouple == None:
            return "ERR"
        return self.thermocouple.temperature

    def _init_thermocouple(self):
        try:
            self.thermocouple = adafruit_mcp9600.MCP9600(self.stemma_i2c,self.thermocouple_address)
            print("MCP9600 thermocouple initialized at", hex(self.thermocouple_address))
            return
        except Exception as error:  # noqa: BLE001 - hardware init failures vary
            print(
                "Thermocouple init failed:",
                error,
            )
        


def create_hardware(display_brightness):
    """Factory helper that returns a :class:`Hardware` instance."""
    display_addresses = {
        "tl": 0x70,
        "tr": 0x71,
        "bl": 0x72,
        "br": 0x73,
    }
    return Hardware(
        encoder_a=board.GPIO6,
        encoder_b=board.GPIO5,
        button_pin=board.GPIO4,
        bottom_relay_pin=board.GPIO10,
        top_relay_pin=board.GPIO11,
        neopixel_pin=board.NEOPIXEL,
        neopixel_brightness=0.05,
        display_i2c_scl=board.GPIO39,
        display_i2c_sda=board.GPIO47,
        stemma_i2c_scl=board.GPIO9,
        stemma_i2c_sda=board.GPIO8,
        display_addresses=display_addresses,
        display_brightness=display_brightness,
        thermocouple_address=0x67,
    )
