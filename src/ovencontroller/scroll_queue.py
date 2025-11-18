"""Scrolling message queue for TL/TR displays."""

from collections import deque
from typing import Deque, Dict, Optional


class _ScrollMessage:
    def __init__(self, text: str, speed: float, padding: int = 4):
        self.text = (" " * padding + text.upper() + " " * padding)
        self.speed = speed
        self.last_update = 0.0
        self.position = 0

    def advance(self, now: float) -> Optional[Dict[str, str]]:
        if self.last_update == 0.0:
            self.last_update = now
        elif (now - self.last_update) >= self.speed:
            self.position += 1
            self.last_update = now

        if self.position > len(self.text):
            return None

        tl = self.text[self.position : self.position + 4].ljust(4)
        tr = self.text[self.position + 4 : self.position + 8].ljust(4)
        return {"tl": tl, "tr": tr}


class ScrollQueue:
    """Manage queued scroll messages and pending resets."""

    def __init__(self):
        self._queue: Deque[_ScrollMessage] = deque()
        self._active: Optional[_ScrollMessage] = None
        self._pending_reset = False

    def queue_message(self, text: str, speed: float):
        self._queue.append(_ScrollMessage(text, speed))

    def request_reset(self):
        self._pending_reset = True

    def overrides(self, now: float) -> Dict[str, str]:
        if self._active is None and self._queue:
            self._active = self._queue.popleft()

        if self._active is None:
            return {}

        result = self._active.advance(now)
        if result is None:
            self._active = None
            return self.overrides(now)
        return result

    def ready_to_reset(self) -> bool:
        return (
            self._pending_reset and not self._active and not self._queue
        )

    def clear_reset(self):
        self._pending_reset = False
