from src.notifier import format_alert_message


_COMMON_ARGS = dict(
    camera_name="电梯厅",
    event_time="2026-04-08 23:15:02",
    person_name="杨孝治",
    rule_name="杨孝治夜间外出监护",
    evidence_path="./evidence/电梯厅/2026-04-08_23-15-02_clip.mp4",
    phone_result="拨打成功",
)


def test_includes_alert_prefix():
    msg = format_alert_message(**_COMMON_ARGS)
    assert "[告警]" in msg


def test_includes_person_name():
    msg = format_alert_message(**_COMMON_ARGS)
    assert "杨孝治" in msg


def test_includes_rule_name():
    msg = format_alert_message(**_COMMON_ARGS)
    assert "杨孝治夜间外出监护" in msg


def test_includes_camera_name():
    msg = format_alert_message(**_COMMON_ARGS)
    assert "电梯厅" in msg


def test_includes_timestamp():
    msg = format_alert_message(**_COMMON_ARGS)
    assert "2026-04-08 23:15:02" in msg


def test_includes_evidence_path():
    msg = format_alert_message(**_COMMON_ARGS)
    assert "./evidence/电梯厅/2026-04-08_23-15-02_clip.mp4" in msg


def test_includes_phone_result():
    msg = format_alert_message(**_COMMON_ARGS)
    assert "拨打成功" in msg


def test_is_visually_distinct():
    msg = format_alert_message(**_COMMON_ARGS)
    assert "=" in msg
