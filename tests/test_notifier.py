from src.notifier import format_alert_message


def test_format_alert_message_includes_alert_prefix():
    message = format_alert_message(
        camera_name="客厅",
        event_time="2026-04-08 23:15:02",
        evidence_path="./evidence/客厅/2026-04-08_23-15-02_clip.mp4",
    )
    assert "[告警]" in message


def test_format_alert_message_includes_camera_name():
    message = format_alert_message(
        camera_name="客厅",
        event_time="2026-04-08 23:15:02",
        evidence_path="./evidence/客厅/2026-04-08_23-15-02_clip.mp4",
    )
    assert "客厅" in message


def test_format_alert_message_includes_timestamp():
    message = format_alert_message(
        camera_name="大门",
        event_time="2026-04-08 23:15:02",
        evidence_path="./evidence/大门/2026-04-08_23-15-02_clip.mp4",
    )
    assert "2026-04-08 23:15:02" in message


def test_format_alert_message_includes_evidence_path():
    path = "./evidence/客厅/2026-04-08_23-15-02_clip.mp4"
    message = format_alert_message(
        camera_name="客厅",
        event_time="2026-04-08 23:15:02",
        evidence_path=path,
    )
    assert path in message


def test_format_alert_message_is_visually_distinct():
    message = format_alert_message(
        camera_name="客厅",
        event_time="2026-04-08 23:15:02",
        evidence_path="./evidence/客厅/2026-04-08_23-15-02_clip.mp4",
    )
    # 告警文本应有分隔线或醒目标记
    assert "=" in message or "!" in message or "★" in message or "▶" in message
