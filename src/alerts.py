from datetime import datetime, timedelta


class AlertCooldown:
    def __init__(self, minutes: int) -> None:
        self._cooldown = timedelta(minutes=minutes)
        self._last_triggered: dict[str, datetime] = {}

    def should_trigger(self, camera_name: str, now: datetime) -> bool:
        last = self._last_triggered.get(camera_name)
        if last is None:
            return True
        return now - last >= self._cooldown

    def record(self, camera_name: str, now: datetime) -> None:
        self._last_triggered[camera_name] = now
