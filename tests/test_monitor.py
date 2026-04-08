from src.monitor import should_alert_for_detection


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
