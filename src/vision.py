from collections import deque


class StrangerDecisionWindow:
    def __init__(self, frame_threshold: int, window_seconds: float) -> None:
        self._threshold = frame_threshold
        self._window = window_seconds
        self._hits: deque[float] = deque()

    def _purge_old(self, now: float) -> None:
        while self._hits and now - self._hits[0] > self._window:
            self._hits.popleft()

    def record(self, is_stranger: bool, event_time: float) -> bool:
        self._purge_old(event_time)

        if is_stranger:
            self._hits.append(event_time)

        if len(self._hits) >= self._threshold:
            self._hits.clear()
            return True

        return False
