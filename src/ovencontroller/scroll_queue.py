"""Scrolling message queue for TL/TR displays."""

from .logger import logger


class _ScrollMessage:
    def __init__(self, text, speed, padding=4):
        raw = " " * padding + text.upper() + " " * padding
        self.text = raw
        self.speed = speed
        self.last_update = 0.0
        self.position = 0
        self._tokens = self._tokenize(raw)

    def _tokenize(self, text):
        tokens = []
        for char in text:
            if char == ".":
                if tokens:
                    tokens[-1] += "."
                continue
            tokens.append(char)
        return tokens

    def advance(self, now):
        if self.last_update == 0.0:
            self.last_update = now
        elif (now - self.last_update) >= self.speed:
            self.position += 1
            self.last_update = now

        if self.position > len(self._tokens):
            return None

        window = self._tokens[self.position : self.position + 8]
        if len(window) < 8:
            window += [" "] * (8 - len(window))
        tl_tokens = window[:4]
        tr_tokens = window[4:]
        return {"tl": "".join(tl_tokens), "tr": "".join(tr_tokens)}


class ScrollQueue:
    """Manage queued scroll messages and pending resets."""

    def __init__(self):
        self._queue = []
        self._active = None
        self._pending_reset = False

    def queue_message(self, text, speed):
        logger.info("Queue scroll message:", text)
        self._queue.append(_ScrollMessage(text, speed))

    def request_reset(self):
        self._pending_reset = True

    def overrides(self, now):
        if self._active is None and self._queue:
            self._active = self._queue.pop(0)

        if self._active is None:
            return {}

        result = self._active.advance(now)
        if result is None:
            self._active = None
            return self.overrides(now)
        return result

    def ready_to_reset(self):
        return (
            self._pending_reset and not self._active and not self._queue
        )

    def clear_reset(self):
        self._pending_reset = False
