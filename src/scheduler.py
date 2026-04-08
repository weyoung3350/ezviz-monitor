from datetime import datetime, time


def _parse_time(s: str) -> time:
    parts = s.strip().split(":")
    return time(int(parts[0]), int(parts[1]))


def _schedule_matches(schedule: dict, now_time: time) -> bool:
    start = _parse_time(schedule["start"])
    end = _parse_time(schedule["end"])

    # start == end 视为全天生效
    if start == end:
        return True

    # 跨天时段
    if start > end:
        return now_time >= start or now_time < end

    # 普通时段
    return start <= now_time < end


def is_in_schedule(schedules: list[dict], now: datetime) -> bool:
    return any(_schedule_matches(s, now.time()) for s in schedules)
