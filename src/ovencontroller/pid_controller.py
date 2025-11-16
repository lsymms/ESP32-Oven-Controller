"""PID controller utilities for the oven controller."""

from __future__ import annotations


def _clamp(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


class PIDController:
    """PID controller with integral windup guarding and time-proportional output."""

    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        *,
        output_min: float,
        output_max: float,
        integral_limit: float,
        cycle_time: float,
    ) -> None:
        self.output_min = output_min
        self.output_max = output_max
        self.integral_limit = integral_limit
        self.cycle_time = cycle_time

        self.kp = kp
        self.ki = ki
        self.kd = kd

        self.integral = 0.0
        self.last_error = 0.0
        self.last_time = None
        self.cycle_start = None
        self.cycle_on_time = 0.0
        self.last_output = 0.0

    def configure(self, *, kp: float, ki: float, kd: float) -> None:
        """Update controller gains."""

        self.kp = kp
        self.ki = ki
        self.kd = kd

    def reset(self, *, now=None, error: float = 0.0) -> None:
        """Reset accumulated state, optionally seeding the last timestamp and error."""

        self.integral = 0.0
        self.last_error = error
        self.last_time = now
        self.cycle_start = None
        self.cycle_on_time = 0.0
        self.last_output = 0.0

    def update(self, error: float, now) -> tuple[float, bool]:
        """Update the PID controller and return (output, heater_on)."""

        if self.last_time is None:
            dt = 0.0
        else:
            dt = now - self.last_time
            if dt < 0:
                dt = 0.0

        derivative = 0.0
        if dt > 0:
            self.integral += error * dt
            self.integral = _clamp(
                self.integral, -self.integral_limit, self.integral_limit
            )
            derivative = (error - self.last_error) / dt

        output = (
            (self.kp * error)
            + (self.ki * self.integral)
            + (self.kd * derivative)
        )
        output = _clamp(output, self.output_min, self.output_max)

        self.last_output = output
        self.last_error = error
        self.last_time = now

        heater_on = self._cycle_state(now)
        return output, heater_on

    def _cycle_state(self, now) -> bool:
        """Return whether the time-proportional output should be active."""

        on_time = self.last_output * self.cycle_time
        self.cycle_on_time = on_time

        if self.cycle_start is None or now < self.cycle_start:
            self.cycle_start = now
            elapsed = 0.0
        else:
            elapsed = now - self.cycle_start
            if elapsed >= self.cycle_time:
                self.cycle_start = now
                elapsed = 0.0

        if self.cycle_time <= 0:
            return on_time > 0

        return elapsed < on_time
