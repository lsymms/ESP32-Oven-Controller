"""Persistent settings storage helpers for the oven controller."""

import errno
import json
import os

from .logger import logger

DEFAULT_SETTINGS = {
    "brightness": 0.0,
    "pid_kp": 0.05,
    "pid_ki": 0.001,
    "pid_kd": 0.1,
    "pid_window_delta": 15.0,
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
    logger.info(
        "attempting to save settings to temp file",
        temp_path,
        "and replace setting file",
        path,
    )
    with open(temp_path, "w") as file_handle:
        json.dump(payload, file_handle)
        file_handle.flush()
        logger.info("finished writing settings to temp", temp_path)

    try:
        os.rename(temp_path, path)
        logger.info("finished replacing settings file", path)
    except OSError as error:
        logger.error("failed to rename", temp_path, "to", path)
        # Best-effort cleanup on failure; leave temp file if removal fails.
        try:
            os.remove(temp_path)
            logger.info("cleaned settings temp file", temp_path)
        except OSError:
            pass
        raise error


class SettingsStore:
    """Small helper to manage oven settings with dirty tracking."""

    def __init__(self, path):
        self._path = path
        self._data = load_settings(path)
        self._dirty = False

    def get(self, key):
        return self._data.get(key, DEFAULT_SETTINGS.get(key))

    def set(self, key, value):
        if self._data.get(key) == value:
            return False
        self._data[key] = value
        self._dirty = True
        return True

    @property
    def dirty(self):
        return self._dirty

    def save_if_dirty(self):
        if not self._dirty:
            return True
        try:
            save_settings(self._data, self._path)
        except OSError as error:
            if error.errno in (errno.EROFS, errno.EPERM, errno.EACCES):
                logger.warn("Failed to save settings (read-only filesystem):", error)
                self._dirty = False
                return False
            logger.error("Failed to save settings:", error)
            return False
        else:
            self._dirty = False
            logger.info("Settings saved.")
            return True
