import os
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
    should_alert_for_detection,
)


# --- should_alert_for_detection 纯布尔逻辑 ---

def test_should_alert_when_all_conditions_met():
    assert should_alert_for_detection(
        in_schedule=True,
        stranger_event=True,
        cooldown_allows=True,
    )


def test_should_not_alert_outside_schedule():
    assert not should_alert_for_detection(
        in_schedule=False,
        stranger_event=True,
        cooldown_allows=True,
    )


def test_should_not_alert_when_no_stranger():
    assert not should_alert_for_detection(
        in_schedule=True,
        stranger_event=False,
        cooldown_allows=True,
    )


def test_should_not_alert_during_cooldown():
    assert not should_alert_for_detection(
        in_schedule=True,
        stranger_event=True,
        cooldown_allows=False,
    )


def test_should_not_alert_when_nothing():
    assert not should_alert_for_detection(
        in_schedule=False,
        stranger_event=False,
        cooldown_allows=False,
    )


# --- FrameBuffer 测试 ---

def test_frame_buffer_stores_and_retrieves():
    buf = FrameBuffer(max_seconds=2.0, fps=5.0)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    buf.add(1.0, frame)
    buf.add(2.0, frame)

    frames = buf.get_frames()
    assert len(frames) == 2
    assert frames[0][0] == 1.0
    assert frames[1][0] == 2.0


def test_frame_buffer_respects_max_frames():
    buf = FrameBuffer(max_seconds=1.0, fps=2.0)  # max_frames = 2
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    buf.add(1.0, frame)
    buf.add(2.0, frame)
    buf.add(3.0, frame)  # 应挤掉第一帧

    frames = buf.get_frames()
    assert len(frames) == 2
    assert frames[0][0] == 2.0
    assert frames[1][0] == 3.0


def test_frame_buffer_fps_property():
    buf = FrameBuffer(max_seconds=5.0, fps=15.0)
    assert buf.fps == 15.0


# --- _save_snapshot 测试 ---

def test_save_snapshot_creates_file(tmp_path: Path):
    from datetime import datetime
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    now = datetime(2026, 4, 8, 23, 15, 2)

    path = _save_snapshot(frame, tmp_path, "客厅", now)

    assert path.exists()
    assert path.name == "2026-04-08_23-15-02_snapshot.jpg"
    assert path.parent.name == "客厅"


# --- _save_clip 测试 ---

def test_save_clip_creates_file(tmp_path: Path):
    from datetime import datetime
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    now = datetime(2026, 4, 8, 23, 15, 2)

    pre_frames = [(1.0, frame), (2.0, frame)]
    post_frames = [(3.0, frame), (4.0, frame)]

    path = _save_clip(pre_frames, post_frames, tmp_path, "客厅", now, fps=10.0)

    assert path.exists()
    assert path.name == "2026-04-08_23-15-02_clip.mp4"
    assert path.stat().st_size > 0


def test_save_clip_no_frame_duplication(tmp_path: Path):
    """验证 pre_frames 和 post_frames 不会重复写入。"""
    from datetime import datetime
    frame_a = np.full((100, 100, 3), 100, dtype=np.uint8)
    frame_b = np.full((100, 100, 3), 200, dtype=np.uint8)
    now = datetime(2026, 4, 8, 23, 15, 2)

    pre_frames = [(1.0, frame_a), (2.0, frame_a)]
    post_frames = [(3.0, frame_b), (4.0, frame_b)]

    path = _save_clip(pre_frames, post_frames, tmp_path, "测试", now, fps=10.0)

    # 读回视频验证帧数 = pre + post = 4
    cap = cv2.VideoCapture(str(path))
    frame_count = 0
    while True:
        ret, _ = cap.read()
        if not ret:
            break
        frame_count += 1
    cap.release()

    assert frame_count == 4, f"期望 4 帧，实际 {frame_count} 帧（可能有重复）"


def test_save_clip_empty_frames(tmp_path: Path):
    from datetime import datetime
    now = datetime(2026, 4, 8, 23, 15, 2)

    path = _save_clip([], [], tmp_path, "客厅", now, fps=10.0)
    # 空帧时文件路径仍返回，但文件可能不存在或为空
    assert path.name == "2026-04-08_23-15-02_clip.mp4"


# --- ensure_face_recognition_available 测试 ---

def test_ensure_face_recognition_available_passes():
    """face_recognition 已安装时不应抛异常。"""
    ensure_face_recognition_available()


def test_ensure_face_recognition_missing_raises():
    """模拟 face_recognition 未安装时必须抛出 ImportError。"""
    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "face_recognition":
            raise ImportError("mocked")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with pytest.raises(ImportError, match="face_recognition 未安装"):
            ensure_face_recognition_available()
