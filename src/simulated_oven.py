"""Simulated oven temperature model used when no sensor is available."""

import time


class SimulatedOven:
    """Generate oven temperatures based on element activity and set point."""

    def __init__(
        self,
        *,
        ambient_temp=75.0,
        single_direct_rate=3.5,
        double_direct_rate=6.0,
        single_heat_gain=0.6,
        double_heat_gain=1.4,
        heat_loss=0.25,
        heat_to_temp_rate=0.15,
        cool_rate=0.015,
        max_heat=20.0,
        max_temp=650.0,
    ):
        self.ambient_temp = ambient_temp
        self.single_direct_rate = single_direct_rate
        self.double_direct_rate = double_direct_rate
        self.single_heat_gain = single_heat_gain
        self.double_heat_gain = double_heat_gain
        self.heat_loss = heat_loss
        self.heat_to_temp_rate = heat_to_temp_rate
        self.cool_rate = cool_rate
        self.max_heat = max_heat
        self.max_temp = max_temp

        self._temperature = None
        self._stored_heat = 0.0
        self._last_update = None

    def read(self, *, set_temp, bottom_on, top_on, now=None):
        """Return the next simulated temperature value."""

        if now is None:
            now = time.monotonic()

        if self._temperature is None:
            # Seed the simulation with an ambient-temperature oven so that
            # warm-up behaviour is visible as soon as elements begin cycling.
            self._temperature = self.ambient_temp
            self._last_update = now
            return self._temperature

        if self._last_update is None:
            self._last_update = now
            return self._temperature

        dt = now - self._last_update
        if dt <= 0.0:
            return self._temperature

        self._last_update = now

        element_count = int(bool(bottom_on)) + int(bool(top_on))
        if element_count == 2:
            direct_rate = self.double_direct_rate
            heat_gain = self.double_heat_gain
        elif element_count == 1:
            direct_rate = self.single_direct_rate
            heat_gain = self.single_heat_gain
        else:
            direct_rate = 0.0
            heat_gain = 0.0

        if element_count:
            direct_delta = direct_rate * dt
        else:
            direct_delta = 0.0

        self._stored_heat += heat_gain * dt
        self._stored_heat -= self.heat_loss * dt
        if self._stored_heat < 0.0:
            self._stored_heat = 0.0
        elif self._stored_heat > self.max_heat:
            self._stored_heat = self.max_heat

        heat_delta = self._stored_heat * self.heat_to_temp_rate * dt

        cooling_delta = (self._temperature - self.ambient_temp) * self.cool_rate * dt

        new_temp = self._temperature + direct_delta + heat_delta - cooling_delta
        if new_temp < self.ambient_temp:
            new_temp = self.ambient_temp
        elif new_temp > self.max_temp:
            new_temp = self.max_temp

        self._temperature = new_temp
        return self._temperature
