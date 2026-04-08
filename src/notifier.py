import logging
import sys

logger = logging.getLogger(__name__)

_SEPARATOR = "=" * 60


def format_alert_message(
    camera_name: str,
    event_time: str,
    evidence_path: str,
) -> str:
    return (
        f"\n{_SEPARATOR}\n"
        f"  [告警] 检测到陌生人!\n"
        f"  摄像头: {camera_name}\n"
        f"  时间:   {event_time}\n"
        f"  证据:   {evidence_path}\n"
        f"{_SEPARATOR}"
    )


def print_alert(
    camera_name: str,
    event_time: str,
    evidence_path: str,
) -> None:
    message = format_alert_message(camera_name, event_time, evidence_path)
    print(message, file=sys.stderr, flush=True)
    logger.warning("陌生人告警: 摄像头=%s 时间=%s 证据=%s", camera_name, event_time, evidence_path)
