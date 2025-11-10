"""Persistent settings storage helpers for the oven controller."""

import json
import os

DEFAULT_SETTINGS = {
    "brightness": 0.0,
}


def load_settings(path):
    """Load settings from *path*, falling back to defaults on failure."""
    data = {}
    try:
        with open(path, "r") as file_handle:
            data = json.load(file_handle)
    except OSError:
        # File missing (first boot) or not readable; will create on save.
        data = {}
    except ValueError:
        # Corrupt JSON; ignore and fall back to defaults.
        data = {}

    settings = DEFAULT_SETTINGS.copy()
    for key in settings:
        if key in data:
            settings[key] = data[key]
    return settings


def save_settings(settings, path):
    """Persist *settings* dictionary to *path* atomically where possible."""
    # Only store keys we know about to keep the file tidy.
    payload = {key: settings.get(key, DEFAULT_SETTINGS.get(key)) for key in DEFAULT_SETTINGS}

    temp_path = path + ".tmp"
    with open(temp_path, "w") as file_handle:
        json.dump(payload, file_handle)
        file_handle.flush()

    try:
        os.rename(temp_path, path)
    except OSError as error:
        # Best-effort cleanup on failure; leave temp file if removal fails.
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise error
