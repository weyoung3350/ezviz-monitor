from dataclasses import dataclass


@dataclass
class StreamState:
    last_error_at: float
    reconnect_interval_seconds: float


def should_retry_connect(state: StreamState, now_monotonic: float) -> bool:
    return now_monotonic - state.last_error_at >= state.reconnect_interval_seconds
