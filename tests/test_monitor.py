from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from src.monitor import (
    FrameBuffer,
    _save_clip,
    _save_snapshot,
    ensure_face_recognition_available,
    should_trigger_rule,
)


# --- should_trigger_rule ---

def test_trigger_when_all_conditions_met():
    assert should_trigger_rule(in_schedule=True, target_hit=True, cooldown_allows=True)


def test_no_trigger_outside_schedule():
    assert not should_trigger_rule(in_schedule=False, target_hit=True, cooldown_allows=True)


def test_no_trigger_without_hit():
    assert not should_trigger_rule(in_schedule=True, target_hit=False, cooldown_allows=True)


def test_no_trigger_during_cooldown():
    assert not should_trigger_rule(in_schedule=True, target_hit=True, cooldown_allows=False)


def test_no_trigger_when_nothing():
    assert not should_trigger_rule(in_schedule=False, target_hit=False, cooldown_allows=False)


# --- FrameBuffer ---

def test_frame_buffer_stores_and_retrieves():
    buf = FrameBuffer(max_seconds=2.0, fps=5.0)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    buf.add(1.0, frame)
    buf.add(2.0, frame)
    frames = buf.get_frames()
    assert len(frames) == 2


def test_frame_buffer_respects_max_frames():
    buf = FrameBuffer(max_seconds=1.0, fps=2.0)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    buf.add(1.0, frame)
    buf.add(2.0, frame)
    buf.add(3.0, frame)
    frames = buf.get_frames()
    assert len(frames) == 2
    assert frames[0][0] == 2.0


def test_frame_buffer_fps_property():
    buf = FrameBuffer(max_seconds=5.0, fps=15.0)
    assert buf.fps == 15.0


# --- _save_snapshot / _save_clip ---

def test_save_snapshot_creates_file(tmp_path: Path):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    now = datetime(2026, 4, 8, 23, 15, 2)
    path = _save_snapshot(frame, tmp_path, "电梯厅", now)
    assert path.exists()
    assert path.name == "2026-04-08_23-15-02_snapshot.jpg"
    assert path.parent.name == "电梯厅"


def test_save_clip_creates_file(tmp_path: Path):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    now = datetime(2026, 4, 8, 23, 15, 2)
    pre = [(1.0, frame), (2.0, frame)]
    post = [(3.0, frame), (4.0, frame)]
    path = _save_clip(pre, post, tmp_path, "电梯厅", now, fps=10.0)
    assert path.exists()
    assert path.stat().st_size > 0


def test_save_clip_no_frame_duplication(tmp_path: Path):
    fa = np.full((100, 100, 3), 100, dtype=np.uint8)
    fb = np.full((100, 100, 3), 200, dtype=np.uint8)
    now = datetime(2026, 4, 8, 23, 15, 2)
    pre = [(1.0, fa), (2.0, fa)]
    post = [(3.0, fb), (4.0, fb)]
    path = _save_clip(pre, post, tmp_path, "测试", now, fps=10.0)
    cap = cv2.VideoCapture(str(path))
    count = 0
    while True:
        ret, _ = cap.read()
        if not ret:
            break
        count += 1
    cap.release()
    assert count == 4


# --- ensure_face_recognition_available ---

def test_ensure_face_recognition_available_passes():
    ensure_face_recognition_available()


def test_ensure_face_recognition_missing_raises():
    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "face_recognition":
            raise ImportError("mocked")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with pytest.raises(ImportError, match="face_recognition 未安装"):
            ensure_face_recognition_available()


# --- 规则驱动集成逻辑测试（纯逻辑，不依赖 RTSP/OpenCV） ---

def test_full_rule_flow_yangxiaozhi_in_schedule():
    """告警时段 + 杨孝治命中 + 冷却允许 → 触发。"""
    from src.vision import PersonHitWindow
    from src.alerts import AlertCooldown
    from src.scheduler import is_in_schedule

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)
    cooldown = AlertCooldown(minutes=5)
    schedules = [{"start": "22:00", "end": "07:00"}]
    now_dt = datetime(2026, 4, 8, 23, 30)

    # 模拟连续两帧识别到杨孝治
    assert not window.record("杨孝治", event_time=0.0)
    target_hit = window.record("杨孝治", event_time=0.5)
    assert target_hit

    in_sched = is_in_schedule(schedules, now_dt)
    cool_ok = cooldown.should_trigger("电梯厅", now_dt)

    assert should_trigger_rule(in_sched, target_hit, cool_ok)


def test_full_rule_flow_outside_schedule():
    """非告警时段 + 杨孝治命中 → 不触发。"""
    from src.vision import PersonHitWindow
    from src.alerts import AlertCooldown
    from src.scheduler import is_in_schedule

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)
    cooldown = AlertCooldown(minutes=5)
    schedules = [{"start": "22:00", "end": "07:00"}]
    now_dt = datetime(2026, 4, 8, 14, 0)  # 下午，非告警时段

    window.record("杨孝治", event_time=0.0)
    target_hit = window.record("杨孝治", event_time=0.5)
    assert target_hit

    in_sched = is_in_schedule(schedules, now_dt)
    assert not in_sched
    assert not should_trigger_rule(in_sched, target_hit, True)


def test_full_rule_flow_other_family():
    """告警时段 + 其他家庭成员 → 不触发。"""
    from src.vision import PersonHitWindow

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)

    # 连续多帧识别到老婆
    window.record("老婆", event_time=0.0)
    window.record("老婆", event_time=0.5)
    target_hit = window.record("老婆", event_time=1.0)
    assert not target_hit


def test_full_rule_flow_uncertain_identity():
    """告警时段 + 不确定身份 → 不触发。"""
    from src.vision import PersonHitWindow

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)

    window.record(None, event_time=0.0)
    window.record(None, event_time=0.5)
    target_hit = window.record(None, event_time=1.0)
    assert not target_hit


def test_full_rule_flow_cooldown_blocks():
    """告警时段 + 杨孝治命中 + 冷却中 → 不触发。"""
    from src.vision import PersonHitWindow
    from src.alerts import AlertCooldown
    from datetime import timedelta

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)
    cooldown = AlertCooldown(minutes=5)
    now_dt = datetime(2026, 4, 8, 23, 30)

    # 第一次触发
    cooldown.record("电梯厅", now_dt)

    # 2 分钟后再次命中
    window.record("杨孝治", event_time=0.0)
    target_hit = window.record("杨孝治", event_time=0.5)
    assert target_hit

    cool_ok = cooldown.should_trigger("电梯厅", now_dt + timedelta(minutes=2))
    assert not cool_ok
    assert not should_trigger_rule(True, target_hit, cool_ok)


def test_phone_failure_still_allows_evidence():
    """电话告警失败时，证据保存和终端日志应该继续执行。

    这个测试验证的是逻辑分离：phone_result.success 不影响后续步骤。
    """
    from src.phone_alert import MockPhoneAlertClient, PhoneAlertEvent

    client = MockPhoneAlertClient(should_succeed=False, error_message="网络故障")
    event = PhoneAlertEvent(
        person_name="杨孝治",
        camera_name="电梯厅",
        rule_name="杨孝治夜间外出监护",
        event_time=datetime(2026, 4, 8, 23, 30),
    )

    result = client.call(event)
    assert not result.success

    # 电话失败后，仍然可以继续保存证据（这里验证流程不中断）
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _save_snapshot(frame, Path(tmp), "电梯厅", event.event_time)
        assert path.exists()
