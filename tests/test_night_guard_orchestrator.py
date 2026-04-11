"""NightGuardOrchestrator 单元测试。

TDD 节奏：先写测试 → 跑测试确认失败 → 写最小实现 → 确认通过。

测试分层：
  - 纯函数：is_in_alert_window / should_alert / build_* / format_*
  - App 方法：_guard_enabled / _check_window / _check_cooldown / _update_cooldown /
             _fire_first_alert / _run_snapshot_loop / _fire_snapshot_notification /
             _fire_detail_fallback
  - 主入口：on_door_unlock_trigger 各分支 + 并发 race 覆盖
"""

import asyncio
import inspect
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock

import pytest

from night_guard_orchestrator import (
    NightGuardOrchestrator,
    build_detail_message,
    build_first_alert_message,
    build_photo_caption,
    build_snapshot_path,
    build_time_display,
    build_timestamp_tag,
    format_door_confirmation,
    is_in_alert_window,
    should_alert,
)


class TestIsInAlertWindow:
    """时段判断纯函数，支持跨天 [start, end) 半开区间。"""

    def test_non_crossing_inside(self):
        assert is_in_alert_window(time(9, 0), time(17, 0), time(12, 0)) is True

    def test_non_crossing_start_boundary(self):
        assert is_in_alert_window(time(9, 0), time(17, 0), time(9, 0)) is True

    def test_non_crossing_end_boundary_exclusive(self):
        assert is_in_alert_window(time(9, 0), time(17, 0), time(17, 0)) is False

    def test_non_crossing_outside(self):
        assert is_in_alert_window(time(9, 0), time(17, 0), time(8, 0)) is False

    def test_crossing_after_start(self):
        assert is_in_alert_window(time(23, 0), time(7, 30), time(23, 30)) is True

    def test_crossing_before_end(self):
        assert is_in_alert_window(time(23, 0), time(7, 30), time(1, 37)) is True

    def test_crossing_midday_outside(self):
        assert is_in_alert_window(time(23, 0), time(7, 30), time(12, 0)) is False

    def test_crossing_end_boundary_exclusive(self):
        assert is_in_alert_window(time(23, 0), time(7, 30), time(7, 30)) is False

    def test_empty_window(self):
        assert is_in_alert_window(time(12, 0), time(12, 0), time(12, 0)) is False


class TestShouldAlert:
    """冷却判断纯函数。None 视为从未告警。"""

    def test_none_last_alert(self):
        assert should_alert(None, timedelta(seconds=300), datetime(2026, 4, 11, 1, 0)) is True

    def test_within_cooldown(self):
        last = datetime(2026, 4, 11, 1, 0)
        now = last + timedelta(seconds=100)
        assert should_alert(last, timedelta(seconds=300), now) is False

    def test_exactly_cooldown(self):
        last = datetime(2026, 4, 11, 1, 0)
        now = last + timedelta(seconds=300)
        assert should_alert(last, timedelta(seconds=300), now) is True

    def test_past_cooldown(self):
        last = datetime(2026, 4, 11, 1, 0)
        now = last + timedelta(seconds=301)
        assert should_alert(last, timedelta(seconds=300), now) is True


class TestTimestampHelpers:
    def test_build_timestamp_tag(self):
        dt = datetime(2026, 4, 11, 1, 37, 33)
        assert build_timestamp_tag(dt) == "20260411_013733"

    def test_build_time_display(self):
        dt = datetime(2026, 4, 11, 1, 37, 33)
        assert build_time_display(dt) == "01:37:33"


class TestSnapshotPath:
    def test_build_snapshot_path_without_trailing_slash(self):
        path = build_snapshot_path(
            "night_alert_{timestamp}_{index}.jpg",
            "/config/www",
            "20260411_013733",
            3,
        )
        assert path == "/config/www/night_alert_20260411_013733_3.jpg"

    def test_build_snapshot_path_with_trailing_slash(self):
        path = build_snapshot_path(
            "night_alert_{timestamp}_{index}.jpg",
            "/config/www/",
            "20260411_013733",
            3,
        )
        assert path == "/config/www/night_alert_20260411_013733_3.jpg"


class TestDoorConfirmation:
    def test_ever_opened(self):
        text = format_door_confirmation(True, "已开锁", "已上锁")
        assert text == "已确认开门（已开锁）"

    def test_never_opened(self):
        text = format_door_confirmation(False, "", "已上锁")
        assert text == "未确认开门（当前：已上锁）"


class TestFirstAlertMessage:
    def test_basic(self):
        msg = build_first_alert_message("01:37:33")
        assert "时间：01:37:33" in msg
        assert "门内按钮开锁" in msg
        assert "正在抓拍" in msg


class TestPhotoCaption:
    def test_basic(self):
        caption = build_photo_caption("01:37:33", "已确认开门（已开锁）", 5)
        assert "时间：01:37:33" in caption
        assert "已确认开门" in caption
        assert "抓拍：5 次尝试" in caption


class TestDetailMessage:
    def test_all_failed(self):
        msg = build_detail_message("01:37:33", "未确认开门（当前：已上锁）", 5, False, False)
        assert "全部抓拍失败" in msg

    def test_photo_attempted(self):
        msg = build_detail_message("01:37:33", "已确认开门（已开锁）", 5, True, True)
        assert "已尝试发送图片" in msg
        assert "全部抓拍失败" not in msg

    def test_has_snapshot_no_photo_attempted(self):
        msg = build_detail_message("01:37:33", "已确认开门（已开锁）", 5, True, False)
        assert "已尝试发送图片" not in msg
        assert "全部抓拍失败" not in msg


# ══════════════════════════════════════════════════════════
#  App 类：smoke test + initialize
# ══════════════════════════════════════════════════════════


def test_orchestrator_class_signature():
    """Smoke test：验证 NightGuardOrchestrator 类存在关键方法且核心是 coroutine。

    这是对 MagicMock(spec=...) 的补充，防止 mock 对 AppDaemon API 约束过弱导致运行时报错。
    """
    assert hasattr(NightGuardOrchestrator, "initialize")
    assert hasattr(NightGuardOrchestrator, "on_door_unlock_trigger")
    assert hasattr(NightGuardOrchestrator, "_guard_enabled")
    assert hasattr(NightGuardOrchestrator, "_check_window")
    assert hasattr(NightGuardOrchestrator, "_check_cooldown")
    assert hasattr(NightGuardOrchestrator, "_update_cooldown")
    assert hasattr(NightGuardOrchestrator, "_fire_first_alert")
    assert hasattr(NightGuardOrchestrator, "_run_snapshot_loop")
    assert hasattr(NightGuardOrchestrator, "_fire_snapshot_notification")
    assert hasattr(NightGuardOrchestrator, "_fire_detail_fallback")
    # 核心 async 方法必须是 coroutine
    assert inspect.iscoroutinefunction(NightGuardOrchestrator.initialize)
    assert inspect.iscoroutinefunction(NightGuardOrchestrator.on_door_unlock_trigger)


async def test_initialize_registers_listener(mock_hass_app):
    """调用真实 initialize 逻辑，验证 listen_event 被正确注册、并发锁初始化。"""
    mock_hass_app.args = {
        "camera_entity": "camera.test_cam",
        "door_state_entity": "sensor.test_door",
        "snapshot_count": 3,
        "snapshot_interval_seconds": 1,
        "cooldown_seconds": 300,
        "helper_enabled": "input_boolean.test_enabled",
        "helper_window_start": "input_datetime.test_start",
        "helper_window_end": "input_datetime.test_end",
        "helper_last_alert": "input_datetime.test_last",
    }

    await NightGuardOrchestrator.initialize(mock_hass_app)

    mock_hass_app.listen_event.assert_called_once()
    args, _ = mock_hass_app.listen_event.call_args
    assert args[1] == "night_guard.door_unlock_trigger"
    assert mock_hass_app.camera_entity == "camera.test_cam"
    assert mock_hass_app.cooldown.total_seconds() == 300
    # 并发保护设施
    assert isinstance(mock_hass_app._trigger_lock, asyncio.Lock)
    assert mock_hass_app._in_process_last_alert is None


# ══════════════════════════════════════════════════════════
#  _guard_enabled
# ══════════════════════════════════════════════════════════


async def test_guard_enabled_on(mock_hass_app):
    mock_hass_app.get_state = AsyncMock(return_value="on")
    result = await NightGuardOrchestrator._guard_enabled(mock_hass_app)
    assert result is True
    mock_hass_app.get_state.assert_called_once_with("input_boolean.test_enabled")


async def test_guard_enabled_off(mock_hass_app):
    mock_hass_app.get_state = AsyncMock(return_value="off")
    result = await NightGuardOrchestrator._guard_enabled(mock_hass_app)
    assert result is False


async def test_guard_enabled_unavailable_defaults_to_true(mock_hass_app):
    """helper 不可用时默认放行，防止因 helper 故障漏报。"""
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    result = await NightGuardOrchestrator._guard_enabled(mock_hass_app)
    assert result is True


async def test_guard_enabled_none_defaults_to_true(mock_hass_app):
    mock_hass_app.get_state = AsyncMock(return_value=None)
    result = await NightGuardOrchestrator._guard_enabled(mock_hass_app)
    assert result is True


# ══════════════════════════════════════════════════════════
#  _check_window
# ══════════════════════════════════════════════════════════


async def test_check_window_inside(mock_hass_app):
    async def fake_get_state(entity):
        if "start" in entity:
            return "23:00:00"
        if "end" in entity:
            return "07:30:00"
        return None

    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)
    result = await NightGuardOrchestrator._check_window(mock_hass_app, time(1, 37))
    assert result is True


async def test_check_window_outside(mock_hass_app):
    async def fake_get_state(entity):
        if "start" in entity:
            return "23:00:00"
        if "end" in entity:
            return "07:30:00"
        return None

    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)
    result = await NightGuardOrchestrator._check_window(mock_hass_app, time(12, 0))
    assert result is False


async def test_check_window_helper_unavailable_defaults_to_true(mock_hass_app):
    """helper 不可用时默认放行。"""
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    result = await NightGuardOrchestrator._check_window(mock_hass_app, time(12, 0))
    assert result is True


async def test_check_window_handles_time_only_format(mock_hass_app):
    """HH:MM 格式（无秒）也应正确解析。"""
    async def fake_get_state(entity):
        if "start" in entity:
            return "23:00"
        if "end" in entity:
            return "07:30"
        return None

    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)
    result = await NightGuardOrchestrator._check_window(mock_hass_app, time(0, 30))
    assert result is True


# ══════════════════════════════════════════════════════════
#  _check_cooldown / _update_cooldown
# ══════════════════════════════════════════════════════════


async def test_check_cooldown_never_alerted(mock_hass_app):
    mock_hass_app.get_state = AsyncMock(return_value="unknown")
    mock_hass_app._in_process_last_alert = None
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 0)
    )
    assert result is True


async def test_check_cooldown_within(mock_hass_app):
    mock_hass_app.get_state = AsyncMock(return_value="2026-04-11 00:58:00")
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 0)
    )
    assert result is False  # 距今 120 秒 < 300 秒冷却


async def test_check_cooldown_past(mock_hass_app):
    mock_hass_app.get_state = AsyncMock(return_value="2026-04-11 00:50:00")
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 0)
    )
    assert result is True  # 距今 600 秒


async def test_update_cooldown_calls_service_and_writes_process_last(mock_hass_app):
    now = datetime(2026, 4, 11, 1, 37, 33)
    mock_hass_app._in_process_last_alert = None
    await NightGuardOrchestrator._update_cooldown(mock_hass_app, now)
    mock_hass_app.call_service.assert_called_once()
    call_args = mock_hass_app.call_service.call_args
    assert call_args[0][0] == "input_datetime/set_datetime"
    assert call_args[1]["entity_id"] == "input_datetime.test_last"
    # 进程内兜底同时被更新
    assert mock_hass_app._in_process_last_alert == now


async def test_update_cooldown_helper_failure_does_not_raise(mock_hass_app):
    """Codex R5 blocking 修复验证: call_service 抛异常时 _update_cooldown 必须吞掉异常，
    否则会中断上层 on_door_unlock_trigger 流程导致主告警不发（但冷却已生效，构成漏报）。

    预期行为：
    - 不向上抛异常
    - 进程内 _in_process_last_alert 仍然被设置（作为兜底冷却）
    - log WARNING 记录 helper 写入失败
    """
    now = datetime(2026, 4, 11, 1, 37, 33)
    mock_hass_app._in_process_last_alert = None
    mock_hass_app.call_service = AsyncMock(
        side_effect=RuntimeError("HA service unavailable")
    )

    # 必须不抛异常
    await NightGuardOrchestrator._update_cooldown(mock_hass_app, now)

    # 进程内兜底仍被设置
    assert mock_hass_app._in_process_last_alert == now
    # WARNING 日志被记录（只断言调用过 log，具体参数 level 由调用方传）
    assert mock_hass_app.log.called


async def test_on_trigger_continues_alert_when_cooldown_helper_fails(trigger_ready_app):
    """Codex R5 blocking 修复的集成验证: 即使 HA helper 写入失败，
    主告警和后续流程仍应继续执行（不漏报）。"""
    trigger_ready_app._guard_enabled = AsyncMock(return_value=True)
    trigger_ready_app._check_window = AsyncMock(return_value=True)
    trigger_ready_app._check_cooldown = AsyncMock(return_value=True)
    # _update_cooldown 走真实逻辑，其内部 call_service 被 mock 成抛异常
    trigger_ready_app._update_cooldown = (
        NightGuardOrchestrator._update_cooldown.__get__(trigger_ready_app)
    )
    trigger_ready_app.call_service = AsyncMock(
        side_effect=RuntimeError("HA service unavailable")
    )
    trigger_ready_app._fire_first_alert = AsyncMock()
    trigger_ready_app._run_snapshot_loop = AsyncMock(return_value={
        "last_successful_snapshot": "",
        "door_ever_opened": False,
        "door_opened_state": "",
        "last_door_state": "已上锁",
    })
    trigger_ready_app._fire_snapshot_notification = AsyncMock(return_value=False)
    trigger_ready_app._fire_detail_fallback = AsyncMock()

    # 不应抛异常
    await NightGuardOrchestrator.on_door_unlock_trigger(
        trigger_ready_app, "event", {"source": "test"}, {}
    )

    # 主告警必须被发出（不漏报）
    trigger_ready_app._fire_first_alert.assert_called_once()
    trigger_ready_app._run_snapshot_loop.assert_called_once()
    trigger_ready_app._fire_detail_fallback.assert_called_once()
    # 进程内兜底冷却仍被设置（下次进入会拦截）
    assert trigger_ready_app._in_process_last_alert is not None


async def test_check_cooldown_helper_unavailable_no_in_process(mock_hass_app):
    """helper 不可用 + 进程内兜底为空 → 放行。"""
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    mock_hass_app._in_process_last_alert = None
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 37, 33)
    )
    assert result is True


async def test_check_cooldown_helper_unavailable_in_process_active(mock_hass_app):
    """helper 不可用 + 进程内兜底在冷却期 → 拦截。"""
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    mock_hass_app._in_process_last_alert = datetime(2026, 4, 11, 1, 35, 0)
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 37, 0)
    )
    assert result is False  # 距今 120 秒 < 300 秒冷却


async def test_check_cooldown_helper_unavailable_in_process_past(mock_hass_app):
    """helper 不可用 + 进程内兜底已过冷却 → 放行。"""
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    mock_hass_app._in_process_last_alert = datetime(2026, 4, 11, 1, 30, 0)
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 37, 0)
    )
    assert result is True  # 距今 420 秒 > 300 秒冷却


# ══════════════════════════════════════════════════════════
#  _fire_first_alert
# ══════════════════════════════════════════════════════════


async def test_fire_first_alert_correct_event_data(mock_hass_app):
    await NightGuardOrchestrator._fire_first_alert(
        mock_hass_app, "20260411_013733", "01:37:33"
    )
    mock_hass_app.fire_event.assert_called_once()
    call_args = mock_hass_app.fire_event.call_args
    assert call_args[0][0] == "notify_service_request"
    kwargs = call_args[1]
    assert kwargs["channel"] == "all"
    assert kwargs["force_sound"] is True
    assert kwargs["request_id"] == "night_unlock_20260411_013733"
    assert kwargs["source"] == "night_guard.orchestrator"
    assert "01:37:33" in kwargs["message"]
    assert kwargs["title"] == "夜间门内开锁告警"
    assert kwargs["phone_alert_name"] == "夜间门内开锁"


# ══════════════════════════════════════════════════════════
#  _run_snapshot_loop
# ══════════════════════════════════════════════════════════


async def test_snapshot_loop_all_available(mock_hass_app, monkeypatch):
    """摄像头全程 available，门状态中段出现'已开锁'。"""
    door_sequence = ["已上锁", "已上锁", "已开锁"]
    call_count = {"door": 0}

    async def fake_get_state(entity):
        if entity == "camera.test_cam":
            return "streaming"
        if entity == "sensor.test_door":
            idx = call_count["door"]
            call_count["door"] += 1
            return door_sequence[min(idx, len(door_sequence) - 1)]
        return None

    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)
    mock_hass_app.call_service = AsyncMock()

    # 屏蔽 asyncio.sleep，加快测试
    import night_guard_orchestrator as _ngo
    monkeypatch.setattr(_ngo.asyncio, "sleep", AsyncMock())

    result = await NightGuardOrchestrator._run_snapshot_loop(
        mock_hass_app, "20260411_013733"
    )

    assert result["last_successful_snapshot"].endswith("_3.jpg")
    assert result["door_ever_opened"] is True
    assert result["door_opened_state"] == "已开锁"
    assert result["last_door_state"] == "已开锁"
    assert mock_hass_app.call_service.call_count == 3  # snapshot_count


async def test_snapshot_loop_camera_unavailable(mock_hass_app, monkeypatch):
    """摄像头全程 unavailable → 返回空路径 + 最后门状态。"""
    async def fake_get_state(entity):
        if entity == "camera.test_cam":
            return "unavailable"
        return "已上锁"

    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)
    mock_hass_app.call_service = AsyncMock()

    import night_guard_orchestrator as _ngo
    monkeypatch.setattr(_ngo.asyncio, "sleep", AsyncMock())

    result = await NightGuardOrchestrator._run_snapshot_loop(
        mock_hass_app, "20260411_013733"
    )

    assert result["last_successful_snapshot"] == ""
    assert result["door_ever_opened"] is False
    assert result["last_door_state"] == "已上锁"


async def test_snapshot_loop_snapshot_service_raises(mock_hass_app, monkeypatch):
    """camera.snapshot 服务调用抛异常不应中断循环，last_successful_snapshot 仍有值。"""
    async def fake_call_service(service, **kwargs):
        raise RuntimeError("snapshot failed")

    async def fake_get_state(entity):
        if entity == "camera.test_cam":
            return "streaming"
        return "已上锁"

    mock_hass_app.call_service = AsyncMock(side_effect=fake_call_service)
    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)

    import night_guard_orchestrator as _ngo
    monkeypatch.setattr(_ngo.asyncio, "sleep", AsyncMock())

    result = await NightGuardOrchestrator._run_snapshot_loop(
        mock_hass_app, "20260411_013733"
    )

    # 调用 3 次都异常，但摄像头 state 正常，所以仍记录最后一张候选路径
    assert result["last_successful_snapshot"].endswith("_3.jpg")
    assert mock_hass_app.call_service.call_count == 3


async def test_snapshot_loop_get_state_raises(mock_hass_app, monkeypatch):
    """Codex R5 改进: get_state 抛异常不应中断循环，循环必须跑完所有轮次。

    边界条件：HA 偶发 RPC 故障导致 get_state 抛 ConnectionError 等异常，
    旧实现会让整条循环崩溃，后续快照通知 + 详情兜底全部丢失。新实现要把
    get_state 也用 try/except 包起来，异常时记 WARNING 但继续循环。
    """
    async def fake_call_service(service, **kwargs):
        return None  # snapshot 成功

    async def fake_get_state(entity):
        raise ConnectionError("HA RPC unavailable")

    mock_hass_app.call_service = AsyncMock(side_effect=fake_call_service)
    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)

    import night_guard_orchestrator as _ngo
    monkeypatch.setattr(_ngo.asyncio, "sleep", AsyncMock())

    # 不应抛异常
    result = await NightGuardOrchestrator._run_snapshot_loop(
        mock_hass_app, "20260411_013733"
    )

    # snapshot call 全部跑完
    assert mock_hass_app.call_service.call_count == 3
    # 因 get_state 异常，无法判断摄像头可用性，last_successful_snapshot 保持空
    assert result["last_successful_snapshot"] == ""
    # 门状态读取也失败，door_ever_opened 维持 False
    assert result["door_ever_opened"] is False
    assert result["last_door_state"] == ""


# ══════════════════════════════════════════════════════════
#  _fire_snapshot_notification / _fire_detail_fallback
# ══════════════════════════════════════════════════════════


async def test_fire_snapshot_notification_with_snapshot(mock_hass_app):
    snapshot_result = {
        "last_successful_snapshot": "/config/www/night_alert_20260411_013733_5.jpg",
        "door_ever_opened": True,
        "door_opened_state": "已开锁",
        "last_door_state": "已开锁",
    }
    photo_attempted = await NightGuardOrchestrator._fire_snapshot_notification(
        mock_hass_app, "20260411_013733", "01:37:33", snapshot_result
    )
    assert photo_attempted is True
    mock_hass_app.fire_event.assert_called_once()
    kwargs = mock_hass_app.fire_event.call_args[1]
    assert kwargs["channel"] == ["dingtalk", "ios_push"]
    assert kwargs["image_path"] == "/config/www/night_alert_20260411_013733_5.jpg"
    assert kwargs["request_id"] == "night_unlock_photo_20260411_013733"
    assert kwargs["force_sound"] is False
    assert "已确认开门" in kwargs["message"]


async def test_fire_snapshot_notification_no_snapshot(mock_hass_app):
    snapshot_result = {
        "last_successful_snapshot": "",
        "door_ever_opened": False,
        "door_opened_state": "",
        "last_door_state": "已上锁",
    }
    photo_attempted = await NightGuardOrchestrator._fire_snapshot_notification(
        mock_hass_app, "20260411_013733", "01:37:33", snapshot_result
    )
    assert photo_attempted is False
    mock_hass_app.fire_event.assert_not_called()


async def test_fire_detail_fallback_with_snapshot(mock_hass_app):
    snapshot_result = {
        "last_successful_snapshot": "/config/www/foo.jpg",
        "door_ever_opened": True,
        "door_opened_state": "已开锁",
        "last_door_state": "已开锁",
    }
    await NightGuardOrchestrator._fire_detail_fallback(
        mock_hass_app, "20260411_013733", "01:37:33", snapshot_result, True
    )
    mock_hass_app.fire_event.assert_called_once()
    kwargs = mock_hass_app.fire_event.call_args[1]
    assert kwargs["channel"] == ["dingtalk", "ios_push"]
    assert kwargs["request_id"] == "night_unlock_detail_20260411_013733"
    assert kwargs["force_sound"] is False
    assert "📋" in kwargs["message"] or "详情" in kwargs["message"]
    assert "已尝试发送图片" in kwargs["message"]


async def test_fire_detail_fallback_no_snapshot(mock_hass_app):
    snapshot_result = {
        "last_successful_snapshot": "",
        "door_ever_opened": False,
        "door_opened_state": "",
        "last_door_state": "已上锁",
    }
    await NightGuardOrchestrator._fire_detail_fallback(
        mock_hass_app, "20260411_013733", "01:37:33", snapshot_result, False
    )
    kwargs = mock_hass_app.fire_event.call_args[1]
    assert "全部抓拍失败" in kwargs["message"]


async def test_fire_detail_fallback_uses_last_door_state(mock_hass_app):
    """当 door_ever_opened=False 时，message 里的 current 状态应来自 last_door_state，
    而不是硬编码 'unknown'。这是 Codex R1 指出的 bug 修复验证。"""
    snapshot_result = {
        "last_successful_snapshot": "",
        "door_ever_opened": False,
        "door_opened_state": "",
        "last_door_state": "已上锁",
    }
    await NightGuardOrchestrator._fire_detail_fallback(
        mock_hass_app, "20260411_013733", "01:37:33", snapshot_result, False
    )
    kwargs = mock_hass_app.fire_event.call_args[1]
    assert "已上锁" in kwargs["message"]
    assert "unknown" not in kwargs["message"]


# ══════════════════════════════════════════════════════════
#  on_door_unlock_trigger 主入口（分支 + 并发）
# ══════════════════════════════════════════════════════════


@pytest.fixture
def trigger_ready_app(mock_hass_app):
    """给 mock_hass_app 注入真实 asyncio.Lock，方便 on_door_unlock_trigger 测试。"""
    mock_hass_app._trigger_lock = asyncio.Lock()
    mock_hass_app._in_process_last_alert = None
    return mock_hass_app


async def test_on_trigger_guard_disabled(trigger_ready_app):
    trigger_ready_app._guard_enabled = AsyncMock(return_value=False)
    trigger_ready_app._check_window = AsyncMock()
    trigger_ready_app._fire_first_alert = AsyncMock()

    await NightGuardOrchestrator.on_door_unlock_trigger(
        trigger_ready_app, "event", {"source": "test"}, {}
    )

    trigger_ready_app._guard_enabled.assert_called_once()
    trigger_ready_app._check_window.assert_not_called()
    trigger_ready_app._fire_first_alert.assert_not_called()


async def test_on_trigger_outside_window(trigger_ready_app):
    trigger_ready_app._guard_enabled = AsyncMock(return_value=True)
    trigger_ready_app._check_window = AsyncMock(return_value=False)
    trigger_ready_app._check_cooldown = AsyncMock()
    trigger_ready_app._fire_first_alert = AsyncMock()

    await NightGuardOrchestrator.on_door_unlock_trigger(
        trigger_ready_app, "event", {"source": "test"}, {}
    )

    trigger_ready_app._guard_enabled.assert_called_once()
    trigger_ready_app._check_window.assert_called_once()
    trigger_ready_app._check_cooldown.assert_not_called()
    trigger_ready_app._fire_first_alert.assert_not_called()


async def test_on_trigger_cooldown_active(trigger_ready_app):
    trigger_ready_app._guard_enabled = AsyncMock(return_value=True)
    trigger_ready_app._check_window = AsyncMock(return_value=True)
    trigger_ready_app._check_cooldown = AsyncMock(return_value=False)
    trigger_ready_app._update_cooldown = AsyncMock()
    trigger_ready_app._fire_first_alert = AsyncMock()

    await NightGuardOrchestrator.on_door_unlock_trigger(
        trigger_ready_app, "event", {"source": "test"}, {}
    )

    trigger_ready_app._guard_enabled.assert_called_once()
    trigger_ready_app._check_window.assert_called_once()
    trigger_ready_app._check_cooldown.assert_called_once()
    trigger_ready_app._update_cooldown.assert_not_called()
    trigger_ready_app._fire_first_alert.assert_not_called()


async def test_on_trigger_full_path(trigger_ready_app):
    trigger_ready_app._guard_enabled = AsyncMock(return_value=True)
    trigger_ready_app._check_window = AsyncMock(return_value=True)
    trigger_ready_app._check_cooldown = AsyncMock(return_value=True)
    trigger_ready_app._update_cooldown = AsyncMock()
    trigger_ready_app._fire_first_alert = AsyncMock()
    trigger_ready_app._run_snapshot_loop = AsyncMock(return_value={
        "last_successful_snapshot": "/config/www/foo.jpg",
        "door_ever_opened": True,
        "door_opened_state": "已开锁",
        "last_door_state": "已开锁",
    })
    trigger_ready_app._fire_snapshot_notification = AsyncMock(return_value=True)
    trigger_ready_app._fire_detail_fallback = AsyncMock()

    await NightGuardOrchestrator.on_door_unlock_trigger(
        trigger_ready_app, "event", {"source": "test"}, {}
    )

    trigger_ready_app._update_cooldown.assert_called_once()
    trigger_ready_app._fire_first_alert.assert_called_once()
    trigger_ready_app._run_snapshot_loop.assert_called_once()
    trigger_ready_app._fire_snapshot_notification.assert_called_once()
    trigger_ready_app._fire_detail_fallback.assert_called_once()


async def test_on_trigger_concurrent_second_blocked_by_cooldown(trigger_ready_app):
    """并发触发防御：使用真实 _check_cooldown + _update_cooldown 逻辑验证 race 被 Lock 覆盖。

    构造方式：
    - helper `last_alert` 返回 "unknown"，强制 _check_cooldown 走进程内兜底路径
    - 进程内初始 _in_process_last_alert = None，第一次会放行
    - Lock 保证 _update_cooldown 写入 _in_process_last_alert 之前第二次不能进入 _check_cooldown
    - 第二次进入时读到已被第一次写入的 _in_process_last_alert，真实 should_alert 判定冷却期内
    - 断言只有一次真正进入 _fire_first_alert

    如果去掉 asyncio.Lock，两个协程并发跑完 _check_cooldown（都拿到 True），然后两次都进入
    _update_cooldown + _fire_first_alert，断言会失败。这就是 race 被真正覆盖的证据。
    """
    # 不 mock 这 4 个方法，使用类的真实实现（描述符协议绑定）
    trigger_ready_app._guard_enabled = (
        NightGuardOrchestrator._guard_enabled.__get__(trigger_ready_app)
    )
    trigger_ready_app._check_window = (
        NightGuardOrchestrator._check_window.__get__(trigger_ready_app)
    )
    trigger_ready_app._check_cooldown = (
        NightGuardOrchestrator._check_cooldown.__get__(trigger_ready_app)
    )
    trigger_ready_app._update_cooldown = (
        NightGuardOrchestrator._update_cooldown.__get__(trigger_ready_app)
    )

    async def fake_get_state(entity):
        if entity == "input_boolean.test_enabled":
            return "on"
        if entity == "input_datetime.test_start":
            return "00:00:00"
        if entity == "input_datetime.test_end":
            return "23:59:00"
        if entity == "input_datetime.test_last":
            return "unknown"  # 触发进程内兜底
        return None

    trigger_ready_app.get_state = AsyncMock(side_effect=fake_get_state)
    trigger_ready_app.call_service = AsyncMock()

    # 下游方法 mock 掉，不跑真实抓拍
    trigger_ready_app._fire_first_alert = AsyncMock()
    trigger_ready_app._run_snapshot_loop = AsyncMock(return_value={
        "last_successful_snapshot": "",
        "door_ever_opened": False,
        "door_opened_state": "",
        "last_door_state": "已上锁",
    })
    trigger_ready_app._fire_snapshot_notification = AsyncMock(return_value=False)
    trigger_ready_app._fire_detail_fallback = AsyncMock()

    # 并发两次触发
    await asyncio.gather(
        NightGuardOrchestrator.on_door_unlock_trigger(
            trigger_ready_app, "event", {"source": "test-1"}, {}
        ),
        NightGuardOrchestrator.on_door_unlock_trigger(
            trigger_ready_app, "event", {"source": "test-2"}, {}
        ),
    )

    # 核心断言：Lock + 真实 _check_cooldown 保证只有 1 次真的往下走
    assert trigger_ready_app._fire_first_alert.call_count == 1
    assert trigger_ready_app._run_snapshot_loop.call_count == 1
    # 进程内冷却戳被第一次写入
    assert trigger_ready_app._in_process_last_alert is not None
