import logging
import sys

logger = logging.getLogger(__name__)

_SEPARATOR = "=" * 60


def format_alert_message(
    camera_name: str,
    event_time: str,
    person_name: str,
    rule_name: str,
    evidence_path: str,
    phone_result: str,
) -> str:
    return (
        f"\n{_SEPARATOR}\n"
        f"  [告警] 目标人物出现!\n"
        f"  人物:   {person_name}\n"
        f"  规则:   {rule_name}\n"
        f"  摄像头: {camera_name}\n"
        f"  时间:   {event_time}\n"
        f"  证据:   {evidence_path}\n"
        f"  电话:   {phone_result}\n"
        f"{_SEPARATOR}"
    )


def print_alert(
    camera_name: str,
    event_time: str,
    person_name: str,
    rule_name: str,
    evidence_path: str,
    phone_result: str,
) -> None:
    message = format_alert_message(
        camera_name, event_time, person_name, rule_name, evidence_path, phone_result,
    )
    print(message, file=sys.stderr, flush=True)
    logger.warning(
        "目标告警: 人物=%s 规则=%s 摄像头=%s 时间=%s 证据=%s 电话=%s",
        person_name, rule_name, camera_name, event_time, evidence_path, phone_result,
    )
