from src.vision import PersonHitWindow


TARGET = "杨孝治"


# --- 单帧不触发 ---

def test_single_hit_does_not_trigger():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert not w.record(TARGET, event_time=0.1)


# --- 连续多帧命中后触发 ---

def test_threshold_reached_triggers():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert not w.record(TARGET, event_time=0.1)
    assert w.record(TARGET, event_time=0.6)


def test_threshold_3_needs_3_hits():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=3, window_seconds=3)
    assert not w.record(TARGET, event_time=0.0)
    assert not w.record(TARGET, event_time=0.5)
    assert w.record(TARGET, event_time=1.0)


# --- 其他家庭成员不触发 ---

def test_other_family_does_not_trigger():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert not w.record("老婆", event_time=0.1)
    assert not w.record("老婆", event_time=0.5)
    assert not w.record("老婆", event_time=1.0)
    assert not w.record("儿子", event_time=1.5)
    # 即使其他人大量出现，也不触发


# --- 不确定身份不触发 ---

def test_none_does_not_trigger():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert not w.record(None, event_time=0.1)
    assert not w.record(None, event_time=0.5)
    assert not w.record(None, event_time=1.0)


def test_uncertain_mixed_does_not_trigger():
    """不确定帧夹在目标帧中间，不应影响目标帧的计数。"""
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert not w.record(TARGET, event_time=0.0)
    assert not w.record(None, event_time=0.3)       # 不确定，不影响已有命中
    assert not w.record("老婆", event_time=0.6)      # 其他人，不影响已有命中
    assert w.record(TARGET, event_time=1.0)           # 第 2 次命中 → 触发


# --- 时间窗外旧命中失效 ---

def test_old_hits_expire():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert not w.record(TARGET, event_time=0.0)
    # 3 秒后，第一次命中已过期
    assert not w.record(TARGET, event_time=3.0)
    # 需要再来一次
    assert w.record(TARGET, event_time=4.0)


def test_partial_expiry():
    """窗口内只保留未过期的命中。"""
    w = PersonHitWindow(target_name=TARGET, frame_threshold=3, window_seconds=2)
    assert not w.record(TARGET, event_time=0.0)
    assert not w.record(TARGET, event_time=0.5)
    # 3.0 时，0.0 和 0.5 都过期了
    assert not w.record(TARGET, event_time=3.0)
    assert not w.record(TARGET, event_time=3.5)
    assert w.record(TARGET, event_time=4.0)


# --- 触发后状态重置 ---

def test_reset_after_trigger():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert not w.record(TARGET, event_time=0.1)
    assert w.record(TARGET, event_time=0.6)
    # 触发后重置，下一次需重新累积
    assert not w.record(TARGET, event_time=1.0)
    assert w.record(TARGET, event_time=1.5)


def test_reset_clears_completely():
    """触发后即使紧接着来目标帧，也需从 0 开始累积。"""
    w = PersonHitWindow(target_name=TARGET, frame_threshold=3, window_seconds=5)
    assert not w.record(TARGET, event_time=0.0)
    assert not w.record(TARGET, event_time=0.5)
    assert w.record(TARGET, event_time=1.0)     # 第 3 帧 → 触发
    # 触发后立刻再来 2 帧，还差 1 帧
    assert not w.record(TARGET, event_time=1.5)
    assert not w.record(TARGET, event_time=2.0)
    assert w.record(TARGET, event_time=2.5)     # 重新累积到 3 → 再次触发


# --- target_name 属性 ---

def test_target_name_property():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert w.target_name == TARGET
