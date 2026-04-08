from datetime import datetime, timedelta

from src.alerts import AlertCooldown


def test_first_trigger_always_allowed():
    cooldown = AlertCooldown(minutes=5)
    now = datetime(2026, 4, 8, 23, 0)
    assert cooldown.should_trigger("大门", now)


def test_cooldown_blocks_within_period():
    cooldown = AlertCooldown(minutes=5)
    now = datetime(2026, 4, 8, 23, 0)

    assert cooldown.should_trigger("大门", now)
    cooldown.record("大门", now)
    assert not cooldown.should_trigger("大门", now + timedelta(minutes=1))
    assert not cooldown.should_trigger("大门", now + timedelta(minutes=4))


def test_cooldown_allows_after_period():
    cooldown = AlertCooldown(minutes=5)
    now = datetime(2026, 4, 8, 23, 0)

    cooldown.record("大门", now)
    assert cooldown.should_trigger("大门", now + timedelta(minutes=5))
    assert cooldown.should_trigger("大门", now + timedelta(minutes=10))


def test_cooldown_is_per_camera():
    cooldown = AlertCooldown(minutes=5)
    now = datetime(2026, 4, 8, 23, 0)

    assert cooldown.should_trigger("大门", now)
    cooldown.record("大门", now)
    assert not cooldown.should_trigger("大门", now + timedelta(minutes=1))
    assert cooldown.should_trigger("客厅", now + timedelta(minutes=1))


def test_multiple_records():
    cooldown = AlertCooldown(minutes=5)
    t1 = datetime(2026, 4, 8, 23, 0)
    t2 = t1 + timedelta(minutes=6)

    cooldown.record("大门", t1)
    assert cooldown.should_trigger("大门", t2)
    cooldown.record("大门", t2)
    assert not cooldown.should_trigger("大门", t2 + timedelta(minutes=3))
    assert cooldown.should_trigger("大门", t2 + timedelta(minutes=5))
