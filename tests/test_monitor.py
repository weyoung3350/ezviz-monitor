from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import tempfile

import cv2
import numpy as np
import pytest

from src.monitor import (
    FrameBuffer,
    _load_image_with_exif,
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


# --- FrameBuffer ---

def test_frame_buffer_stores_and_retrieves():
    buf = FrameBuffer(max_seconds=2.0, fps=5.0)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    buf.add(1.0, frame)
    buf.add(2.0, frame)
    assert len(buf.get_frames()) == 2


def test_frame_buffer_respects_max_frames():
    buf = FrameBuffer(max_seconds=1.0, fps=2.0)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    buf.add(1.0, frame)
    buf.add(2.0, frame)
    buf.add(3.0, frame)
    frames = buf.get_frames()
    assert len(frames) == 2
    assert frames[0][0] == 2.0


# --- 证据保存 ---

def test_save_snapshot_creates_file(tmp_path: Path):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    now = datetime(2026, 4, 8, 23, 15, 2)
    path = _save_snapshot(frame, tmp_path, "电梯厅", now)
    assert path.exists()
    assert path.parent.name == "电梯厅"


def test_save_clip_creates_file(tmp_path: Path):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    now = datetime(2026, 4, 8, 23, 15, 2)
    path = _save_clip([(1.0, frame)], [(2.0, frame)], tmp_path, "电梯厅", now, fps=10.0)
    assert path.exists()


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


# === 规则驱动集成逻辑测试 ===

def test_rule_flow_yangxiaozhi_in_schedule():
    """告警时段 + 杨孝治命中 + 冷却允许 → 触发。"""
    from src.vision import PersonHitWindow
    from src.alerts import AlertCooldown
    from src.scheduler import is_in_schedule

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)
    cooldown = AlertCooldown(minutes=5)
    schedules = [{"start": "22:00", "end": "07:00"}]
    now_dt = datetime(2026, 4, 8, 23, 30)
    cooldown_key = "电梯厅:杨孝治夜间外出监护"

    window.record("杨孝治", event_time=0.0)
    window.record("杨孝治", event_time=0.5)
    assert window.is_hit()

    in_sched = is_in_schedule(schedules, now_dt)
    cool_ok = cooldown.should_trigger(cooldown_key, now_dt)
    assert should_trigger_rule(in_sched, True, cool_ok)

    window.consume()
    assert not window.is_hit()


def test_rule_flow_outside_schedule_preserves_hit():
    """非告警时段命中时，命中状态不被消费，等进入时段后可触发。"""
    from src.vision import PersonHitWindow
    from src.scheduler import is_in_schedule

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=60)
    schedules = [{"start": "22:00", "end": "07:00"}]

    # 下午 14:00 命中
    window.record("杨孝治", event_time=0.0)
    window.record("杨孝治", event_time=0.5)
    assert window.is_hit()

    afternoon = datetime(2026, 4, 8, 14, 0)
    assert not is_in_schedule(schedules, afternoon)
    # 不消费 → 命中状态保留
    assert window.is_hit()

    # 进入时段后，命中状态仍在
    night = datetime(2026, 4, 8, 23, 0)
    assert is_in_schedule(schedules, night)
    assert window.is_hit()  # 可以触发

    window.consume()
    assert not window.is_hit()


def test_rule_flow_other_family_no_hit():
    """其他家庭成员不触发杨孝治规则。"""
    from src.vision import PersonHitWindow

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)
    window.record("老婆", event_time=0.0)
    window.record("老婆", event_time=0.5)
    window.record("儿子", event_time=1.0)
    assert not window.is_hit()


def test_rule_flow_uncertain_no_hit():
    """不确定身份不触发。"""
    from src.vision import PersonHitWindow

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)
    window.record(None, event_time=0.0)
    window.record(None, event_time=0.5)
    assert not window.is_hit()


def test_rule_flow_cooldown_blocks():
    """冷却期内不重复触发。"""
    from src.vision import PersonHitWindow
    from src.alerts import AlertCooldown

    window = PersonHitWindow(target_name="杨孝治", frame_threshold=2, window_seconds=2)
    cooldown = AlertCooldown(minutes=5)
    cooldown_key = "电梯厅:杨孝治夜间外出监护"
    now_dt = datetime(2026, 4, 8, 23, 30)

    # 第一次触发
    cooldown.record(cooldown_key, now_dt)

    # 2 分钟后再次命中
    window.record("杨孝治", event_time=0.0)
    window.record("杨孝治", event_time=0.5)
    assert window.is_hit()

    cool_ok = cooldown.should_trigger(cooldown_key, now_dt + timedelta(minutes=2))
    assert not cool_ok
    assert not should_trigger_rule(True, True, cool_ok)


def test_cooldown_per_camera_and_rule():
    """同摄像头不同规则的冷却互不影响。"""
    from src.alerts import AlertCooldown

    cooldown = AlertCooldown(minutes=5)
    now_dt = datetime(2026, 4, 8, 23, 30)

    key_a = "电梯厅:规则A"
    key_b = "电梯厅:规则B"

    cooldown.record(key_a, now_dt)

    assert not cooldown.should_trigger(key_a, now_dt + timedelta(minutes=1))
    assert cooldown.should_trigger(key_b, now_dt + timedelta(minutes=1))


def test_phone_failure_still_allows_evidence():
    """电话告警失败时，证据保存继续执行。"""
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

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    with tempfile.TemporaryDirectory() as tmp:
        path = _save_snapshot(frame, Path(tmp), "电梯厅", event.event_time)
        assert path.exists()


# --- EXIF 旋转处理 ---

def test_load_image_with_exif_applies_transpose(tmp_path: Path):
    """验证 _load_image_with_exif 调用了 exif_transpose，修正图片方向。"""
    from PIL import Image

    # 创建一张 200x100 的横向图片，设置 EXIF Orientation=6（顺时针旋转 90°）
    img = Image.new("RGB", (200, 100), color=(255, 0, 0))
    from PIL.ExifTags import Base as ExifBase
    import piexif
    exif_dict = {"0th": {piexif.ImageIFD.Orientation: 6}}
    exif_bytes = piexif.dump(exif_dict)
    path = tmp_path / "rotated.jpg"
    img.save(str(path), exif=exif_bytes)

    # 原始像素：200x100。EXIF Orientation=6 表示顺时针 90°，
    # exif_transpose 后应变为 100x200
    result = _load_image_with_exif(path)
    h, w = result.shape[:2]
    assert w == 100 and h == 200, f"期望 100x200，实际 {w}x{h}"


def test_load_image_with_exif_no_exif_passthrough(tmp_path: Path):
    """没有 EXIF 信息的图片应原样返回。"""
    from PIL import Image

    img = Image.new("RGB", (300, 150), color=(0, 255, 0))
    path = tmp_path / "normal.jpg"
    img.save(str(path))

    result = _load_image_with_exif(path)
    h, w = result.shape[:2]
    assert w == 300 and h == 150
