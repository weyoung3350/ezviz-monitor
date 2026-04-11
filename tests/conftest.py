"""Pytest fixtures and stub modules for NightGuardOrchestrator tests.

这个 conftest.py 的主要作用：

1. 把 `ha/appdaemon/apps` 目录加入 sys.path，让 test_night_guard_orchestrator.py
   可以 `from night_guard_orchestrator import ...`
2. stub 掉 AppDaemon 的 hassapi 模块，避免 import NightGuardOrchestrator 时
   真的尝试连接 AppDaemon。被 stub 的 Hass 基类只是一个普通 type，不做任何事。
3. 提供 mock_hass_app fixture，MagicMock 模拟 AppDaemon App 实例的所有 self.* 调用。

注意：此 stub 只作用于本测试目录，对其他 tests/test_*.py 不影响
（它们不 import appdaemon）。
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── stub AppDaemon 模块（必须在 import NightGuardOrchestrator 之前生效）──
#
# 为什么要这么绕：`import appdaemon.plugins.hass.hassapi as hass` 在 CPython
# 里实际上是通过父模块的属性链访问最终模块的。MagicMock 的属性访问会动态生成
# 新的 MagicMock，这会覆盖 sys.modules 里的 stub。必须显式把每一层的属性链都
# 绑到正确的 stub，避免中间被 auto-MagicMock 截断。
_fake_hassapi = MagicMock()
_fake_hassapi.Hass = type("Hass", (), {})

_fake_hass_mod = MagicMock()
_fake_hass_mod.hassapi = _fake_hassapi

_fake_plugins = MagicMock()
_fake_plugins.hass = _fake_hass_mod

_fake_appdaemon = MagicMock()
_fake_appdaemon.plugins = _fake_plugins

sys.modules["appdaemon"] = _fake_appdaemon
sys.modules["appdaemon.plugins"] = _fake_plugins
sys.modules["appdaemon.plugins.hass"] = _fake_hass_mod
sys.modules["appdaemon.plugins.hass.hassapi"] = _fake_hassapi


# ── 把 AppDaemon apps 目录加入 sys.path，让 import night_guard_orchestrator 能找到 ──
_APPS_DIR = Path(__file__).parent.parent / "ha" / "appdaemon" / "apps"
if str(_APPS_DIR) not in sys.path:
    sys.path.insert(0, str(_APPS_DIR))


@pytest.fixture
def mock_hass_app():
    """构造一个 mock 的 AppDaemon Hass app 实例。

    所有 AppDaemon API 方法（get_state / call_service / fire_event /
    listen_event / run_in_executor / log）都是 MagicMock 或 AsyncMock，
    可以直接断言调用参数，也可以 override side_effect。

    配置字段（camera_entity / door_state_entity 等）预先填入测试用默认值，
    方便单测直接访问 self.xxx。
    """
    from datetime import timedelta

    app = MagicMock()

    # ── AppDaemon API ──
    app.log = MagicMock()
    app.get_state = AsyncMock()
    app.call_service = AsyncMock()
    app.fire_event = MagicMock()
    app.listen_event = MagicMock()
    app.run_in_executor = AsyncMock()

    # ── 配置参数（Task 4 initialize 会设置这些，测试里可直接覆盖）──
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

    # ── 并发锁和进程内冷却戳（Task 4 initialize 会真正创建）──
    app._trigger_lock = None
    app._in_process_last_alert = None

    return app
