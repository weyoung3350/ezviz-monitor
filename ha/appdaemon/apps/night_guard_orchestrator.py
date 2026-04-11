"""夜间门锁告警编排器 — AppDaemon App（TDD 构建中，Task 4+ 逐步充实）。

纯函数集中在模块顶部，易于单元测试。
"""

import asyncio
from datetime import datetime, time, timedelta

import appdaemon.plugins.hass.hassapi as hass


# ══════════════════════════════════════════════════════════
#  纯函数
# ══════════════════════════════════════════════════════════


def is_in_alert_window(window_start, window_end, now):
    """判断 now 是否落在 [window_start, window_end) 时段内。支持跨天。

    规则：
    - window_start == window_end → 空区间，始终 False
    - window_start < window_end → 非跨天，标准半开区间判断
    - window_start > window_end → 跨天（如 23:00~07:30），now >= start 或 now < end
    """
    if window_start == window_end:
        return False
    if window_start < window_end:
        return window_start <= now < window_end
    return now >= window_start or now < window_end


def should_alert(last_alert, cooldown, now):
    """冷却判断：last_alert 为 None 视为从未告警，直接放行。"""
    if last_alert is None:
        return True
    return (now - last_alert) >= cooldown


def build_timestamp_tag(now):
    """'20260411_013733'"""
    return now.strftime("%Y%m%d_%H%M%S")


def build_time_display(now):
    """'01:37:33'"""
    return now.strftime("%H:%M:%S")


def build_snapshot_path(template, directory, timestamp, index):
    """例：'/config/www/night_alert_20260411_013733_3.jpg'"""
    filename = template.format(timestamp=timestamp, index=index)
    if directory.endswith("/"):
        return directory + filename
    return f"{directory}/{filename}"


def format_door_confirmation(door_ever_opened, last_open_state, current_state):
    """构造门状态文案。"""
    if door_ever_opened:
        return f"已确认开门（{last_open_state}）"
    return f"未确认开门（当前：{current_state}）"


def build_first_alert_message(time_display):
    """首条主告警文案。"""
    return (
        f"时间：{time_display}\n"
        "门锁状态：门内按钮开锁\n"
        "正在抓拍电梯厅画面..."
    )


def build_photo_caption(time_display, door_text, snapshot_attempts):
    """快照图片的 caption。"""
    return (
        f"时间：{time_display}\n"
        f"门状态：{door_text}\n"
        f"抓拍：{snapshot_attempts} 次尝试"
    )


def build_detail_message(
    time_display,
    door_text,
    snapshot_attempts,
    has_any_snapshot,
    photo_attempted,
):
    """详情兜底文案。"""
    lines = [
        "📋 告警详情",
        f"时间：{time_display}",
        f"门状态：{door_text}",
        f"抓拍尝试：{snapshot_attempts} 次",
    ]
    if not has_any_snapshot:
        lines.append("❌ 全部抓拍失败，请打开摄像头查看")
    elif photo_attempted:
        lines.append("ℹ️ 已尝试发送图片")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  App 主体
# ══════════════════════════════════════════════════════════


class NightGuardOrchestrator(hass.Hass):
    """监听门锁触发事件，编排告警流程（时段判断、冷却、抓拍、通知分发）。

    依赖：
    - 运行时：AppDaemon + HA（通过 self.call_service / self.fire_event / self.get_state）
    - 下游通知通道：AppDaemon NotifyService（通过 fire_event notify_service_request 解耦）
    """

    async def initialize(self):
        """读取 apps.yaml 参数，初始化并发锁，注册事件监听。"""
        self.camera_entity = self.args["camera_entity"]
        self.door_state_entity = self.args["door_state_entity"]
        self.snapshot_count = int(self.args.get("snapshot_count", 5))
        self.snapshot_interval_seconds = int(
            self.args.get("snapshot_interval_seconds", 5)
        )
        self.snapshot_dir = self.args.get("snapshot_dir", "/config/www")
        self.snapshot_filename_template = self.args.get(
            "snapshot_filename_template",
            "night_alert_{timestamp}_{index}.jpg",
        )
        self.cooldown = timedelta(
            seconds=int(self.args.get("cooldown_seconds", 300))
        )
        self.helper_enabled = self.args["helper_enabled"]
        self.helper_window_start = self.args["helper_window_start"]
        self.helper_window_end = self.args["helper_window_end"]
        self.helper_last_alert = self.args["helper_last_alert"]
        self.log_prefix = self.args.get("log_prefix", "[night_guard]")

        # 并发保护：串行化 on_door_unlock_trigger，防止 check-then-set race
        self._trigger_lock = asyncio.Lock()
        # 进程内兜底冷却戳：当 HA helper 不可用时使用
        self._in_process_last_alert: datetime | None = None

        self.listen_event(
            self.on_door_unlock_trigger, "night_guard.door_unlock_trigger"
        )
        self.log(
            f"{self.log_prefix} NightGuardOrchestrator 已启动 | "
            f"camera={self.camera_entity} cooldown={self.cooldown.total_seconds()}s "
            f"snapshot={self.snapshot_count}x{self.snapshot_interval_seconds}s"
        )

    async def on_door_unlock_trigger(self, event_name, data, kwargs):
        """主入口：业务编排。

        受 asyncio.Lock 保护，并发事件串行进入；check-then-set 原子。
        """
        async with self._trigger_lock:
            now = datetime.now()
            source = data.get("source", "unknown")
            triggered_at = data.get("triggered_at", "unknown")
            self.log(
                f"{self.log_prefix} 收到触发 source={source} triggered_at={triggered_at}"
            )

            if not await self._guard_enabled():
                self.log(f"{self.log_prefix} 总开关关闭，跳过")
                return

            if not await self._check_window(now.time()):
                self.log(f"{self.log_prefix} 当前时间不在告警时段，跳过")
                return

            if not await self._check_cooldown(now):
                self.log(f"{self.log_prefix} 冷却期内，跳过")
                return

            # 通过冷却检查 → 立即写冷却戳（helper + 进程内）
            await self._update_cooldown(now)

            timestamp = build_timestamp_tag(now)
            time_display = build_time_display(now)

            await self._fire_first_alert(timestamp, time_display)

            snapshot_result = await self._run_snapshot_loop(timestamp)

            photo_attempted = await self._fire_snapshot_notification(
                timestamp, time_display, snapshot_result
            )

            await self._fire_detail_fallback(
                timestamp, time_display, snapshot_result, photo_attempted
            )

            self.log(
                f"{self.log_prefix} 告警完整流程完成 timestamp={timestamp}"
            )

    async def _guard_enabled(self) -> bool:
        """读总开关 helper。helper 不可用时默认放行（告警优先，防止 helper 故障漏报）。"""
        state = await self.get_state(self.helper_enabled)
        if state in (None, "unknown", "unavailable"):
            return True
        return state == "on"

    async def _check_window(self, now_time) -> bool:
        """读时段 helper，调用 is_in_alert_window。helper 不可用时默认放行。"""
        start_state = await self.get_state(self.helper_window_start)
        end_state = await self.get_state(self.helper_window_end)

        def _parse(value):
            if value in (None, "unknown", "unavailable", ""):
                return None
            parts = value.split(":")
            if len(parts) < 2:
                return None
            try:
                return time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                return None

        start = _parse(start_state)
        end = _parse(end_state)
        if start is None or end is None:
            self.log(
                f"{self.log_prefix} 时段 helper 不可用 start={start_state} end={end_state}，默认放行",
                level="WARNING",
            )
            return True
        return is_in_alert_window(start, end, now_time)

    async def _check_cooldown(self, now) -> bool:
        """冷却判断：优先读 helper，helper 不可用时回退到进程内兜底。

        分级策略（Codex R1 review 决策 D）：
        - helper 可用且可解析 → 用 helper 时间戳做 should_alert 判断
        - helper 不可用 + 进程内兜底非空 → 用进程内兜底时间戳做判断
        - helper 不可用 + 进程内兜底为空 → 首次放行（冷却系统完全失灵时的兜底）
        """
        state = await self.get_state(self.helper_last_alert)

        # helper 不可用 → 进程内兜底
        if state in (None, "unknown", "unavailable", ""):
            if self._in_process_last_alert is None:
                self.log(
                    f"{self.log_prefix} helper 不可用且进程内冷却为空，首次放行",
                    level="WARNING",
                )
                return True
            self.log(
                f"{self.log_prefix} helper 不可用，使用进程内冷却 last={self._in_process_last_alert}",
                level="WARNING",
            )
            return should_alert(self._in_process_last_alert, self.cooldown, now)

        # helper 可用 → 尝试解析
        try:
            last = datetime.strptime(state, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                last = datetime.fromisoformat(state)
            except ValueError:
                self.log(
                    f"{self.log_prefix} 无法解析 last_alert 时间戳: {state}，走进程内兜底",
                    level="WARNING",
                )
                if self._in_process_last_alert is None:
                    return True
                return should_alert(
                    self._in_process_last_alert, self.cooldown, now
                )
        return should_alert(last, self.cooldown, now)

    async def _update_cooldown(self, now) -> None:
        """同时更新 HA helper 和进程内兜底变量。"""
        self._in_process_last_alert = now
        await self.call_service(
            "input_datetime/set_datetime",
            entity_id=self.helper_last_alert,
            datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
        )

    async def _fire_first_alert(self, timestamp, time_display) -> None:
        """发首条主告警：channel=all，force_sound=true，三通道并发（含电话响铃）。"""
        message = build_first_alert_message(time_display)
        self.fire_event(
            "notify_service_request",
            channel="all",
            title="夜间门内开锁告警",
            message=message,
            phone_alert_name="夜间门内开锁",
            force_sound=True,
            request_id=f"night_unlock_{timestamp}",
            source="night_guard.orchestrator",
        )
        self.log(
            f"{self.log_prefix} 主告警已发 request_id=night_unlock_{timestamp}"
        )

    async def _run_snapshot_loop(self, timestamp) -> dict:
        """连续抓拍 + 门状态观察。

        返回:
            dict:
                - last_successful_snapshot: 最后一次摄像头可用时的快照路径，否则 ""
                - door_ever_opened: 循环中是否观察到门被打开
                - door_opened_state: 首次观察到"已开锁"时的门状态原文
                - last_door_state: 循环结束时最后一次读到的门状态（供下游 format 使用）
        """
        last_successful = ""
        door_ever_opened = False
        door_opened_state = ""
        last_door_state = ""

        for i in range(1, self.snapshot_count + 1):
            path = build_snapshot_path(
                self.snapshot_filename_template,
                self.snapshot_dir,
                timestamp,
                i,
            )

            try:
                await self.call_service(
                    "camera/snapshot",
                    entity_id=self.camera_entity,
                    filename=path,
                )
            except Exception as e:
                self.log(
                    f"{self.log_prefix} 抓拍 {i} 异常: {e}",
                    level="WARNING",
                )

            # 摄像头可用时记录候选路径（HA 不能精确判断 snapshot 是否落盘，
            # 只能以实体状态作为粗略信号）
            cam_state = await self.get_state(self.camera_entity)
            if cam_state not in ("unavailable", "unknown", None):
                last_successful = path

            # 门状态观察
            door_state = await self.get_state(self.door_state_entity)
            if door_state:
                last_door_state = door_state
            if (
                not door_ever_opened
                and door_state
                and (
                    "已开锁" in door_state
                    or "虚掩" in door_state
                    or "门未关" in door_state
                )
            ):
                door_ever_opened = True
                door_opened_state = door_state

            # 间隔（最后一次不等）
            if i < self.snapshot_count:
                await asyncio.sleep(self.snapshot_interval_seconds)

        self.log(
            f"{self.log_prefix} 抓拍循环完成 last={last_successful or '(none)'} "
            f"door_ever_opened={door_ever_opened} last_door_state={last_door_state}"
        )
        return {
            "last_successful_snapshot": last_successful,
            "door_ever_opened": door_ever_opened,
            "door_opened_state": door_opened_state,
            "last_door_state": last_door_state,
        }

    async def _fire_snapshot_notification(
        self, timestamp, time_display, snapshot_result
    ) -> bool:
        """发快照通知（仅 dingtalk + ios_push，不响铃）。

        只有记录到候选快照路径时才发送。返回是否 attempted 发送。
        """
        if not snapshot_result["last_successful_snapshot"]:
            self.log(f"{self.log_prefix} 无候选快照，跳过快照通知")
            return False

        door_text = format_door_confirmation(
            snapshot_result["door_ever_opened"],
            snapshot_result["door_opened_state"],
            snapshot_result.get("last_door_state") or "unknown",
        )
        caption = build_photo_caption(time_display, door_text, self.snapshot_count)

        self.fire_event(
            "notify_service_request",
            channel=["dingtalk", "ios_push"],
            title="电梯厅快照",
            message=caption,
            image_path=snapshot_result["last_successful_snapshot"],
            force_sound=False,
            request_id=f"night_unlock_photo_{timestamp}",
            source="night_guard.orchestrator",
        )
        self.log(
            f"{self.log_prefix} 快照通知已发 request_id=night_unlock_photo_{timestamp}"
        )
        return True

    async def _fire_detail_fallback(
        self, timestamp, time_display, snapshot_result, photo_attempted
    ) -> None:
        """发详情兜底（仅 dingtalk + ios_push，不响铃，始终发送）。

        门状态使用 snapshot_result['last_door_state']（循环中最后一次读到的值），
        不硬编码 'unknown' 也不再额外读一次 HA 状态（避免时序不一致）。
        """
        door_text = format_door_confirmation(
            snapshot_result["door_ever_opened"],
            snapshot_result["door_opened_state"],
            snapshot_result.get("last_door_state") or "unknown",
        )
        has_any = bool(snapshot_result["last_successful_snapshot"])
        message = build_detail_message(
            time_display, door_text, self.snapshot_count, has_any, photo_attempted
        )
        self.fire_event(
            "notify_service_request",
            channel=["dingtalk", "ios_push"],
            message=message,
            force_sound=False,
            request_id=f"night_unlock_detail_{timestamp}",
            source="night_guard.orchestrator",
        )
        self.log(
            f"{self.log_prefix} 详情兜底已发 request_id=night_unlock_detail_{timestamp}"
        )
