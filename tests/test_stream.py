from src.stream import StreamState, should_retry_connect


def test_should_not_retry_before_interval():
    state = StreamState(last_error_at=10.0, reconnect_interval_seconds=10)
    assert not should_retry_connect(state, now_monotonic=15.0)


def test_should_retry_after_interval():
    state = StreamState(last_error_at=10.0, reconnect_interval_seconds=10)
    assert should_retry_connect(state, now_monotonic=20.1)


def test_should_retry_at_exact_interval():
    state = StreamState(last_error_at=10.0, reconnect_interval_seconds=10)
    assert should_retry_connect(state, now_monotonic=20.0)


def test_different_intervals():
    state = StreamState(last_error_at=0.0, reconnect_interval_seconds=5)
    assert not should_retry_connect(state, now_monotonic=3.0)
    assert should_retry_connect(state, now_monotonic=5.0)
    assert should_retry_connect(state, now_monotonic=100.0)
