from src.vision import PersonHitWindow


TARGET = "杨孝治"


# --- 单帧不触发 ---

def test_single_hit_does_not_trigger():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    w.record(TARGET, event_time=0.1)
    assert not w.is_hit()


# --- 连续多帧命中后触发 ---

def test_threshold_reached_triggers():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    w.record(TARGET, event_time=0.1)
    w.record(TARGET, event_time=0.6)
    assert w.is_hit()


def test_threshold_3_needs_3_hits():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=3, window_seconds=3)
    w.record(TARGET, event_time=0.0)
    w.record(TARGET, event_time=0.5)
    assert not w.is_hit()
    w.record(TARGET, event_time=1.0)
    assert w.is_hit()


# --- 其他家庭成员不触发 ---

def test_other_family_does_not_trigger():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    w.record("老婆", event_time=0.1)
    w.record("老婆", event_time=0.5)
    w.record("儿子", event_time=1.0)
    assert not w.is_hit()


# --- 不确定身份不触发 ---

def test_none_does_not_trigger():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    w.record(None, event_time=0.1)
    w.record(None, event_time=0.5)
    assert not w.is_hit()


def test_uncertain_mixed_does_not_block_target():
    """不确定帧夹在目标帧中间，不影响目标帧的计数。"""
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    w.record(TARGET, event_time=0.0)
    w.record(None, event_time=0.3)
    w.record("老婆", event_time=0.6)
    assert not w.is_hit()
    w.record(TARGET, event_time=1.0)
    assert w.is_hit()


# --- 时间窗外旧命中失效 ---

def test_old_hits_expire():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    w.record(TARGET, event_time=0.0)
    w.record(TARGET, event_time=3.0)  # 0.0 已过期
    assert not w.is_hit()
    w.record(TARGET, event_time=4.0)
    assert w.is_hit()


def test_partial_expiry():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=3, window_seconds=2)
    w.record(TARGET, event_time=0.0)
    w.record(TARGET, event_time=0.5)
    w.record(TARGET, event_time=3.0)  # 前两个过期
    w.record(TARGET, event_time=3.5)
    assert not w.is_hit()
    w.record(TARGET, event_time=4.0)
    assert w.is_hit()


# --- consume 后重置 ---

def test_consume_resets_state():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    w.record(TARGET, event_time=0.1)
    w.record(TARGET, event_time=0.6)
    assert w.is_hit()

    w.consume()
    assert not w.is_hit()

    # 需重新累积
    w.record(TARGET, event_time=1.0)
    assert not w.is_hit()
    w.record(TARGET, event_time=1.5)
    assert w.is_hit()


def test_is_hit_without_consume_stays_true():
    """不调 consume 时，is_hit 保持 True，不会自动消费。"""
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=5)
    w.record(TARGET, event_time=0.0)
    w.record(TARGET, event_time=0.5)
    assert w.is_hit()
    assert w.is_hit()  # 多次查询不消费


# --- target_name 属性 ---

def test_target_name_property():
    w = PersonHitWindow(target_name=TARGET, frame_threshold=2, window_seconds=2)
    assert w.target_name == TARGET
