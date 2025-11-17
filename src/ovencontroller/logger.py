"""Simple dual logger for serial + file output on CircuitPython."""

import os
import time


class Logger:
    def __init__(self, logfile="/log.txt", max_bytes=16_384, enabled=True):
        self.logfile = logfile
        self.max_bytes = max_bytes
        self.enabled = enabled
        self._file = None
        self._open_log()

    def _open_log(self):
        if not self.enabled:
            return
        try:
            print("Attemptinging to open log file ", self.logfile)
            self._file = open(self.logfile, "a")
            self.info("Logfile initiated")
        except OSError:
            self._file = None
            self.enabled = False

    def _rotate(self):
        if not self._file:
            return
        try:
            self._file.flush()
            size = os.stat(self.logfile)[6]
        except OSError:
            return
        if size <= self.max_bytes:
            return
        try:
            self._file.close()
            os.rename(self.logfile, f"{self.logfile}.1")
        except OSError:
            pass
        self._open_log()

    def log(self, level, *args):
        message = " ".join(str(arg) for arg in args)
        serial_line = f"[{level}] {message}"
        print(serial_line)
        if self._file:
            try:
                timestamp = time.monotonic()
                self._file.write(f"{timestamp:.1f} {serial_line}\n")
                self._file.flush()
                self._rotate()
            except OSError:
                self._file = None

    def info(self, *args):
        self.log("INFO", *args)

    def warn(self, *args):
        self.log("WARN", *args)

    def error(self, *args):
        self.log("ERR ", *args)


logger = Logger()
