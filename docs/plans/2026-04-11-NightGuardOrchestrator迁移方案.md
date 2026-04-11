# NightGuardOrchestrator 迁移方案（设计 + 实施 plan）

> **For agentic workers:** 本文档既是 spec 也是 implementation plan。实施阶段按 Task N 顺序推进，使用 checkbox (`- [ ]`) 跟踪。配合 superpowers:test-driven-development skill 使用。

**Goal:** 把 `script.send_night_unlock_alert`（当前 ~200 行 YAML）完整迁移到 AppDaemon Python app `NightGuardOrchestrator`，让 HA automation 只做事件过滤，业务逻辑全部 Python 化并具备单元测试覆盖。

**Architecture:** 三段式分层 —— `automation.H02`（物理事件 → 三元组过滤 → fire 业务事件） → `NightGuardOrchestrator`（时段/冷却/抓拍/门状态观察/通知编排） → `NotifyService`（已有，三通道并发）。业务配置全部走 HA helper（`input_datetime` / `input_boolean`），可在 HA UI 调整；实体名等技术参数走 apps.yaml。

**Tech Stack:** Python 3.11+（AppDaemon addon 运行时）、AppDaemon 0.18.x、pytest（本地 dev，不入部署）、unittest.mock（集成测试）、标准库 datetime/asyncio。

---

## 1. 背景与动机

当前 `send_night_unlock_alert` 是 HA `script`（YAML，~200 行），承担一次告警的完整编排：首条告警、连续抓拍、门状态循环观察、图片消息、详情兜底。虽然工作正常，但已经到了 YAML 能舒服表达的边界：

- 嵌套 `repeat / if / variables` 已经很难阅读
- 无法单元测试，只能靠 trace UI 和人工观察
- 错误处理原始（仅 `continue_on_error` 吞异常）
- 变量作用域是快照语义，新手易踩坑
- 未来扩展（二期 AI 判断、动态抓拍、多摄像头合成）几乎不可能继续写在 YAML 里

迁移到 AppDaemon Python app 后：
- 业务逻辑可测试、可重构、可版本控制
- 异常可分支处理
- automation 退回"声明式过滤"本职
- 为二期扩展留出干净接口

## 2. 分层边界（设计决策核心）

```
┌─────────────────────────────────────────────────────────────────┐
│ 小米门锁 M30 Pro (xiaomi_home)                                  │
└───────────────────────┬─────────────────────────────────────────┘
                        │ state_changed on event.xiaomi_..._lock_event
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ HA automation.H02 night_door_unlock_alert                       │
│   trigger: event.xiaomi_..._lock_event state changed            │
│   condition: op_method=9 & lock_action=2 & op_position=1        │
│     (仅保留三元组过滤)                                          │
│   action:                                                       │
│     - event: night_guard.door_unlock_trigger                    │
│       event_data:                                               │
│         source: "automation.H02"                                │
│         triggered_at: "{{ now().isoformat() }}"                 │
└───────────────────────┬─────────────────────────────────────────┘
                        │ event bus
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ AppDaemon NightGuardOrchestrator (new)                          │
│   listen_event: night_guard.door_unlock_trigger                 │
│   on_door_unlock_trigger (async):                               │
│     1. 读 input_boolean.night_guard_enabled → 关则 return       │
│     2. 读 input_datetime helpers → 时段判断 (pure fn)           │
│     3. 读 input_datetime.last_night_unlock_alert_at → 冷却判断  │
│     4. 写冷却时间戳                                             │
│     5. fire notify_service_request (channel=all,               │
│                                     force_sound=true) ← 主告警│
│     6. 连续抓拍 5 次 × 间隔 5 秒                                │
│        - 每次 call_service("camera/snapshot", ...)              │
│        - 每次读 door_state 观察瞬态                              │
│     7. fire notify_service_request                             │
│          (channel=[dingtalk,ios_push], image_path=...)          │
│     8. fire notify_service_request                             │
│          (channel=[dingtalk,ios_push], 详情文案)                │
└───────────────────────┬─────────────────────────────────────────┘
                        │ notify_service_request 事件
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ AppDaemon NotifyService (existing, 不改动)                      │
│   三通道并发：钉钉 / iOS 推送 / 电话                            │
└─────────────────────────────────────────────────────────────────┘
```

**决策点 A：三元组过滤放 automation**
- 理由：物理事件 → 业务事件的 filter，不属业务逻辑；过滤后减少 orchestrator 的无效调度
- 代价：automation YAML 里仍有 2 个 template condition

**决策点 B：时段 + 冷却判断放 orchestrator**
- 理由：这是业务策略（可变、需要 UI 配置、需要单元测试），应该集中在 Python
- 代价：时段判断逻辑在 automation 里会被全部放行，所有门内开锁事件都进入 orchestrator，但由于三元组已过滤，日常流量本来就很低（家里一天几次）

**决策点 C：保留 `input_datetime.last_night_unlock_alert_at` 不变**
- 理由：这是 HA 可视化的辅助实体，运维时能看到最近告警时间；orchestrator 读 + 写即可

## 3. 事件契约

### 3.1 输入事件 `night_guard.door_unlock_trigger`

```yaml
event_type: night_guard.door_unlock_trigger
event_data:
  source: "automation.H02"            # 必填，用于日志追溯
  triggered_at: "2026-04-11T13:30:00+08:00"  # 必填，ISO8601，orchestrator 用于日志对齐
```

orchestrator 不再读原始门锁实体属性。automation 已完成三元组过滤，信任触发上下文即可。如果未来需要其他门锁或其他触发源，新增 automation 发同一事件即可（orchestrator 代码无需改动）。

### 3.2 输出事件 `notify_service_request`

格式与现状一致，见 `docs/notify-service-design.md` §5。orchestrator 会按业务流程发 3 条：

1. **主告警**：`channel="all"`, `force_sound=true`, 首条文字
2. **快照图片**（条件：至少 1 张快照成功）：`channel=["dingtalk","ios_push"]`, `image_path=<最后一张成功快照>`, `force_sound=false`
3. **详情兜底**：`channel=["dingtalk","ios_push"]`, 无 image_path, 文字包含时间/门状态/抓拍次数/失败说明

### 3.3 请求 ID 规则

为了便于日志关联并与现网历史数据保持兼容，orchestrator 沿用现网脚本的 request_id 命名格式：
- 主告警：`night_unlock_{timestamp}`
- 快照：`night_unlock_photo_{timestamp}`
- 详情：`night_unlock_detail_{timestamp}`

`{timestamp}` 格式：`YYYYMMDD_HHMMSS`，由 orchestrator 自己生成（不依赖 automation 传入的 triggered_at）。

## 4. 配置模型

### 4.1 HA helper（UI 可改）

| 实体 | 类型 | 用途 | 默认值 |
|---|---|---|---|
| `input_boolean.night_guard_enabled` | boolean | 告警总开关 | `on` |
| `input_datetime.night_guard_start` | time-only | 告警开始时间 | `23:00:00` |
| `input_datetime.night_guard_end` | time-only | 告警结束时间 | `07:30:00` |
| `input_datetime.last_night_unlock_alert_at` | datetime | 冷却时间戳（**现有**，不改） | — |

跨天逻辑：当 `start > end` 时视为跨天时段（如 23:00~07:30），支持 `now >= start or now < end` 判断。

### 4.2 apps.yaml（技术参数）

```yaml
night_guard_orchestrator:
  module: night_guard_orchestrator
  class: NightGuardOrchestrator

  # 设备实体
  camera_entity: camera.dian_ti_ting_mainstream
  door_state_entity: sensor.xiaomi_cn_1150511669_s20pro_door_state_p_3_1021

  # 抓拍参数
  snapshot_count: 5
  snapshot_interval_seconds: 5
  snapshot_dir: /config/www
  snapshot_filename_template: "night_alert_{timestamp}_{index}.jpg"

  # 冷却（单位：秒）
  cooldown_seconds: 300

  # HA helper 实体名
  helper_enabled: input_boolean.night_guard_enabled
  helper_window_start: input_datetime.night_guard_start
  helper_window_end: input_datetime.night_guard_end
  helper_last_alert: input_datetime.last_night_unlock_alert_at

  # 日志标签
  log_prefix: "[night_guard]"
```

### 4.3 automation 改造

> ⚠️ **部署目标与仓库文件的关系**：HA 实际运行的是 `/homeassistant/automations.yaml` 和 `/homeassistant/scripts.yaml`（monolithic 模式），**不是** package 模式。仓库里的 `ha/packages/night_guard_automations.yaml` 和 `ha/packages/night_guard_scripts.yaml` 是"源码副本"，目前不被 HA 加载，仅用于版本管理和 review。本次部署同时更新两处：修改 HA 上的 monolithic 文件落地，同时同步修改仓库副本保持一致。

`/homeassistant/automations.yaml` 中 `H02 night_door_unlock_alert` 改为：

```yaml
- id: night_door_unlock_alert
  alias: "H02 夜间门内开锁告警（触发层）"
  description: >
    监听门锁事件，满足门内按钮开锁三元组时 fire night_guard.door_unlock_trigger 业务事件。
    所有业务判断（时段、冷却、抓拍、通知）由 AppDaemon NightGuardOrchestrator 处理。
  mode: queued
  max: 5
  trigger:
    - platform: state
      entity_id: event.xiaomi_cn_1150511669_s20pro_lock_event_e_2_1020
  condition:
    - condition: template
      value_template: >-
        {% set op_method = state_attr('event.xiaomi_cn_1150511669_s20pro_lock_event_e_2_1020', '操作方式') %}
        {% set lock_action = state_attr('event.xiaomi_cn_1150511669_s20pro_lock_event_e_2_1020', '锁动作') %}
        {% set op_position = state_attr('event.xiaomi_cn_1150511669_s20pro_lock_event_e_2_1020', '操作位置') %}
        {{ op_method | string == '9' and lock_action | string == '2' and op_position | string == '1' }}
  action:
    - event: night_guard.door_unlock_trigger
      event_data:
        source: "automation.H02"
        triggered_at: "{{ now().isoformat() }}"
```

- 删除时段 condition
- 删除冷却 condition
- 删除 `input_datetime.set_datetime` 动作（冷却时间戳改由 orchestrator 写）
- 模式由 `single` → `queued` max=5（允许短时间内多次物理事件进入 orchestrator 排队，但 orchestrator 内部有冷却保护）

## 5. 代码结构

```
ha/appdaemon/apps/
  night_guard_orchestrator.py        # NEW, ~250 行
  notify_service.py                   # 不动
  apps.yaml.example                   # 更新，加 night_guard_orchestrator 段

tests/
  __init__.py                         # NEW, 空文件
  conftest.py                         # NEW, pytest fixtures
  test_night_guard_orchestrator.py    # NEW, ~400 行

pyproject.toml                        # 更新，加 dev 依赖 pytest
docs/
  notify-service-design.md            # 更新，加 orchestrator 引用
  plans/
    2026-04-11-NightGuardOrchestrator迁移方案.md  # 本文件
```

### 5.1 `night_guard_orchestrator.py` 模块划分

```python
# 模块顶部：纯函数（易测试）
def is_in_alert_window(window_start: time, window_end: time, now: time) -> bool
def should_alert(last_alert: datetime | None, cooldown: timedelta, now: datetime) -> bool
def build_timestamp_tag(now: datetime) -> str                       # YYYYMMDD_HHMMSS
def build_time_display(now: datetime) -> str                        # HH:MM:SS
def build_snapshot_path(template: str, directory: str, timestamp: str, index: int) -> str
def format_door_confirmation(door_ever_opened: bool, last_state: str, current_state: str) -> str
def build_first_alert_message(time_display: str) -> str
def build_photo_caption(time_display: str, door_text: str, snapshot_attempts: int) -> str
def build_detail_message(time_display: str, door_text: str, snapshot_attempts: int, has_any_snapshot: bool, photo_attempted: bool) -> str
```

### 5.1a 并发与进程内状态

`on_door_unlock_trigger` 是 async 回调，AppDaemon 可能为几乎同时到达的两个事件并发调度该回调，进而导致 check-then-set race（两个回调都通过冷却检查）。通过以下机制保证原子性：

```python
# initialize 里
self._trigger_lock = asyncio.Lock()
self._in_process_last_alert: datetime | None = None  # 进程内兜底冷却时间戳

# on_door_unlock_trigger 最外层
async with self._trigger_lock:
    # 冷却判定 + 写冷却（helper + 进程内）是一个原子区间
    # 后续编排（抓拍、发通知）仍在 lock 内
    ...
```

`self._trigger_lock` 把整条业务流程序列化，使即使 automation 以 `mode=queued max=5` 允许多次触发进入事件总线，orchestrator 也只串行处理。

`self._in_process_last_alert` 是进程内冷却兜底：当 HA helper `input_datetime.last_night_unlock_alert_at` 不可用时，`_check_cooldown` 改读进程内变量；`_update_cooldown` 同时更新 helper 和进程内变量。这样即使 helper 永久故障，也不会因为冷却判断失效而无限重复告警。

# App 主体
class NightGuardOrchestrator(hass.Hass):
    async def initialize(self):
        """读取 apps.yaml 配置，注册事件监听。"""

    async def on_door_unlock_trigger(self, event_name, data, kwargs):
        """主入口：业务编排。"""

    async def _guard_enabled(self) -> bool:
        """读 input_boolean 总开关。"""

    async def _check_window(self, now: datetime) -> bool:
        """读 input_datetime helper，调用 is_in_alert_window 纯函数。"""

    async def _check_cooldown(self, now: datetime) -> bool:
        """读 input_datetime helper，调用 should_alert 纯函数。"""

    async def _update_cooldown(self, now: datetime) -> None:
        """写 input_datetime.set_datetime 服务。"""

    async def _fire_first_alert(self, timestamp: str, time_display: str) -> None:
        """发主告警 notify_service_request。"""

    async def _run_snapshot_loop(self, timestamp: str) -> dict:
        """连续抓拍 + 门状态观察，返回 {last_successful_snapshot, door_ever_opened, door_opened_state, last_door_state}。"""

    async def _fire_snapshot_notification(self, timestamp: str, time_display: str, snapshot_result: dict) -> bool:
        """发快照通知，返回是否 attempted 发送图片。"""

    async def _fire_detail_fallback(self, timestamp: str, time_display: str, snapshot_result: dict, photo_attempted: bool) -> None:
        """发详情兜底通知。"""
```

### 5.2 纯函数完整实现（便于 TDD 先写测试）

```python
from datetime import datetime, time, timedelta

def is_in_alert_window(window_start: time, window_end: time, now: time) -> bool:
    """判断 now 是否落在 [window_start, window_end) 时段内。支持跨天。

    规则：
    - window_start < window_end：非跨天，标准区间判断
    - window_start == window_end：永远为 False（空区间）
    - window_start > window_end：跨天，now >= start or now < end
    """
    if window_start == window_end:
        return False
    if window_start < window_end:
        return window_start <= now < window_end
    return now >= window_start or now < window_end


def should_alert(last_alert: datetime | None, cooldown: timedelta, now: datetime) -> bool:
    """判断是否可以告警（冷却结束）。last_alert 为 None 视为从未告警。"""
    if last_alert is None:
        return True
    return (now - last_alert) >= cooldown


def build_timestamp_tag(now: datetime) -> str:
    return now.strftime("%Y%m%d_%H%M%S")


def build_time_display(now: datetime) -> str:
    return now.strftime("%H:%M:%S")


def build_snapshot_path(template: str, directory: str, timestamp: str, index: int) -> str:
    """例：'/config/www/night_alert_20260411_013733_3.jpg'"""
    filename = template.format(timestamp=timestamp, index=index)
    if directory.endswith("/"):
        return directory + filename
    return f"{directory}/{filename}"


def format_door_confirmation(door_ever_opened: bool, last_open_state: str, current_state: str) -> str:
    if door_ever_opened:
        return f"已确认开门（{last_open_state}）"
    return f"未确认开门（当前：{current_state}）"


def build_first_alert_message(time_display: str) -> str:
    return (
        f"时间：{time_display}\n"
        "门锁状态：门内按钮开锁\n"
        "正在抓拍电梯厅画面..."
    )


def build_photo_caption(time_display: str, door_text: str, snapshot_attempts: int) -> str:
    return (
        f"时间：{time_display}\n"
        f"门状态：{door_text}\n"
        f"抓拍：{snapshot_attempts} 次尝试"
    )


def build_detail_message(
    time_display: str,
    door_text: str,
    snapshot_attempts: int,
    has_any_snapshot: bool,
    photo_attempted: bool,
) -> str:
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
```

### 5.3 App 主体实现要点

```python
import asyncio
from datetime import datetime, time, timedelta
from functools import partial

import appdaemon.plugins.hass.hassapi as hass


class NightGuardOrchestrator(hass.Hass):

    async def initialize(self):
        # apps.yaml 参数
        self.camera_entity = self.args["camera_entity"]
        self.door_state_entity = self.args["door_state_entity"]
        self.snapshot_count = int(self.args.get("snapshot_count", 5))
        self.snapshot_interval_seconds = int(self.args.get("snapshot_interval_seconds", 5))
        self.snapshot_dir = self.args.get("snapshot_dir", "/config/www")
        self.snapshot_filename_template = self.args.get(
            "snapshot_filename_template",
            "night_alert_{timestamp}_{index}.jpg",
        )
        self.cooldown = timedelta(seconds=int(self.args.get("cooldown_seconds", 300)))
        self.helper_enabled = self.args["helper_enabled"]
        self.helper_window_start = self.args["helper_window_start"]
        self.helper_window_end = self.args["helper_window_end"]
        self.helper_last_alert = self.args["helper_last_alert"]
        self.log_prefix = self.args.get("log_prefix", "[night_guard]")

        self.listen_event(self.on_door_unlock_trigger, "night_guard.door_unlock_trigger")
        self.log(f"{self.log_prefix} NightGuardOrchestrator 已启动 | "
                 f"camera={self.camera_entity} cooldown={self.cooldown.total_seconds()}s "
                 f"snapshot={self.snapshot_count}x{self.snapshot_interval_seconds}s")

    async def on_door_unlock_trigger(self, event_name, data, kwargs):
        # ⭐ 整个回调被 asyncio.Lock 包住，防止 check-then-set race
        async with self._trigger_lock:
            now = datetime.now()
            source = data.get("source", "unknown")
            triggered_at = data.get("triggered_at", "unknown")
            self.log(f"{self.log_prefix} 收到门锁触发 source={source} triggered_at={triggered_at}")

            # 1. 总开关
            if not await self._guard_enabled():
                self.log(f"{self.log_prefix} 总开关关闭，跳过")
                return

            # 2. 时段判断
            if not await self._check_window(now.time()):
                self.log(f"{self.log_prefix} 当前时间不在告警时段，跳过")
                return

            # 3. 冷却判断（读 helper + 进程内兜底）
            if not await self._check_cooldown(now):
                self.log(f"{self.log_prefix} 冷却期内，跳过")
                return

            # 4. 写冷却时间戳（helper + 进程内）
            await self._update_cooldown(now)

            # 5. 发主告警
            timestamp = build_timestamp_tag(now)
            time_display = build_time_display(now)
            await self._fire_first_alert(timestamp, time_display)

            # 6. 连续抓拍
            snapshot_result = await self._run_snapshot_loop(timestamp)

            # 7. 发快照通知
            photo_attempted = await self._fire_snapshot_notification(
                timestamp, time_display, snapshot_result
            )

            # 8. 发详情兜底
            await self._fire_detail_fallback(
                timestamp, time_display, snapshot_result, photo_attempted
            )

            self.log(f"{self.log_prefix} 告警完整流程完成 timestamp={timestamp}")
```

其他方法（`_guard_enabled` / `_check_window` / `_check_cooldown` / `_update_cooldown` / `_fire_first_alert` / `_run_snapshot_loop` / `_fire_snapshot_notification` / `_fire_detail_fallback`）的具体实现见 Task 4 ~ Task 11。

## 6. 单元测试策略

### 6.1 测试分层

| 层 | 类型 | 工具 | 覆盖 |
|---|---|---|---|
| **纯函数** | 单元测试 | pytest + parametrize | `is_in_alert_window`, `should_alert`, `build_*` 所有格式化函数 |
| **App 方法** | 集成测试 | pytest + unittest.mock | `_guard_enabled`, `_check_window`, `_check_cooldown`, `_update_cooldown`, `_fire_*`, `_run_snapshot_loop` |
| **主入口** | 端到端（mock） | pytest + AsyncMock | `on_door_unlock_trigger` 各分支（总开关关 / 时段外 / 冷却期内 / 正常 / 抓拍全失败 / 抓拍部分成功） |

### 6.2 pytest fixtures（`tests/conftest.py`）

```python
import asyncio
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# 从测试目录直接导入被测模块（需要把 ha/appdaemon/apps 加入 sys.path）
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "ha" / "appdaemon" / "apps"))


@pytest.fixture
def mock_hass_app():
    """构造一个 mock 的 AppDaemon Hass app 实例，所有 self.* 调用都是 AsyncMock/MagicMock。"""
    from night_guard_orchestrator import NightGuardOrchestrator

    app = MagicMock(spec=NightGuardOrchestrator)

    # AppDaemon 常用 API
    app.log = MagicMock()
    app.get_state = AsyncMock()
    app.call_service = AsyncMock()
    app.fire_event = MagicMock()
    app.listen_event = MagicMock()
    app.run_in_executor = AsyncMock()

    # 配置
    app.camera_entity = "camera.test_cam"
    app.door_state_entity = "sensor.test_door"
    app.snapshot_count = 3
    app.snapshot_interval_seconds = 1
    app.snapshot_dir = "/config/www"
    app.snapshot_filename_template = "night_alert_{timestamp}_{index}.jpg"
    app.cooldown = timedelta(seconds=300)
    app.helper_enabled = "input_boolean.test_enabled"
    app.helper_window_start = "input_datetime.test_start"
    app.helper_window_end = "input_datetime.test_end"
    app.helper_last_alert = "input_datetime.test_last"
    app.log_prefix = "[test]"

    return app


@pytest.fixture
def fixed_now():
    """固定时间点：2026-04-11 01:37:33，位于默认告警时段内。"""
    return datetime(2026, 4, 11, 1, 37, 33)
```

### 6.3 纯函数测试用例清单（详见 Task 1 ~ Task 3）

**`is_in_alert_window`**:
- 非跨天区间，now 在中间 → True
- 非跨天区间，now 在边界 start → True
- 非跨天区间，now 在边界 end → False（半开区间）
- 非跨天区间，now 在区间外 → False
- 跨天区间，now 在 start 之后 → True
- 跨天区间，now 在 end 之前 → True
- 跨天区间，now 在空档期 → False
- start == end 空区间 → 始终 False

**`should_alert`**:
- last_alert 为 None → True
- last_alert 距今大于 cooldown → True
- last_alert 距今等于 cooldown → True
- last_alert 距今小于 cooldown → False

**`build_snapshot_path`**:
- 目录末尾无 / → 正确拼接
- 目录末尾有 / → 不重复加 /
- index 代入正确

**`format_door_confirmation`**:
- door_ever_opened=True → 显示 last_open_state
- door_ever_opened=False → 显示 current_state

**`build_detail_message`**:
- 正常有图 + photo_attempted=True → 包含"已尝试发送图片"
- 无任何快照 → 包含"全部抓拍失败"
- 有快照但 photo 未尝试 → 不含两者

### 6.4 App 方法测试用例清单（详见 Task 4 ~ Task 12）

| 方法 | 场景 | 断言 |
|---|---|---|
| `_guard_enabled` | helper 返回 "on" | True |
| `_guard_enabled` | helper 返回 "off" | False |
| `_guard_enabled` | helper 返回 None / unavailable | True（默认放行） |
| `_check_window` | mock helper 返回 23:00 / 07:30，now=01:37 | True |
| `_check_window` | mock helper 返回 23:00 / 07:30，now=12:00 | False |
| `_check_cooldown` | last_alert 为 unknown | True |
| `_check_cooldown` | last_alert 距今 100 秒，冷却 300 秒 | False |
| `_update_cooldown` | 调用后 call_service 被正确调用 | call_args 断言 |
| `_fire_first_alert` | 调用后 fire_event 被正确调用 | channel="all", force_sound=True |
| `_run_snapshot_loop` | 摄像头 available 全程 | 返回 last_successful_snapshot 非空 |
| `_run_snapshot_loop` | 摄像头 unavailable 全程 | 返回 last_successful_snapshot = "" |
| `_run_snapshot_loop` | 门状态中途出现"已开锁" | door_ever_opened=True |
| `_fire_snapshot_notification` | last_snapshot 非空 | fire_event 被调用，photo_attempted=True 返回 |
| `_fire_snapshot_notification` | last_snapshot 为空 | fire_event 未被调用，返回 False |
| `_fire_detail_fallback` | 总是调用 fire_event | 断言 channel=[dingtalk,ios_push] |
| `on_door_unlock_trigger` | 总开关关 | 提前 return，fire_event 未调用 |
| `on_door_unlock_trigger` | 时段外 | 提前 return |
| `on_door_unlock_trigger` | 冷却期内 | 提前 return |
| `on_door_unlock_trigger` | 全流程正常 | 主告警 + 快照 + 详情 3 次 fire_event |

### 6.5 pytest 依赖声明

`pyproject.toml` 更新：

```toml
[project.optional-dependencies]
dev = [
  "pytest>=7.0",
  "pytest-asyncio>=0.21",
]
```

本地运行：`pip install -e .[dev] && pytest tests/ -v`

**注意**：AppDaemon addon 运行时不依赖 pytest；dev 依赖只在本地开发机（Mac）使用。测试文件不会部署到 HA。

## 7. 部署步骤

### 7.1 本地准备
1. 本地跑 `pytest tests/ -v` 全部通过
2. 语法检查 `python3 -m py_compile ha/appdaemon/apps/night_guard_orchestrator.py`
3. 更新 `ha/appdaemon/apps.yaml.example` 加入 `night_guard_orchestrator` 段（占位符）
4. 同步更新仓库副本 `ha/packages/night_guard_automations.yaml`（让仓库和部署后的 HA 配置保持一致）

### 7.2 HA 侧部署

> 部署目标都是 HA 上的实际运行文件，**不是仓库文件**。仓库 `ha/packages/*.yaml` 是源码副本，部署完后单独同步。

**实际运行文件路径**：
- AppDaemon app: `/addon_configs/a0d7b954_appdaemon/apps/night_guard_orchestrator.py`
- AppDaemon 配置: `/addon_configs/a0d7b954_appdaemon/apps/apps.yaml`
- HA 自动化: `/homeassistant/automations.yaml`
- HA 脚本: `/homeassistant/scripts.yaml`（用于保留旧 script 作为回滚）
- HA 配置: `/homeassistant/configuration.yaml`（用于声明 input_boolean / input_datetime helper）

**部署顺序**：

1. 备份 4 个文件：apps.yaml / automations.yaml / scripts.yaml / configuration.yaml，加 `.bak-20260411-T14` 后缀
2. `scp` 本地 `night_guard_orchestrator.py` → `/addon_configs/a0d7b954_appdaemon/apps/`
3. SSH 编辑 `/addon_configs/a0d7b954_appdaemon/apps/apps.yaml`，追加 `night_guard_orchestrator` 段（填真实 camera/sensor 实体名）
4. SSH 编辑 `/homeassistant/configuration.yaml`，声明 3 个新 helper（见 §7.3）
5. SSH 编辑 `/homeassistant/automations.yaml`，修改 H02 为新版（见 §4.3）—— 删除时段 condition、删除冷却 condition、action 改为 fire `night_guard.door_unlock_trigger`
6. 通过 HA REST API reload: `input_boolean.reload`, `input_datetime.reload`, `automation.reload`
7. 重启 AppDaemon addon：`ssh root@192.168.77.253 'ha addons restart a0d7b954_appdaemon'`（legacy 命令 `ha addons` 仍兼容，与本项目其他部署脚本保持一致）
8. 观察 AppDaemon 日志，确认 `NightGuardOrchestrator 已启动` 和无 Traceback

### 7.3 helper 声明方式

**不能通过 HA REST API 动态创建 `input_boolean` / `input_datetime`**（HA 对这些"配置型"实体不开放 REST 创建）。必须通过编辑 `/homeassistant/configuration.yaml` 或 HA UI 手动创建。本次选择 configuration.yaml 方式，便于脚本化：

```yaml
# 在 /homeassistant/configuration.yaml 追加：
input_boolean:
  night_guard_enabled:
    name: "夜间监护总开关"
    initial: true
    icon: mdi:shield-home

input_datetime:
  night_guard_start:
    name: "告警开始时间"
    has_date: false
    has_time: true
    initial: "23:00:00"
    icon: mdi:clock-start
  night_guard_end:
    name: "告警结束时间"
    has_date: false
    has_time: true
    initial: "07:30:00"
    icon: mdi:clock-end
  # last_night_unlock_alert_at 已存在（由 H02 旧自动化创建），本次不动
```

然后通过 REST API reload：
```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://192.168.77.253:8123/api/services/input_boolean/reload
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://192.168.77.253:8123/api/services/input_datetime/reload
```

### 7.3 验证
1. 观察 AppDaemon 日志确认 NightGuardOrchestrator 初始化成功
2. 手动 fire 测试事件：`POST /api/events/night_guard.door_unlock_trigger` body `{"source": "manual", "triggered_at": "..."}`
3. 观察日志顺序：总开关 → 时段 → 冷却 → 主告警 → 抓拍 5 次 → 快照通知 → 详情兜底
4. 检查钉钉群 + iPhone + 电话（如 channel=all）
5. 临时把 `night_guard_start` 改为当前时间前 1 分钟，真实开门触发端到端测试
6. 确认 `input_datetime.last_night_unlock_alert_at` 被 orchestrator 更新

### 7.4 旧 script 保留 1 周
- 旧 `script.send_night_unlock_alert` 保留在 scripts.yaml 中，但不再被任何 automation 引用
- 观察 1 周运行稳定后删除

## 8. 回滚预案

**回滚触发条件**：
- orchestrator 不响应事件
- 任何一类通道彻底失效
- 冷却或时段逻辑错误

**回滚步骤**（目标 < 5 分钟，需 SSH + 人工编辑 + reload + 验证）：

1. SSH 到 HA：`ssh root@192.168.77.253`
2. 把 automations.yaml 恢复为备份版本：
   ```bash
   cp /homeassistant/automations.yaml.bak-20260411-T14 /homeassistant/automations.yaml
   ```
3. 注释 apps.yaml 中 `night_guard_orchestrator:` 段的所有行：
   ```bash
   # 简易：用 sed 注释行，或手工用 vi/nano 编辑
   ```
4. `ha addons restart a0d7b954_appdaemon`
5. 通过 HA REST API reload automations：
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     http://192.168.77.253:8123/api/services/automation/reload
   ```
6. 手动触发 `script.send_night_unlock_alert` 验证原路径恢复可用

**回滚依赖**：
- 旧 `script.send_night_unlock_alert` 必须在 `/homeassistant/scripts.yaml` 中完整保留，直到迁移稳定运行 1 周
- 旧 script 上方添加 `DEPRECATED` 注释但**不删除代码内容**
- `input_datetime.last_night_unlock_alert_at` helper 保留（仍被新老逻辑共用）
- 3 个新 helper（`night_guard_enabled` / `night_guard_start` / `night_guard_end`）即使不回滚也可以继续保留，不影响旧路径

**"< 5 分钟"含义说明**：SSH 连接 + 文件编辑 + 两次 reload + 手动验证触发 + 确认通知送达，正常情况下人工操作时间约 3-5 分钟。不包含排障时间。

## 9. 风险与已知未决

| 风险 | 影响 | 缓解 |
|---|---|---|
| 并发触发导致 check-then-set race | 双重告警 | **`asyncio.Lock`** 包住整个 on_door_unlock_trigger，串行处理所有事件 |
| HA helper 返回 unknown / unavailable | 判断失败 | 见决策 D（分级处理） |
| AppDaemon mock 基类构造复杂 | 单测写不出来 | `MagicMock(spec=...)` + AsyncMock 方案；另加 smoke test 验证类签名 |
| pytest-asyncio fixture 版本差异 | CI 失败 | 固定最低版本 ≥ 0.21，用 `@pytest.mark.asyncio` |
| automation 改为 queued max=5 | 多次门锁抖动时 orchestrator 排队，日志噪声增加 | 可接受代价；冷却 + Lock 保证只有第一次真实告警；必要时可把 max 改回 1 |
| 旧 script 保留期间被人工引用 | 双路径并行导致认知混乱 | 在旧 script 上添加 `DEPRECATED` 注释 + 禁止任何 automation 引用 + 禁止通过 HA UI 手动触发 |
| 本次迁移与仓库/HA 配置不一致（packages vs monolithic） | 部署出错 | §4.3 / §7.2 明确部署目标 = HA 上 monolithic 文件，仓库副本单独同步 |

**决策 D（helper 不可用时的默认行为）—— 分级处理**：

| Helper | 不可用时行为 | 理由 |
|---|---|---|
| `input_boolean.night_guard_enabled`（总开关） | **默认放行** | 告警优先，防止因 helper 故障漏报 |
| `input_datetime.night_guard_start/end`（时段） | **默认放行** | 同上；边界失效时宁可多报不漏报 |
| `input_datetime.last_night_unlock_alert_at`（冷却） | **使用进程内兜底冷却**（`self._in_process_last_alert`） | 不能默认放行 —— 放行会放大重复告警。helper 失效时改读进程内变量，仍然受 `cooldown_seconds` 保护。只有 orchestrator 重启+helper 同时失效才可能重复告警 |

进程内兜底冷却的细节：
- `initialize` 时 `self._in_process_last_alert = None`
- `_check_cooldown` 优先读 helper；helper 不可用时读 `self._in_process_last_alert`
- `_update_cooldown` 总是同时更新 helper 和进程内变量
- orchestrator 进程重启后进程内变量归零，此时如果 helper 也失效，会有一次"首次告警"窗口 —— 可接受

## 10. 不做的事（YAGNI）

- 不改 NotifyService（已稳定）
- 不改三通道送达逻辑
- 不引入 OSS 图片上传（等用户提供凭据后单独一轮）
- 不做多摄像头合成
- 不做二期 AI 判断
- 不重构门锁实体硬编码（通过 apps.yaml 已可配置）
- 不做 automation 的 helpers.yaml 分离（保持 monolithic automations.yaml）

## 11. 评审历史

### R1 — Codex（2026-04-11）

**结论**：建议修改后实施

**Blocking 问题（已在 R2 修复）**：
1. **§5.3 / Task 11 Check-then-set race**：冷却判定与写回分离，并发事件可能同时通过检查双告警 → 已加 `asyncio.Lock` 包住整个 `on_door_unlock_trigger`，`_update_cooldown` 同时写 helper 和进程内变量；新增并发测试 `test_on_trigger_concurrent_second_blocked_by_cooldown`
2. **§7.2 / Task 14 部署文件路径**：plan 写 `/homeassistant/automations.yaml`，但仓库是 packages 模式。→ §4.3 / §7.2 已明确实际运行文件在 monolithic 路径，仓库副本作为源码管理单独同步
3. **Task 14.4 REST API 创建矛盾命令**：先写 REST create 再说"不能 REST create" → 已删除无效 REST 命令，只保留 configuration.yaml 方案
4. **Task 14 重启命令不一致**：前后混用 `ha addons restart` 和 `ha apps restart` → 统一为 `ha addons restart`（legacy 命令，与项目既有脚本一致）

**已采纳的建议改进（Non-blocking）**：
- §9 决策 D：冷却 helper 不可用时改为**进程内兜底**（`self._in_process_last_alert`），而非无条件放行；时段和总开关保持"默认放行"取向
- §3.3 `request_id` 命名改为与现网一致的 `night_unlock_photo_{ts}` / `night_unlock_detail_{ts}` 格式
- §5.3 `_run_snapshot_loop` 返回增加 `last_door_state` 字段，`_fire_snapshot_notification` / `_fire_detail_fallback` 使用它而不是硬编码 `"unknown"` 或重读 HA 状态
- §6.2 / Task 4 加 `test_orchestrator_class_signature` smoke test，验证类关键方法和 coroutine 签名

**风险提示（记录到 §9）**：
- automation `mode=queued max=5` 在门锁抖动时增加日志噪声 —— 可接受代价
- 旧 script 保留期间禁止任何 automation / 人工引用
- `notify-service-design.md` 文档更新验收口径加入 Task 16

### R2 — Codex（2026-04-11）

**结论**：仍有 blocking，不建议直接进入实施

**Blocking 问题**：
1. **Task 11 并发测试 mock 自相矛盾**：原测试用 `_check_cooldown.side_effect=[True, False]` mock 掉了真实状态机，但注释声明"用真实状态机"，不能证明 race 被真实覆盖
2. **自检清单行 2295 `< 2 分钟` vs §8 正文 `< 5 分钟` 冲突**：R1 修复时漏改自检清单

**其他说明**：
- 仓库里还没有 `night_guard_orchestrator.py` 和 test 文件（plan 阶段正常），Codex 期望代码落地后做最终评审

### R3 — 本次修改（2026-04-11）

**处理**：
1. **Task 11 并发测试重写**：改为使用 `Method.__get__(instance)` 把未绑定的 `_guard_enabled` / `_check_window` / `_check_cooldown` / `_update_cooldown` 真实方法绑定到 mock 实例，只 mock `get_state` / `call_service` / 下游 `_fire_*` 方法。helper 返回 `unknown` 强制走进程内兜底路径。这样两次并发触发，只有第一次通过真实的 `should_alert` 纯函数判断并写入 `_in_process_last_alert`，第二次被真实的冷却判定拦截。如果去掉 Lock 该测试会失败 —— 证明 race 被真正覆盖。
2. **自检清单 §12 行 2295 改为 `< 5 分钟`**，与正文一致
3. **新增 R3 条目** 等待 Codex 第三轮确认

## 12. 实施结果（2026-04-11 晚）

### 12.1 TDD 结果
- `tests/test_night_guard_orchestrator.py`：**55/55 通过**，耗时 ~50 ms
- `ha/appdaemon/apps/night_guard_orchestrator.py`：432 行
- 测试覆盖（合计 55）：
  - **纯函数 24 个**：`is_in_alert_window` (9) + `should_alert` (4) + `build_timestamp_tag`/`build_time_display` (2) + `build_snapshot_path` (2) + `format_door_confirmation` (2) + `build_first_alert_message` (1) + `build_photo_caption` (1) + `build_detail_message` (3)
  - **App 类结构 2 个**：`test_orchestrator_class_signature` smoke + `test_initialize_registers_listener`
  - **App 方法 24 个**：`_guard_enabled` (4) + `_check_window` (4) + `_check_cooldown` + `_update_cooldown` (7 含进程内兜底分支) + `_fire_first_alert` (1) + `_run_snapshot_loop` (3 含异常分支) + `_fire_snapshot_notification` + `_fire_detail_fallback` (5)
  - **主入口 `on_door_unlock_trigger` 5 个**：4 个分支（guard_disabled / outside_window / cooldown_active / full_path）+ 1 个并发 race 覆盖（`test_on_trigger_concurrent_second_blocked_by_cooldown`）
- **并发 race 测试**（`test_on_trigger_concurrent_second_blocked_by_cooldown`）通过 `Method.__get__` 绑定真实 `_check_cooldown` / `_update_cooldown`，让两个并发 coroutine 竞争进入 orchestrator，验证只有第一次真正走完全流程 —— 如果去掉 `asyncio.Lock` 测试会失败，race 被真正覆盖。

### 12.2 部署结果
- `night_guard_orchestrator.py` 上传到 `/addon_configs/a0d7b954_appdaemon/apps/`
- `apps.yaml` 追加 `night_guard_orchestrator:` 段（真实实体名）
- `configuration.yaml` 追加 `input_boolean:` / `input_datetime:` helper 声明（与 UI-created `last_night_unlock_alert_at` 共存）
- `automations.yaml` 清理掉 2 个历史 H02 重复项，加入新的"仅三元组过滤 + fire event"版本（`mode: queued, max: 5`）
- AppDaemon addon 重启后启动日志：`[night_guard] NightGuardOrchestrator 已启动 | camera=... cooldown=300.0s snapshot=5x5s`
- HA `automation.reload` / `input_boolean.reload` / `input_datetime.reload` 三次 reload 均成功，新 helper 就位
- 备份文件保留：`apps.yaml.bak-20260411-T14` / `automations.yaml.bak-20260411-T14` / `scripts.yaml.bak-20260411-T14` / `configuration.yaml.bak-20260411-T14`

### 12.3 端到端验证数据

| 测试 | 触发源 | 预期 | 实际 | 总耗时 |
|---|---|---|---|---|
| 真实开门 | `automation.H02` 在 15:14:03 真实触发 | 完整流程 + 三通道告警 | ✅ iOS Critical Alert 穿透勿扰送达 + 电话 CallId 156483434759 + 钉钉送达 + 抓拍 5 张 door_ever_opened=True + 快照通知 + 详情兜底 | ~30s（抓拍 25s + 通知 5s）|
| Test 1 完整路径 | curl fire `night_guard.door_unlock_trigger` 15:21:21 | 完整流程 | ✅ 同上，door_ever_opened=False 但 last_snapshot 仍然记录 | ~30s |
| Test 2 冷却拦截 | Test 1 后 1 分钟内再 fire 15:22:22 | 跳过且不发通知 | ✅ `冷却期内，跳过` | <5ms |
| Test 3 时段外 | 时段临时改 03:00~04:00，当前 15:23 | 跳过且不发通知 | ✅ `当前时间不在告警时段，跳过` | <5ms |
| Test 4 总开关关 | `input_boolean.night_guard_enabled=off` 15:23:23 | 跳过且不发通知 | ✅ `总开关关闭，跳过` | <3ms |

**关键里程碑 — iOS Critical Alert 穿透勿扰成功**：本次会话前 `force_sound=true` 的 iOS critical alert 一直无法穿透勿扰（早期测试显示推送没响铃），被列为遗留问题。本次通过 `orchestrator → fire_event("notify_service_request", channel="all", force_sound=True) → NotifyService._send_ios_push` 链路，iOS Companion App 成功送达 Critical Alert（截图确认：锁屏"⚠️ 重要"badge 可见，状态栏 🔕 勿扰图标存在但推送依然带声音）。

### 12.4 产线配置已恢复

```
input_boolean.night_guard_enabled: on
input_datetime.night_guard_start: 23:00:00
input_datetime.night_guard_end: 07:30:00
```

### 12.5 已知现象（非 bug）
- AppDaemon 日志出现 `Excessive time spent in callback idle. Thread entity: 'thread.async' - now complete after 00:26 (limit=10.0s)`
  - 原因：orchestrator 回调整体时长 ~26-30 秒（抓拍 5 次 × 5 秒 interval + 下游 iOS/电话 call_service 耗时）
  - 和之前 NotifyService 的 "Excessive time" 不同 —— NotifyService 那次是 sync 回调真的阻塞事件循环；orchestrator 这次是 async 回调，所有 `await asyncio.sleep` / `await self.call_service` 都正确让出事件循环，其他 AppDaemon app（gasmeter_cloud 等）不受影响
  - AppDaemon 的 "Excessive time" 警告对这两种情况都会报，阈值统一是 10s，对长业务流程的 async 回调是误报
  - 缓解方向：未来可以把"抓拍 + 观察"剥离为 `self.create_task(...)` 后台任务，让 `on_door_unlock_trigger` 在发完主告警后立即返回；本次不做（YAGNI）
- AppDaemon 日志中文乱码（中文字符显示为 `���`），是 `ha addons logs` 命令的 locale 问题，不影响功能

### 12.6 阶段 4 (TDD) 遗留风险项处理

| R3 期望 | 实施后结果 |
|---|---|
| 代码落地后做最终评审 | 代码已落地，`night_guard_orchestrator.py` 432 行 + 713 行测试，全部通过 + 端到端真实告警跑通，可送终审 |

### 12.7 R4 — Codex 终审（2026-04-11）

**结论**：**终审通过**

**Codex 复核要点**：
- 实施与 plan 一致：代码实现了 `asyncio.Lock` / 进程内冷却 / 3 段通知 / 抓拍循环；`apps.yaml.example` 已追加编排器段；§12 记录了部署与 5 场景验证
- 并发 race 测试确实覆盖真实冷却状态机：`Method.__get__` 绑定真实 `_check_cooldown`/`_update_cooldown`，mock `get_state`/`call_service`/`_fire_*`，两次 gather 后只允许一次 `_fire_first_alert`，证明有锁时 race 被覆盖
- 生产就绪度：可宣告一期完成，旧 script 按既定计划保留 1 周回滚窗口
- `Excessive time` 警告可接受：是抓拍 `5x sleep` 的长业务 async 回调统一阈值告警，非真正阻塞事件循环；是否拆 `create_task` 是后续优化，非一期前置
- 文档完整性：§12 基本完整；**原 §12.1 测试分类统计加起来 52 ≠ 55**，已在本次 commit 里修正为"纯函数 24 + App 类结构 2 + App 方法 24 + 主入口 5 = 55"

**无新增 blocking。**

---

# 实施 Plan（按 Task 顺序执行）

> 每个 Task 都是 TDD 节奏：先写测试 → 跑测试确认失败 → 写最小实现 → 跑测试确认通过 → commit。

## Task 1: 项目脚手架 — 测试基础设施

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `pyproject.toml` (add dev dependencies)

- [ ] **Step 1.1: 检查 pyproject.toml 现状**

Run: `cat pyproject.toml`

- [ ] **Step 1.2: 添加 dev 依赖**

编辑 `pyproject.toml`，确保存在：

```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
]
```

- [ ] **Step 1.3: 创建 tests/__init__.py**

```python
# 测试包占位
```

- [ ] **Step 1.4: 创建 tests/conftest.py**

（完整内容见 §6.2）

- [ ] **Step 1.5: 本地安装 dev 依赖**

Run: `pip install -e ".[dev]"`
Expected: 无错误

- [ ] **Step 1.6: 跑空 pytest 验证环境**

Run: `pytest tests/ --collect-only`
Expected: `collected 0 items` 或 `no tests collected`

- [ ] **Step 1.7: Commit**

```bash
git add pyproject.toml tests/__init__.py tests/conftest.py
git commit -m "test: 添加 pytest + pytest-asyncio dev 依赖和基础 fixtures"
```

## Task 2: 纯函数测试 —— 时段判断

**Files:**
- Create: `tests/test_night_guard_orchestrator.py`（首次创建）
- Create: `ha/appdaemon/apps/night_guard_orchestrator.py`（首次创建，只含纯函数占位）

- [ ] **Step 2.1: 写 is_in_alert_window 失败测试**

创建 `tests/test_night_guard_orchestrator.py`:

```python
from datetime import time

from night_guard_orchestrator import is_in_alert_window


class TestIsInAlertWindow:
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
```

- [ ] **Step 2.2: 创建 night_guard_orchestrator.py 空骨架**

```python
"""夜间门锁告警编排器 — AppDaemon App（占位，Task 2+ 逐步充实）"""
```

- [ ] **Step 2.3: 跑测试确认失败**

Run: `pytest tests/test_night_guard_orchestrator.py::TestIsInAlertWindow -v`
Expected: ImportError: cannot import name 'is_in_alert_window'

- [ ] **Step 2.4: 实现 is_in_alert_window**

在 `night_guard_orchestrator.py` 顶部添加：

```python
from datetime import time as _time_type


def is_in_alert_window(window_start, window_end, now):
    """判断 now 是否落在 [window_start, window_end) 时段内。支持跨天。"""
    if window_start == window_end:
        return False
    if window_start < window_end:
        return window_start <= now < window_end
    return now >= window_start or now < window_end
```

- [ ] **Step 2.5: 跑测试确认通过**

Run: `pytest tests/test_night_guard_orchestrator.py::TestIsInAlertWindow -v`
Expected: 9 passed

- [ ] **Step 2.6: Commit**

```bash
git add ha/appdaemon/apps/night_guard_orchestrator.py tests/test_night_guard_orchestrator.py
git commit -m "feat(orchestrator): 时段判断纯函数 is_in_alert_window + 单测"
```

## Task 3: 纯函数测试 —— 冷却判断 + 格式化

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`

- [ ] **Step 3.1: 写 should_alert 和格式化函数的失败测试**

追加到 `tests/test_night_guard_orchestrator.py`:

```python
from datetime import datetime, timedelta

from night_guard_orchestrator import (
    should_alert,
    build_timestamp_tag,
    build_time_display,
    build_snapshot_path,
    format_door_confirmation,
    build_first_alert_message,
    build_photo_caption,
    build_detail_message,
)


class TestShouldAlert:
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
```

- [ ] **Step 3.2: 跑测试确认失败**

Run: `pytest tests/test_night_guard_orchestrator.py -v`
Expected: ImportError 各个函数名

- [ ] **Step 3.3: 实现全部纯函数**

在 `night_guard_orchestrator.py` 追加（完整实现见 §5.2）：

```python
from datetime import datetime, time, timedelta


def should_alert(last_alert, cooldown, now):
    if last_alert is None:
        return True
    return (now - last_alert) >= cooldown


def build_timestamp_tag(now):
    return now.strftime("%Y%m%d_%H%M%S")


def build_time_display(now):
    return now.strftime("%H:%M:%S")


def build_snapshot_path(template, directory, timestamp, index):
    filename = template.format(timestamp=timestamp, index=index)
    if directory.endswith("/"):
        return directory + filename
    return f"{directory}/{filename}"


def format_door_confirmation(door_ever_opened, last_open_state, current_state):
    if door_ever_opened:
        return f"已确认开门（{last_open_state}）"
    return f"未确认开门（当前：{current_state}）"


def build_first_alert_message(time_display):
    return (
        f"时间：{time_display}\n"
        "门锁状态：门内按钮开锁\n"
        "正在抓拍电梯厅画面..."
    )


def build_photo_caption(time_display, door_text, snapshot_attempts):
    return (
        f"时间：{time_display}\n"
        f"门状态：{door_text}\n"
        f"抓拍：{snapshot_attempts} 次尝试"
    )


def build_detail_message(time_display, door_text, snapshot_attempts, has_any_snapshot, photo_attempted):
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
```

- [ ] **Step 3.4: 跑测试确认通过**

Run: `pytest tests/test_night_guard_orchestrator.py -v`
Expected: 全部 passed（19 个纯函数测试）

- [ ] **Step 3.5: Commit**

```bash
git add ha/appdaemon/apps/night_guard_orchestrator.py tests/test_night_guard_orchestrator.py
git commit -m "feat(orchestrator): 冷却判断 + 消息格式化纯函数 + 单测"
```

## Task 4: App 类骨架 + initialize 测试

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`
- Create: `tests/test_night_guard_init.py`（可选独立文件）

- [ ] **Step 4.1: 写 initialize 失败测试**

由于 AppDaemon 真实 Hass 基类初始化复杂，测试使用 `MagicMock(spec=NightGuardOrchestrator)` 方式，断言 `listen_event` 被调用。

追加到 `tests/test_night_guard_orchestrator.py`:

```python
from unittest.mock import MagicMock, AsyncMock

import pytest


import inspect


def test_orchestrator_class_signature():
    """Smoke test：验证 NightGuardOrchestrator 类存在关键方法且是 coroutine。

    这是对 MagicMock(spec=...) 的补充，防止 mock 对 AppDaemon API 约束过弱导致运行时报错。
    """
    from night_guard_orchestrator import NightGuardOrchestrator

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


@pytest.mark.asyncio
async def test_initialize_registers_listener(mock_hass_app):
    """调用真实 initialize 逻辑，验证 listen_event 被正确注册、Lock 已初始化。"""
    from night_guard_orchestrator import NightGuardOrchestrator

    # 直接绑定真实 initialize 到 mock 实例
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
```

- [ ] **Step 4.2: 跑测试确认失败**

Run: `pytest tests/test_night_guard_orchestrator.py::test_initialize_registers_listener -v`
Expected: ImportError or AttributeError: NightGuardOrchestrator 未定义

- [ ] **Step 4.3: 实现 App 类骨架和 initialize**

在 `night_guard_orchestrator.py` 追加：

```python
import asyncio

import appdaemon.plugins.hass.hassapi as hass


class NightGuardOrchestrator(hass.Hass):
    async def initialize(self):
        self.camera_entity = self.args["camera_entity"]
        self.door_state_entity = self.args["door_state_entity"]
        self.snapshot_count = int(self.args.get("snapshot_count", 5))
        self.snapshot_interval_seconds = int(self.args.get("snapshot_interval_seconds", 5))
        self.snapshot_dir = self.args.get("snapshot_dir", "/config/www")
        self.snapshot_filename_template = self.args.get(
            "snapshot_filename_template",
            "night_alert_{timestamp}_{index}.jpg",
        )
        self.cooldown = timedelta(seconds=int(self.args.get("cooldown_seconds", 300)))
        self.helper_enabled = self.args["helper_enabled"]
        self.helper_window_start = self.args["helper_window_start"]
        self.helper_window_end = self.args["helper_window_end"]
        self.helper_last_alert = self.args["helper_last_alert"]
        self.log_prefix = self.args.get("log_prefix", "[night_guard]")

        # 并发保护
        self._trigger_lock = asyncio.Lock()
        # 进程内兜底冷却（helper 不可用时的 fallback）
        self._in_process_last_alert: datetime | None = None

        self.listen_event(self.on_door_unlock_trigger, "night_guard.door_unlock_trigger")
        self.log(
            f"{self.log_prefix} NightGuardOrchestrator 已启动 | "
            f"camera={self.camera_entity} cooldown={self.cooldown.total_seconds()}s "
            f"snapshot={self.snapshot_count}x{self.snapshot_interval_seconds}s"
        )

    async def on_door_unlock_trigger(self, event_name, data, kwargs):
        """主入口占位，Task 11 填充完整逻辑。"""
        pass
```

**注意**：由于测试需要 mock 掉 hass.Hass 基类，可能需要在 conftest.py 里做 stub。如果 import 报错，在 conftest.py 顶部加：

```python
# Stub AppDaemon hassapi.Hass 基类，避免导入真实 AppDaemon
import sys
from unittest.mock import MagicMock

_fake_hassapi = MagicMock()
_fake_hassapi.Hass = type("Hass", (), {})
sys.modules["appdaemon"] = MagicMock()
sys.modules["appdaemon.plugins"] = MagicMock()
sys.modules["appdaemon.plugins.hass"] = MagicMock()
sys.modules["appdaemon.plugins.hass.hassapi"] = _fake_hassapi
```

- [ ] **Step 4.4: 跑测试确认通过**

Run: `pytest tests/test_night_guard_orchestrator.py::test_initialize_registers_listener -v`
Expected: 1 passed

- [ ] **Step 4.5: Commit**

```bash
git add ha/appdaemon/apps/night_guard_orchestrator.py tests/test_night_guard_orchestrator.py tests/conftest.py
git commit -m "feat(orchestrator): App 类骨架 + initialize 单测"
```

## Task 5: _guard_enabled 方法

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`

- [ ] **Step 5.1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_guard_enabled_on(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="on")
    result = await NightGuardOrchestrator._guard_enabled(mock_hass_app)
    assert result is True
    mock_hass_app.get_state.assert_called_once_with("input_boolean.test_enabled")


@pytest.mark.asyncio
async def test_guard_enabled_off(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="off")
    result = await NightGuardOrchestrator._guard_enabled(mock_hass_app)
    assert result is False


@pytest.mark.asyncio
async def test_guard_enabled_unavailable_defaults_to_true(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    result = await NightGuardOrchestrator._guard_enabled(mock_hass_app)
    assert result is True  # 默认放行，防止 helper 故障漏报
```

- [ ] **Step 5.2: 跑测试确认失败**

Run: `pytest tests/test_night_guard_orchestrator.py -k guard_enabled -v`
Expected: AttributeError: _guard_enabled not defined

- [ ] **Step 5.3: 实现 _guard_enabled**

在类里追加：

```python
async def _guard_enabled(self) -> bool:
    """读总开关；helper 不可用时默认放行。"""
    state = await self.get_state(self.helper_enabled)
    if state in (None, "unknown", "unavailable"):
        return True
    return state == "on"
```

- [ ] **Step 5.4: 跑测试确认通过**

Run: `pytest tests/test_night_guard_orchestrator.py -k guard_enabled -v`
Expected: 3 passed

- [ ] **Step 5.5: Commit**

```bash
git add ha/appdaemon/apps/night_guard_orchestrator.py tests/test_night_guard_orchestrator.py
git commit -m "feat(orchestrator): _guard_enabled 方法 + 单测"
```

## Task 6: _check_window 方法

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`

- [ ] **Step 6.1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_check_window_inside(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator

    async def fake_get_state(entity):
        if "start" in entity:
            return "23:00:00"
        if "end" in entity:
            return "07:30:00"
        return None

    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)
    result = await NightGuardOrchestrator._check_window(mock_hass_app, time(1, 37))
    assert result is True


@pytest.mark.asyncio
async def test_check_window_outside(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator

    async def fake_get_state(entity):
        if "start" in entity:
            return "23:00:00"
        if "end" in entity:
            return "07:30:00"
        return None

    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)
    result = await NightGuardOrchestrator._check_window(mock_hass_app, time(12, 0))
    assert result is False


@pytest.mark.asyncio
async def test_check_window_helper_unavailable(mock_hass_app):
    """helper 不可用时默认放行。"""
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    result = await NightGuardOrchestrator._check_window(mock_hass_app, time(12, 0))
    assert result is True
```

- [ ] **Step 6.2: 跑测试确认失败**
- [ ] **Step 6.3: 实现 _check_window**

```python
async def _check_window(self, now_time) -> bool:
    """读时段 helper，调用 is_in_alert_window；helper 不可用默认放行。"""
    start_state = await self.get_state(self.helper_window_start)
    end_state = await self.get_state(self.helper_window_end)

    def _parse(value):
        if value in (None, "unknown", "unavailable"):
            return None
        parts = value.split(":")
        return time(int(parts[0]), int(parts[1]))

    start = _parse(start_state)
    end = _parse(end_state)
    if start is None or end is None:
        return True  # 默认放行
    return is_in_alert_window(start, end, now_time)
```

- [ ] **Step 6.4: 跑测试确认通过**
- [ ] **Step 6.5: Commit**

## Task 7: _check_cooldown + _update_cooldown 方法

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`

- [ ] **Step 7.1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_check_cooldown_never_alerted(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="unknown")
    result = await NightGuardOrchestrator._check_cooldown(mock_hass_app, datetime(2026, 4, 11, 1, 0))
    assert result is True


@pytest.mark.asyncio
async def test_check_cooldown_within(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="2026-04-11 00:58:00")
    result = await NightGuardOrchestrator._check_cooldown(mock_hass_app, datetime(2026, 4, 11, 1, 0))
    assert result is False  # 距今 120 秒，小于 300 秒冷却


@pytest.mark.asyncio
async def test_check_cooldown_past(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="2026-04-11 00:50:00")
    result = await NightGuardOrchestrator._check_cooldown(mock_hass_app, datetime(2026, 4, 11, 1, 0))
    assert result is True  # 距今 600 秒


@pytest.mark.asyncio
async def test_update_cooldown_calls_service(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    now = datetime(2026, 4, 11, 1, 37, 33)
    mock_hass_app._in_process_last_alert = None
    await NightGuardOrchestrator._update_cooldown(mock_hass_app, now)
    mock_hass_app.call_service.assert_called_once()
    call_args = mock_hass_app.call_service.call_args
    assert call_args[0][0] == "input_datetime/set_datetime"
    assert call_args[1]["entity_id"] == "input_datetime.test_last"
    # 进程内兜底同时被更新
    assert mock_hass_app._in_process_last_alert == now


@pytest.mark.asyncio
async def test_check_cooldown_helper_unavailable_no_in_process(mock_hass_app):
    """helper 不可用 + 进程内兜底为空 → 放行。"""
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    mock_hass_app._in_process_last_alert = None
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 37, 33)
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_cooldown_helper_unavailable_in_process_active(mock_hass_app):
    """helper 不可用 + 进程内兜底在冷却期 → 拦截。"""
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    mock_hass_app._in_process_last_alert = datetime(2026, 4, 11, 1, 35, 0)
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 37, 0)
    )
    assert result is False  # 距今 120 秒 < 300 秒冷却


@pytest.mark.asyncio
async def test_check_cooldown_helper_unavailable_in_process_past(mock_hass_app):
    """helper 不可用 + 进程内兜底已过冷却 → 放行。"""
    from night_guard_orchestrator import NightGuardOrchestrator
    mock_hass_app.get_state = AsyncMock(return_value="unavailable")
    mock_hass_app._in_process_last_alert = datetime(2026, 4, 11, 1, 30, 0)
    result = await NightGuardOrchestrator._check_cooldown(
        mock_hass_app, datetime(2026, 4, 11, 1, 37, 0)
    )
    assert result is True  # 距今 420 秒 > 300 秒冷却
```

- [ ] **Step 7.2: 跑测试确认失败**
- [ ] **Step 7.3: 实现**

```python
async def _check_cooldown(self, now: datetime) -> bool:
    """冷却判断：优先读 helper，helper 不可用时回退到进程内兜底。"""
    state = await self.get_state(self.helper_last_alert)

    # helper 不可用 → 进程内兜底
    if state in (None, "unknown", "unavailable", ""):
        if self._in_process_last_alert is None:
            self.log(f"{self.log_prefix} helper 不可用且进程内冷却为空，首次放行",
                     level="WARNING")
            return True
        self.log(f"{self.log_prefix} helper 不可用，使用进程内冷却 last={self._in_process_last_alert}",
                 level="WARNING")
        return should_alert(self._in_process_last_alert, self.cooldown, now)

    # helper 可用 → 尝试解析
    try:
        last = datetime.strptime(state, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            last = datetime.fromisoformat(state)
        except ValueError:
            self.log(f"{self.log_prefix} 无法解析 last_alert 时间戳: {state}，走进程内兜底",
                     level="WARNING")
            if self._in_process_last_alert is None:
                return True
            return should_alert(self._in_process_last_alert, self.cooldown, now)
    return should_alert(last, self.cooldown, now)


async def _update_cooldown(self, now: datetime) -> None:
    """同时更新 HA helper 和进程内兜底变量。"""
    self._in_process_last_alert = now
    await self.call_service(
        "input_datetime/set_datetime",
        entity_id=self.helper_last_alert,
        datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
    )
```

- [ ] **Step 7.4: 跑测试确认通过**
- [ ] **Step 7.5: Commit**

## Task 8: _fire_first_alert 方法

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`

- [ ] **Step 8.1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_fire_first_alert_correct_event_data(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    await NightGuardOrchestrator._fire_first_alert(mock_hass_app, "20260411_013733", "01:37:33")
    mock_hass_app.fire_event.assert_called_once()
    call_args = mock_hass_app.fire_event.call_args
    assert call_args[0][0] == "notify_service_request"
    kwargs = call_args[1]
    assert kwargs["channel"] == "all"
    assert kwargs["force_sound"] is True
    assert kwargs["request_id"] == "night_unlock_20260411_013733"
    assert kwargs["source"] == "night_guard.orchestrator"
    assert "01:37:33" in kwargs["message"]
```

- [ ] **Step 8.2: 跑测试确认失败**
- [ ] **Step 8.3: 实现**

```python
async def _fire_first_alert(self, timestamp: str, time_display: str) -> None:
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
    self.log(f"{self.log_prefix} 主告警已发 request_id=night_unlock_{timestamp}")
```

- [ ] **Step 8.4: 跑测试确认通过**
- [ ] **Step 8.5: Commit**

## Task 9: _run_snapshot_loop 方法

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`

- [ ] **Step 9.1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_snapshot_loop_all_available(mock_hass_app, monkeypatch):
    """摄像头全程 available，预期返回最后一张路径 + door_ever_opened 根据状态决定。"""
    from night_guard_orchestrator import NightGuardOrchestrator
    # 模拟 camera 实体状态
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
    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio, "sleep", AsyncMock())

    result = await NightGuardOrchestrator._run_snapshot_loop(mock_hass_app, "20260411_013733")

    assert result["last_successful_snapshot"].endswith("_3.jpg")
    assert result["door_ever_opened"] is True
    assert result["door_opened_state"] == "已开锁"
    assert result["last_door_state"] == "已开锁"
    assert mock_hass_app.call_service.call_count == 3  # snapshot_count


@pytest.mark.asyncio
async def test_snapshot_loop_camera_unavailable(mock_hass_app, monkeypatch):
    """摄像头全程 unavailable，预期返回空路径。"""
    from night_guard_orchestrator import NightGuardOrchestrator
    async def fake_get_state(entity):
        if entity == "camera.test_cam":
            return "unavailable"
        return "已上锁"

    mock_hass_app.get_state = AsyncMock(side_effect=fake_get_state)
    mock_hass_app.call_service = AsyncMock()

    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio, "sleep", AsyncMock())

    result = await NightGuardOrchestrator._run_snapshot_loop(mock_hass_app, "20260411_013733")

    assert result["last_successful_snapshot"] == ""
    assert result["door_ever_opened"] is False
    assert result["last_door_state"] == "已上锁"
```

- [ ] **Step 9.2: 跑测试确认失败**
- [ ] **Step 9.3: 实现**

```python
async def _run_snapshot_loop(self, timestamp: str) -> dict:
    """连续抓拍 + 门状态观察。"""
    last_successful = ""
    door_ever_opened = False
    door_opened_state = ""
    last_door_state = ""  # 最后一次读到的门状态，供下游 format 使用

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
            self.log(f"{self.log_prefix} 抓拍 {i} 失败: {e}", level="WARNING")

        # 摄像头可用时记录候选路径
        cam_state = await self.get_state(self.camera_entity)
        if cam_state not in ("unavailable", "unknown", None):
            last_successful = path

        # 门状态观察
        door_state = await self.get_state(self.door_state_entity)
        if door_state:
            last_door_state = door_state
        if not door_ever_opened and door_state and (
            "已开锁" in door_state or "虚掩" in door_state or "门未关" in door_state
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
```

文件头需要 `import asyncio`。下游 `_fire_snapshot_notification` 和 `_fire_detail_fallback` 使用 `snapshot_result["last_door_state"]` 作为 current_state 传给 `format_door_confirmation`，而不是硬编码 `"unknown"` 或额外再读一次 HA 状态。

- [ ] **Step 9.4: 跑测试确认通过**
- [ ] **Step 9.5: Commit**

## Task 10: _fire_snapshot_notification + _fire_detail_fallback 方法

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`

- [ ] **Step 10.1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_fire_snapshot_notification_with_snapshot(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
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


@pytest.mark.asyncio
async def test_fire_snapshot_notification_no_snapshot(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
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


@pytest.mark.asyncio
async def test_fire_detail_fallback(mock_hass_app):
    from night_guard_orchestrator import NightGuardOrchestrator
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
    assert "详情" in kwargs["message"] or "📋" in kwargs["message"]


@pytest.mark.asyncio
async def test_fire_detail_fallback_uses_last_door_state(mock_hass_app):
    """当 door_ever_opened=False 时，message 里的 current 状态应来自 last_door_state，
    而不是硬编码 'unknown' 或再读实体。"""
    from night_guard_orchestrator import NightGuardOrchestrator
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
```

- [ ] **Step 10.2: 跑测试确认失败**
- [ ] **Step 10.3: 实现**

```python
async def _fire_snapshot_notification(self, timestamp, time_display, snapshot_result) -> bool:
    if not snapshot_result["last_successful_snapshot"]:
        self.log(f"{self.log_prefix} 无快照，跳过快照通知")
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
    return True


async def _fire_detail_fallback(self, timestamp, time_display, snapshot_result, photo_attempted) -> None:
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
```

- [ ] **Step 10.4: 跑测试确认通过**
- [ ] **Step 10.5: Commit**

## Task 11: on_door_unlock_trigger 主入口集成测试（含并发锁）

**Files:**
- Modify: `ha/appdaemon/apps/night_guard_orchestrator.py`
- Modify: `tests/test_night_guard_orchestrator.py`

**设计要点**：
- 主入口被 `asyncio.Lock` 包住，确保并发事件串行处理
- 测试需要 mock 里给出 `_trigger_lock = asyncio.Lock()`，以便 `async with` 正常工作
- 补一个并发测试：同时 fire 2 个事件，预期第二个被冷却拦截

- [ ] **Step 11.1: 写 5 个主入口失败测试**

```python
@pytest.fixture
def trigger_ready_app(mock_hass_app):
    """给 mock_hass_app 注入 _trigger_lock，方便 on_door_unlock_trigger 测试。"""
    mock_hass_app._trigger_lock = asyncio.Lock()
    mock_hass_app._in_process_last_alert = None
    return mock_hass_app


@pytest.mark.asyncio
async def test_on_trigger_guard_disabled(trigger_ready_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    trigger_ready_app._guard_enabled = AsyncMock(return_value=False)
    trigger_ready_app._check_window = AsyncMock()
    trigger_ready_app._fire_first_alert = AsyncMock()

    await NightGuardOrchestrator.on_door_unlock_trigger(
        trigger_ready_app, "event", {"source": "test"}, {}
    )

    trigger_ready_app._check_window.assert_not_called()
    trigger_ready_app._fire_first_alert.assert_not_called()


@pytest.mark.asyncio
async def test_on_trigger_outside_window(trigger_ready_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    trigger_ready_app._guard_enabled = AsyncMock(return_value=True)
    trigger_ready_app._check_window = AsyncMock(return_value=False)
    trigger_ready_app._check_cooldown = AsyncMock()
    trigger_ready_app._fire_first_alert = AsyncMock()

    await NightGuardOrchestrator.on_door_unlock_trigger(
        trigger_ready_app, "event", {"source": "test"}, {}
    )

    trigger_ready_app._check_cooldown.assert_not_called()
    trigger_ready_app._fire_first_alert.assert_not_called()


@pytest.mark.asyncio
async def test_on_trigger_cooldown_active(trigger_ready_app):
    from night_guard_orchestrator import NightGuardOrchestrator
    trigger_ready_app._guard_enabled = AsyncMock(return_value=True)
    trigger_ready_app._check_window = AsyncMock(return_value=True)
    trigger_ready_app._check_cooldown = AsyncMock(return_value=False)
    trigger_ready_app._update_cooldown = AsyncMock()
    trigger_ready_app._fire_first_alert = AsyncMock()

    await NightGuardOrchestrator.on_door_unlock_trigger(
        trigger_ready_app, "event", {"source": "test"}, {}
    )

    trigger_ready_app._update_cooldown.assert_not_called()
    trigger_ready_app._fire_first_alert.assert_not_called()


@pytest.mark.asyncio
async def test_on_trigger_full_path(trigger_ready_app):
    from night_guard_orchestrator import NightGuardOrchestrator
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


@pytest.mark.asyncio
async def test_on_trigger_concurrent_second_blocked_by_cooldown(trigger_ready_app):
    """并发触发防御：用真实的 _check_cooldown + _update_cooldown 逻辑验证 race 被 Lock 覆盖。

    构造方式：
    - 让 helper `last_alert` 状态返回 "unknown"，强制 _check_cooldown 走进程内兜底路径
    - 进程内初始 _in_process_last_alert = None，第一次会放行
    - Lock 保证 _update_cooldown 写入 _in_process_last_alert 之前第二次不能进入 _check_cooldown
    - 第二次进入时读到已被第一次写入的 _in_process_last_alert，真实 should_alert 判定为冷却期内
    - 断言只有一次真正进入 _fire_first_alert

    如果去掉 asyncio.Lock，两个协程会并发跑完 _check_cooldown（都拿到 True），然后两次
    都进入 _update_cooldown + _fire_first_alert，断言就会失败。这就是 race 被真正覆盖的证据。
    """
    from night_guard_orchestrator import NightGuardOrchestrator

    # 不 mock 这 3 个方法，使用类的真实实现
    trigger_ready_app._guard_enabled = NightGuardOrchestrator._guard_enabled.__get__(trigger_ready_app)
    trigger_ready_app._check_window = NightGuardOrchestrator._check_window.__get__(trigger_ready_app)
    trigger_ready_app._check_cooldown = NightGuardOrchestrator._check_cooldown.__get__(trigger_ready_app)
    trigger_ready_app._update_cooldown = NightGuardOrchestrator._update_cooldown.__get__(trigger_ready_app)

    # get_state 桩：总开关 on、时段覆盖 now、冷却 helper 返回 unknown（走进程内兜底）
    async def fake_get_state(entity):
        if entity == "input_boolean.test_enabled":
            return "on"
        if entity == "input_datetime.test_start":
            return "00:00:00"
        if entity == "input_datetime.test_end":
            return "23:59:00"  # 保证当前时间在窗口内
        if entity == "input_datetime.test_last":
            return "unknown"  # 触发进程内兜底冷却
        return None

    trigger_ready_app.get_state = AsyncMock(side_effect=fake_get_state)
    trigger_ready_app.call_service = AsyncMock()

    # 下游方法仍 mock 掉，避免真的去跑抓拍和发事件
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

    # 核心断言：虽然两次都进入了回调，Lock + 真实 _check_cooldown 保证只有 1 次真的往下走
    assert trigger_ready_app._fire_first_alert.call_count == 1
    assert trigger_ready_app._run_snapshot_loop.call_count == 1
    # 进程内冷却戳被第一次写入
    assert trigger_ready_app._in_process_last_alert is not None
```

**注意**：上面测试为了复用真实方法，使用 `Method.__get__(instance)` 把未绑定方法绑到 mock 实例上。这依赖 Python 描述符协议，在 CPython 上稳定可靠。若测试框架对描述符访问有限制，可退回为 `types.MethodType(NightGuardOrchestrator._check_cooldown, trigger_ready_app)` 写法，效果相同。

- [ ] **Step 11.2: 跑测试确认失败**
- [ ] **Step 11.3: 实现 on_door_unlock_trigger 完整逻辑（含 Lock）**

```python
async def on_door_unlock_trigger(self, event_name, data, kwargs):
    # ⭐ asyncio.Lock 保护：并发事件串行进入，check-then-set 原子
    async with self._trigger_lock:
        now = datetime.now()
        source = data.get("source", "unknown")
        triggered_at = data.get("triggered_at", "unknown")
        self.log(f"{self.log_prefix} 收到触发 source={source} triggered_at={triggered_at}")

        if not await self._guard_enabled():
            self.log(f"{self.log_prefix} 总开关关闭，跳过")
            return

        if not await self._check_window(now.time()):
            self.log(f"{self.log_prefix} 不在告警时段，跳过")
            return

        if not await self._check_cooldown(now):
            self.log(f"{self.log_prefix} 冷却期内，跳过")
            return

        # 通过冷却检查，立即写冷却（helper + 进程内）
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

        self.log(f"{self.log_prefix} 告警流程完成 timestamp={timestamp}")
```

- [ ] **Step 11.4: 跑测试确认通过**
- [ ] **Step 11.5: Commit**

## Task 12: 全量回归

- [ ] **Step 12.1: 跑整套 pytest**

Run: `pytest tests/ -v`
Expected: 全部通过（预计 ~30 个测试）

- [ ] **Step 12.2: 语法检查**

Run: `python3 -m py_compile ha/appdaemon/apps/night_guard_orchestrator.py`
Expected: 无输出

- [ ] **Step 12.3: 检查 import 顺序与格式**

Run: `cat ha/appdaemon/apps/night_guard_orchestrator.py | head -20`
Expected: 文件结构清晰

- [ ] **Step 12.4: Commit（若有微调）**

## Task 13: 更新 apps.yaml.example

**Files:**
- Modify: `ha/appdaemon/apps.yaml.example`

- [ ] **Step 13.1: 追加 night_guard_orchestrator 段**

在文件末尾添加（保留占位符）：

```yaml
# ── 夜间监护编排器 ──
# 监听 night_guard.door_unlock_trigger 事件，执行告警流程
night_guard_orchestrator:
  module: night_guard_orchestrator
  class: NightGuardOrchestrator

  # 设备实体
  camera_entity: "camera.dian_ti_ting_mainstream"
  door_state_entity: "sensor.xiaomi_cn_1150511669_s20pro_door_state_p_3_1021"

  # 抓拍参数
  snapshot_count: 5
  snapshot_interval_seconds: 5
  snapshot_dir: "/config/www"
  snapshot_filename_template: "night_alert_{timestamp}_{index}.jpg"

  # 冷却（秒）
  cooldown_seconds: 300

  # HA helper 实体（需先在 HA UI 创建）
  helper_enabled: "input_boolean.night_guard_enabled"
  helper_window_start: "input_datetime.night_guard_start"
  helper_window_end: "input_datetime.night_guard_end"
  helper_last_alert: "input_datetime.last_night_unlock_alert_at"

  log_prefix: "[night_guard]"
```

- [ ] **Step 13.2: Commit**

## Task 14: 部署到 HA（SSH）

> **部署目标都是 HA 上的实际运行文件**（monolithic 模式），不是仓库副本。仓库 `ha/packages/*.yaml` 是源码副本，部署完后单独同步。
>
> **命令统一**：本项目所有 AppDaemon 操作使用 `ha addons` legacy 命令（HA Supervisor CLI 向后兼容，与历史 commit 中的脚本风格保持一致；`ha apps` 是新别名但非必须）。

- [ ] **Step 14.1: 备份 HA 侧 4 个关键文件**

Run:
```bash
ssh root@192.168.77.253 'TS=20260411-T14 && \
  cp /addon_configs/a0d7b954_appdaemon/apps/apps.yaml /addon_configs/a0d7b954_appdaemon/apps/apps.yaml.bak-$TS && \
  cp /homeassistant/automations.yaml /homeassistant/automations.yaml.bak-$TS && \
  cp /homeassistant/scripts.yaml /homeassistant/scripts.yaml.bak-$TS && \
  cp /homeassistant/configuration.yaml /homeassistant/configuration.yaml.bak-$TS'
```

- [ ] **Step 14.2: scp orchestrator.py 到 AppDaemon 目录**

Run:
```bash
scp ha/appdaemon/apps/night_guard_orchestrator.py root@192.168.77.253:/addon_configs/a0d7b954_appdaemon/apps/
```

- [ ] **Step 14.3: 编辑 HA 上的 apps.yaml，追加 night_guard_orchestrator 段**

通过 SSH + heredoc（示例，把 `<...>` 换成真实实体名）:

```bash
ssh root@192.168.77.253 'cat >> /addon_configs/a0d7b954_appdaemon/apps/apps.yaml' <<'EOF'

night_guard_orchestrator:
  module: night_guard_orchestrator
  class: NightGuardOrchestrator
  camera_entity: "camera.dian_ti_ting_mainstream"
  door_state_entity: "sensor.xiaomi_cn_1150511669_s20pro_door_state_p_3_1021"
  snapshot_count: 5
  snapshot_interval_seconds: 5
  snapshot_dir: "/config/www"
  snapshot_filename_template: "night_alert_{timestamp}_{index}.jpg"
  cooldown_seconds: 300
  helper_enabled: "input_boolean.night_guard_enabled"
  helper_window_start: "input_datetime.night_guard_start"
  helper_window_end: "input_datetime.night_guard_end"
  helper_last_alert: "input_datetime.last_night_unlock_alert_at"
  log_prefix: "[night_guard]"
EOF
```

- [ ] **Step 14.4: SSH 编辑 `/homeassistant/configuration.yaml` 声明新 helper**

HA 对 `input_boolean` / `input_datetime` 这类配置型实体**不开放 REST API 动态创建**，必须通过 `configuration.yaml` 或 HA UI 手动创建。本步骤用 configuration.yaml 便于脚本化和版本管理。

SSH 到 HA 后手工用 `vi`/`nano` 编辑，或用下面的 heredoc 追加（**确认 configuration.yaml 里没有已存在的 input_boolean/input_datetime 段**，如果有需要合并而非追加）：

```bash
# 先 grep 检查是否已有相关段
ssh root@192.168.77.253 'grep -n "^input_boolean:\|^input_datetime:" /homeassistant/configuration.yaml'
```

- 如果输出为空：直接追加（见下）
- 如果输出非空：不能追加，需要 `vi` 手动合并到现有段下

追加写法（首次情况）：

```bash
ssh root@192.168.77.253 'cat >> /homeassistant/configuration.yaml' <<'EOF'

input_boolean:
  night_guard_enabled:
    name: "夜间监护总开关"
    initial: true
    icon: mdi:shield-home

input_datetime:
  night_guard_start:
    name: "告警开始时间"
    has_date: false
    has_time: true
    initial: "23:00:00"
    icon: mdi:clock-start
  night_guard_end:
    name: "告警结束时间"
    has_date: false
    has_time: true
    initial: "07:30:00"
    icon: mdi:clock-end
  # last_night_unlock_alert_at 已存在（由旧 H02 创建），不在此重复定义
EOF
```

**注意**：如果 automations.yaml / scripts.yaml 里原来就定义了 `input_datetime.last_night_unlock_alert_at`，它会在 configuration.yaml 生效后与新的 `night_guard_start` / `_end` 合并到同一个 `input_datetime:` 域下。HA 不允许同一配置域出现两次 `input_datetime:` 顶级键。若 configuration.yaml 里已有 `input_datetime:` 段，合并到同一段下。

- [ ] **Step 14.5: SSH 编辑 `/homeassistant/automations.yaml`，把 H02 改为新版**

用 `vi` 或 Python 脚本定位 `- id: night_door_unlock_alert` 条目，替换整段为 §4.3 的新版。

粗略 sed 方案不适合（YAML 多行复杂）。建议：
```bash
ssh root@192.168.77.253 'vi /homeassistant/automations.yaml'
# 手工查找 night_door_unlock_alert 并整块替换
```

**验收**：替换后的 H02 应只有 1 个 condition（三元组），action 里是 `event: night_guard.door_unlock_trigger` 而不是 `script.send_night_unlock_alert`。

- [ ] **Step 14.6: reload HA 配置**

```bash
TOKEN='<HA long-lived token>'

curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://192.168.77.253:8123/api/services/input_boolean/reload

curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://192.168.77.253:8123/api/services/input_datetime/reload

curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://192.168.77.253:8123/api/services/automation/reload
```

**验证 helper 存在**：
```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://192.168.77.253:8123/api/states/input_boolean.night_guard_enabled
```
预期返回包含 `"state": "on"` 的 JSON。

- [ ] **Step 14.7: 重启 AppDaemon addon**

Run:
```bash
ssh root@192.168.77.253 'ha addons restart a0d7b954_appdaemon'
```

（legacy 命令，HA Supervisor CLI 向后兼容；本项目既有部署脚本统一使用此命令。）

- [ ] **Step 14.8: 观察日志确认加载成功**

Run:
```bash
ssh root@192.168.77.253 'ha addons logs a0d7b954_appdaemon 2>&1 | grep -iE "night_guard|NightGuard|error|traceback" | tail -20'
```

Expected:
- 看到 `[night_guard] NightGuardOrchestrator 已启动` 日志（中文可能在 addon 日志里乱码，匹配 `NightGuardOrchestrator` 即可）
- 无 `Traceback` 或 `ERROR notify_service` / `ERROR night_guard`

## Task 15: 端到端验证

- [ ] **Step 15.1: 手动 fire 测试事件**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source":"manual-test","triggered_at":"2026-04-11T14:00:00"}' \
  http://192.168.77.253:8123/api/events/night_guard.door_unlock_trigger
```

观察日志顺序：总开关 → 时段 → 冷却 → 主告警 → 抓拍 5 次 → 快照 → 详情。

- [ ] **Step 15.2: 验证钉钉 + iOS 通道**

确认收到三条通知（主告警走 `channel=all`，如 phone_enabled=false 则电话不响）。

- [ ] **Step 15.3: 验证冷却**

立即再次 fire 相同事件，预期日志显示"冷却期内，跳过"。

- [ ] **Step 15.4: 验证时段外**

临时把 `input_datetime.night_guard_start` 改为当前时间 + 1 小时，
`night_guard_end` 改为当前 + 2 小时，fire 事件，预期"不在告警时段，跳过"。

- [ ] **Step 15.5: 验证总开关**

把 `input_boolean.night_guard_enabled` 关闭，fire 事件，预期"总开关关闭，跳过"。

- [ ] **Step 15.6: 真实开门测试**

把时段恢复到宽松（如 00:00~23:59），真实从门内按钮开门一次，预期触发完整流程。

- [ ] **Step 15.7: 记录端到端时序数据**

记录：
- 事件到达 → 第一条通知延迟
- 整个流程总耗时
- 三通道送达情况

## Task 16: 最终报告 + 清理

- [ ] **Step 16.1: 写完成报告**

写到 `docs/plans/2026-04-11-NightGuardOrchestrator迁移方案.md` §11「评审历史」和 §12「实施结果」章节。

- [ ] **Step 16.2: 发 codex 最终确认**

用 `codex:rescue` 发送报告，等结论。

- [ ] **Step 16.3: 把旧 script 标记为"保留 1 周后删除"**

在 scripts.yaml 的 `send_night_unlock_alert` 上方加注释：

```yaml
# DEPRECATED 2026-04-11: 已迁移到 AppDaemon NightGuardOrchestrator。
# 保留至 2026-04-18 作为回滚路径，确认稳定后删除。
```

- [ ] **Step 16.4: git push**

---

## 自检清单（写完 plan 自己过一遍）

- [x] §2 分层边界清晰（automation 过滤 + orchestrator 业务 + NotifyService 送达）
- [x] §3 事件契约具体（字段、类型、示例）
- [x] §4 配置模型完整（helper + apps.yaml + automation）
- [x] §5 代码结构完整（文件清单、模块划分、纯函数完整实现、并发锁 + 进程内兜底冷却）
- [x] §6 测试覆盖分层（纯函数 + 方法 + 主入口 + 并发 race 真实覆盖）
- [x] §7 部署步骤具体（SSH 命令 + curl + helper 创建），明确 HA monolithic vs 仓库副本
- [x] §8 回滚预案 < 5 分钟（含 SSH + 编辑 + reload + 验证的人工操作时间）
- [x] §9 风险和决策点（helper 不可用分级处理：总开关/时段默认放行；冷却 helper 走进程内兜底）
- [x] §10 YAGNI 明确不做的事
- [x] Task 1-16 覆盖从零到交付的全流程
- [x] 每个 Task 都是 TDD 节奏
- [x] 所有 Task 都有具体代码和命令
- [x] R1 → R2 的 4 项 blocking 修复全部落地
- [x] R2 → R3 的并发测试 mock 设计修正（用真实 _check_cooldown 路径）
