from src.vision import StrangerDecisionWindow


def test_single_hit_does_not_trigger():
    window = StrangerDecisionWindow(frame_threshold=3, window_seconds=2)
    assert not window.record(True, event_time=0.1)


def test_threshold_reached_triggers():
    window = StrangerDecisionWindow(frame_threshold=3, window_seconds=2)
    assert not window.record(True, event_time=0.1)
    assert not window.record(True, event_time=0.6)
    assert window.record(True, event_time=1.0)


def test_non_stranger_does_not_count():
    window = StrangerDecisionWindow(frame_threshold=3, window_seconds=2)
    assert not window.record(True, event_time=0.0)
    assert not window.record(False, event_time=0.5)
    assert not window.record(False, event_time=1.0)
    # 窗口内只有 1 次 True（0.0），False 不计数
    assert not window.record(True, event_time=1.5)
    # 窗口内 2 次 True（0.0, 1.5），还差 1 次
    assert window.record(True, event_time=1.8)
    # 窗口内 3 次 True（0.0, 1.5, 1.8）=> 触发


def test_old_records_expire():
    window = StrangerDecisionWindow(frame_threshold=3, window_seconds=2)
    assert not window.record(True, event_time=0.0)
    assert not window.record(True, event_time=0.5)
    # 等到 3.0 秒，前面两个已过期
    assert not window.record(True, event_time=3.0)
    assert not window.record(True, event_time=3.5)
    assert window.record(True, event_time=4.0)


def test_reset_after_trigger():
    window = StrangerDecisionWindow(frame_threshold=3, window_seconds=2)
    assert not window.record(True, event_time=0.1)
    assert not window.record(True, event_time=0.6)
    assert window.record(True, event_time=1.0)
    # 触发后应重置
    assert not window.record(True, event_time=1.5)
    assert not window.record(True, event_time=2.0)
    assert window.record(True, event_time=2.5)
