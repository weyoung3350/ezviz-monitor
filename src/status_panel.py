"""终端状态栏：持续刷新的运行状态面板。

使用纯 ANSI 转义序列在终端底部重绘固定区域，不引入新依赖。
monitor 主循环只需更新 StatusData，StatusPanel 负责渲染。

TTY 降级：当 stderr 不是 TTY 时，不输出任何 ANSI 控制字符，
改为低频纯文本心跳（每 30 秒一行）。
"""

import sys
import time
import threading
from dataclasses import dataclass, field


def is_tty() -> bool:
    """检查 stderr 是否是 TTY。"""
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


@dataclass
class StatusData:
    """主循环持续更新的状态数据。所有字段线程安全地通过赋值更新。"""
    camera_name: str = ""
    rule_name: str = ""
    start_time: float = field(default_factory=time.monotonic)
    rtsp_status: str = "未连接"
    last_analysis_time: str = "-"
    last_identity: str = "-"
    last_alert_time: str = "-"
    phone_status: str = "-"
    evidence_size_mb: float = 0.0
    frames_analyzed: int = 0


def format_duration(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def render_status(data: StatusData) -> str:
    """根据 StatusData 生成状态栏文本（不含 ANSI 控制码）。"""
    elapsed = format_duration(time.monotonic() - data.start_time)
    ev_size = f"{data.evidence_size_mb:.1f} MB"

    lines = [
        "─" * 58,
        f"  摄像头: {data.camera_name:<12}  规则: {data.rule_name}",
        f"  运行时长: {elapsed}        RTSP: {data.rtsp_status}",
        f"  已分析帧: {data.frames_analyzed:<10}  最近分析: {data.last_analysis_time}",
        f"  最近识别: {data.last_identity}",
        f"  最近告警: {data.last_alert_time:<20}  电话: {data.phone_status}",
        f"  证据占用: {ev_size}",
        "─" * 58,
        "  按 Ctrl+C 退出",
    ]
    return "\n".join(lines)


def render_heartbeat(data: StatusData) -> str:
    """非 TTY 模式下的单行心跳文本（无 ANSI）。"""
    elapsed = format_duration(time.monotonic() - data.start_time)
    return (
        f"[心跳] {elapsed} | RTSP={data.rtsp_status} | "
        f"帧={data.frames_analyzed} | 识别={data.last_identity} | "
        f"告警={data.last_alert_time} | 证据={data.evidence_size_mb:.1f}MB"
    )


class StatusPanel:
    """后台线程驱动的终端状态面板。

    TTY 模式：每秒 ANSI 原地刷新。
    非 TTY 模式：每 30 秒输出一行纯文本心跳。
    """

    def __init__(self, data: StatusData, interval: float = 1.0) -> None:
        self._data = data
        self._tty = is_tty()
        self._interval = interval if self._tty else 30.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lines_printed = 0

    @property
    def tty_mode(self) -> bool:
        return self._tty

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._draw()
            self._stop.wait(self._interval)

    def _draw(self) -> None:
        if self._tty:
            self._draw_ansi()
        else:
            self._draw_heartbeat()

    def _draw_ansi(self) -> None:
        text = render_status(self._data)
        lines = text.split("\n")
        n = len(lines)

        if self._lines_printed > 0:
            sys.stderr.write(f"\033[{self._lines_printed}A")

        for line in lines:
            sys.stderr.write(f"\033[2K{line}\n")

        sys.stderr.flush()
        self._lines_printed = n

    def _draw_heartbeat(self) -> None:
        text = render_heartbeat(self._data)
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
