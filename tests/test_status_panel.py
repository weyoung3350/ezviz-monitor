import io
import time
from unittest.mock import patch

from src.status_panel import (
    StatusData,
    StatusPanel,
    format_duration,
    is_tty,
    render_heartbeat,
    render_status,
)


# --- format_duration ---

def test_format_duration_zero():
    assert format_duration(0) == "00:00:00"


def test_format_duration_mixed():
    assert format_duration(3661) == "01:01:01"


def test_format_duration_hours():
    assert format_duration(7200) == "02:00:00"


# --- render_status 关键字段 ---

def test_render_status_contains_camera():
    data = StatusData(camera_name="电梯厅", rule_name="杨孝治夜间外出监护")
    assert "电梯厅" in render_status(data)


def test_render_status_contains_rule():
    data = StatusData(rule_name="杨孝治夜间外出监护")
    assert "杨孝治夜间外出监护" in render_status(data)


def test_render_status_contains_rtsp_status():
    data = StatusData(rtsp_status="已连接")
    assert "已连接" in render_status(data)


def test_render_status_contains_identity():
    data = StatusData(last_identity="识别: 杨孝治")
    assert "杨孝治" in render_status(data)


def test_render_status_contains_phone_status():
    data = StatusData(phone_status="骨架模式，不能真实拨打")
    assert "骨架模式" in render_status(data)


def test_render_status_contains_evidence_size():
    data = StatusData(evidence_size_mb=65.3)
    assert "65.3 MB" in render_status(data)


def test_render_status_contains_ctrl_c_hint():
    assert "Ctrl+C" in render_status(StatusData())


def test_render_status_contains_frames_count():
    data = StatusData(frames_analyzed=42)
    assert "42" in render_status(data)


# --- render_status 不含 ANSI ---

def test_render_status_no_ansi():
    text = render_status(StatusData())
    assert "\033[" not in text


# --- render_heartbeat ---

def test_heartbeat_no_ansi():
    text = render_heartbeat(StatusData(rtsp_status="已连接", frames_analyzed=10))
    assert "\033[" not in text
    assert "心跳" in text
    assert "已连接" in text


def test_heartbeat_contains_evidence_size():
    data = StatusData(evidence_size_mb=12.5)
    text = render_heartbeat(data)
    assert "12.5MB" in text


# --- TTY / 非 TTY 分支 ---

def test_panel_tty_mode_when_stderr_is_tty():
    fake_stderr = io.StringIO()
    fake_stderr.isatty = lambda: True
    with patch("src.status_panel.sys.stderr", fake_stderr):
        with patch("src.status_panel.is_tty", return_value=True):
            panel = StatusPanel(StatusData())
            assert panel.tty_mode is True


def test_panel_non_tty_mode_when_stderr_is_not_tty():
    fake_stderr = io.StringIO()
    fake_stderr.isatty = lambda: False
    with patch("src.status_panel.sys.stderr", fake_stderr):
        with patch("src.status_panel.is_tty", return_value=False):
            panel = StatusPanel(StatusData())
            assert panel.tty_mode is False


def test_non_tty_draw_outputs_no_ansi():
    """非 TTY 模式下 _draw 输出不含 ANSI 控制字符。"""
    buf = io.StringIO()
    data = StatusData(camera_name="电梯厅", rtsp_status="已连接")

    with patch("src.status_panel.is_tty", return_value=False):
        panel = StatusPanel(data)
    with patch("src.status_panel.sys.stderr", buf):
        panel._draw()

    output = buf.getvalue()
    assert "\033[" not in output
    assert "心跳" in output


# --- 证据占用初始值 ---

def test_evidence_size_can_be_initialized():
    """StatusData 可以在创建时传入非零的证据占用。"""
    data = StatusData(evidence_size_mb=33.7)
    assert data.evidence_size_mb == 33.7
    assert "33.7 MB" in render_status(data)
