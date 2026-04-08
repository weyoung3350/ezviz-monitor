from datetime import datetime

from src.scheduler import is_in_schedule


def test_normal_schedule():
    schedules = [{"start": "09:00", "end": "17:00"}]
    assert is_in_schedule(schedules, datetime(2026, 4, 8, 12, 0))
    assert not is_in_schedule(schedules, datetime(2026, 4, 8, 8, 0))
    assert not is_in_schedule(schedules, datetime(2026, 4, 8, 18, 0))


def test_cross_day_schedule():
    schedules = [{"start": "22:00", "end": "07:00"}]
    assert is_in_schedule(schedules, datetime(2026, 4, 8, 23, 30))
    assert is_in_schedule(schedules, datetime(2026, 4, 9, 6, 30))
    assert not is_in_schedule(schedules, datetime(2026, 4, 8, 12, 0))


def test_all_day_schedule():
    schedules = [{"start": "00:00", "end": "00:00"}]
    assert is_in_schedule(schedules, datetime(2026, 4, 8, 0, 0))
    assert is_in_schedule(schedules, datetime(2026, 4, 8, 12, 0))
    assert is_in_schedule(schedules, datetime(2026, 4, 8, 23, 59))


def test_not_in_any_schedule():
    schedules = [
        {"start": "09:00", "end": "12:00"},
        {"start": "14:00", "end": "18:00"},
    ]
    assert not is_in_schedule(schedules, datetime(2026, 4, 8, 13, 0))
    assert is_in_schedule(schedules, datetime(2026, 4, 8, 10, 0))
    assert is_in_schedule(schedules, datetime(2026, 4, 8, 15, 0))


def test_empty_schedules():
    assert not is_in_schedule([], datetime(2026, 4, 8, 12, 0))


def test_boundary_times():
    schedules = [{"start": "09:00", "end": "17:00"}]
    assert is_in_schedule(schedules, datetime(2026, 4, 8, 9, 0))
    assert not is_in_schedule(schedules, datetime(2026, 4, 8, 17, 0))
