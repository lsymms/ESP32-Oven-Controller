"""Persistent settings storage helpers for the oven controller.

The ESP32 CircuitPython builds expose only a very small writable region when the
main ``CIRCUITPY`` filesystem is exported read-only to the host.  In that
environment we fall back to :mod:`microcontroller.nvm` for persistence while
still supporting the JSON file workflow used during desktop development.
"""

import json
import os
import struct

try:  # ``microcontroller`` is only available inside CircuitPython.
    import microcontroller  # type: ignore
except ImportError:  # pragma: no cover - exercised on-device.
    microcontroller = None  # type: ignore


DEFAULT_SETTINGS = {
    "brightness": 0.0,
}

# ``microcontroller.nvm`` offers 512 bytes on ESP32 builds, which is more than
# enough for the handful of settings we currently expose.  A short header keeps
# the layout extensible and lets us detect stale data.
_NVM_HEADER = b"OVN1"
_NVM_BRIGHTNESS_OFFSET = len(_NVM_HEADER)
_NVM_REQUIRED_BYTES = _NVM_BRIGHTNESS_OFFSET + struct.calcsize("<f")


def _load_from_nvm():
    if microcontroller is None:
        return {}

    nvm = microcontroller.nvm
    if len(nvm) < _NVM_REQUIRED_BYTES:
        return {}

    if bytes(nvm[: len(_NVM_HEADER)]) != _NVM_HEADER:
        return {}

    try:
        brightness = struct.unpack_from("<f", nvm, _NVM_BRIGHTNESS_OFFSET)[0]
    except (ValueError, struct.error):  # pragma: no cover - corrupt data.
        return {}

    return {"brightness": brightness}


def _save_to_nvm(payload):
    if microcontroller is None:
        return False

    nvm = microcontroller.nvm
    if len(nvm) < _NVM_REQUIRED_BYTES:
        return False

    try:
        brightness = float(payload.get("brightness", DEFAULT_SETTINGS["brightness"]))
    except (TypeError, ValueError):
        brightness = DEFAULT_SETTINGS["brightness"]

    buffer = bytearray(_NVM_REQUIRED_BYTES)
    buffer[: len(_NVM_HEADER)] = _NVM_HEADER
    struct.pack_into("<f", buffer, _NVM_BRIGHTNESS_OFFSET, brightness)

    try:
        nvm[:_NVM_REQUIRED_BYTES] = buffer
    except (TypeError, AttributeError):  # pragma: no cover - unsupported slice.
        return False

    return True


def load_settings(path):
    """Load settings from *path*, falling back to defaults or NVM."""

    data = {}
    try:
        with open(path, "r") as file_handle:
            data = json.load(file_handle)
    except (OSError, ValueError):
        # File missing/corrupt when running from the read-only CIRCUITPY drive.
        data = _load_from_nvm()

    settings = DEFAULT_SETTINGS.copy()
    for key in settings:
        if key in data:
            settings[key] = data[key]
    return settings


def save_settings(settings, path):
    """Persist *settings* dictionary to *path*, falling back to NVM."""

    payload = {key: settings.get(key, DEFAULT_SETTINGS.get(key)) for key in DEFAULT_SETTINGS}

    temp_path = path + ".tmp"
    try:
        with open(temp_path, "w") as file_handle:
            json.dump(payload, file_handle)
            file_handle.flush()
        os.rename(temp_path, path)
        return
    except OSError as error:
        # Best-effort cleanup; ignore failures so we can surface the original
        # exception if the NVM fallback also fails.
        try:
            os.remove(temp_path)
        except OSError:
            pass

        if not _save_to_nvm(payload):
            raise error
