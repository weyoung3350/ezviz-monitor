import time

from src.status_panel import StatusData, format_duration, render_status


def test_format_duration_zero():
    assert format_duration(0) == "00:00:00"


def test_format_duration_mixed():
    assert format_duration(3661) == "01:01:01"


def test_format_duration_hours():
    assert format_duration(7200) == "02:00:00"


def test_render_status_contains_camera():
    data = StatusData(camera_name="电梯厅", rule_name="杨孝治夜间外出监护")
    text = render_status(data)
    assert "电梯厅" in text


def test_render_status_contains_rule():
    data = StatusData(camera_name="电梯厅", rule_name="杨孝治夜间外出监护")
    text = render_status(data)
    assert "杨孝治夜间外出监护" in text


def test_render_status_contains_rtsp_status():
    data = StatusData(rtsp_status="已连接")
    text = render_status(data)
    assert "已连接" in text


def test_render_status_contains_identity():
    data = StatusData(last_identity="识别: 杨孝治")
    text = render_status(data)
    assert "杨孝治" in text


def test_render_status_contains_phone_status():
    data = StatusData(phone_status="骨架模式，不能真实拨打")
    text = render_status(data)
    assert "骨架模式" in text


def test_render_status_contains_evidence_size():
    data = StatusData(evidence_size_mb=65.3)
    text = render_status(data)
    assert "65.3 MB" in text


def test_render_status_contains_ctrl_c_hint():
    data = StatusData()
    text = render_status(data)
    assert "Ctrl+C" in text


def test_render_status_contains_frames_count():
    data = StatusData(frames_analyzed=42)
    text = render_status(data)
    assert "42" in text
