# Claude Code 启动提示词

把下面这段发给 Claude Code 即可：

```text
请在当前项目目录中工作，并严格按现有文档执行。

先不要扩展二期，不要恢复旧 Python 主线，不要另起一套通知实现。

开始前请按以下顺序读取并理解：

1. README.md
2. PROJECT_MEMORY.md
3. docs/需求文档-v2-HA自动化.md
4. docs/plans/2026-04-10-HA一期自动化实现草案.md
5. docs/notify-service-design.md
6. docs/设备与系统清单.md
7. docs/验收测试方案.md
8. docs/claude-code-执行说明.md
9. docs/任务清单.md
10. docs/自测清单.md
11. ha/packages/night_guard_automations.yaml
12. ha/packages/night_guard_scripts.yaml

当前主线已经明确切换为：

- HA 自动化负责触发、编排、冷却、抓拍
- HA script 负责整理通知参数
- AppDaemon `NotifyService` 负责统一通知

当前一期范围只有 3 项：

- 夜间门内开锁告警
- 夜间童锁守护
- 统一通知服务 NotifyService

当前明确不做：

- 二期 AI 增强
- 旧 Python RTSP 主流程
- 通用人物识别
- 与当前主线无关的新后台/新前端

请直接继承以下已确认事实，不要重复发散设计：

- 门锁事件实体：
  `event.xiaomi_cn_1150511669_s20pro_lock_event_e_2_1020`
- 门状态实体：
  `sensor.xiaomi_cn_1150511669_s20pro_door_state_p_3_1021`
- 摄像头实体：
  `camera.dian_ti_ting_mainstream`
- 童锁开启服务：
  当前不可用，`xiaomi_home` 未暴露可调用能力
- 当前 Telegram 底层链路：
  `telegram_bot.send_message`
  `telegram_bot.send_photo`
- 当前业务主线要求：
  业务 YAML 不再直接调用 Telegram，而是统一发 `notify_service_request`
- 夜间门内开锁告警冷却：
  5 分钟
- 童锁提醒冷却：
  30 分钟
- 夜间时段：
  23:00 ~ 07:30

编码阶段必须采用 teams / 多 agent 协作方式，至少拆成 5 个角色：

- 方案负责人
- HA 自动化工程师
- HA Script / 编排工程师
- AppDaemon 通知服务工程师
- 联调 / 验证工程师

如果当前环境不支持 teams / 多 agent，必须先明确说明阻塞，不能静默退化成单 agent。

你当前的工作重点不是重写方案，而是基于现有文档和已有 YAML 实现，切换到新的通知主线。优先完成这些事：

1. 实现 `NotifyService`
- 监听 `notify_service_request`
- 支持 `channel / force_sound / image_path`
- Telegram 与电话通道独立容错
- 静默规则内置
- 配置通过 `apps.yaml` 注入

2. 改造现有夜间监护一期
- `ha/packages/night_guard_automations.yaml`
- `ha/packages/night_guard_scripts.yaml`
- 让业务层不再直接发 Telegram
- 改为构造并发出 `notify_service_request`

3. 保留当前一期业务约束
- 夜间门内开锁仍是主触发
- 童锁仍是“检测未开启 -> 提醒手动处理”
- 抓拍失败仍必须有明确文字兜底
- 冷却逻辑不能退化

4. 补齐验证与回填
- 对照 `docs/自测清单.md`
- 至少补 `NotifyService` 基础验证
- 至少补夜间门锁告警与童锁守护的联调记录
- 每完成一个任务都更新 `docs/任务清单.md`

实施要求：

- 先读现有实现，再做最小必要修改
- 不要把 token、chat_id、AccessKey、手机号等敏感信息写入仓库
- 如修改了主链路实现，同步更新 README 和 PROJECT_MEMORY
- 最终汇总时必须明确：
  - 改了哪些文件
  - 哪些工作由哪个角色完成
  - 做了哪些验证
  - 还剩哪些阻塞或风险

开始后先回复下面 6 点，再进入实施：

1. 你理解的当前一期目标
2. 你准备如何做多 agent 分工
3. 你准备先检查哪些现有文件
4. 你准备先实现 NotifyService 的哪些部分
5. 你准备如何改造 night_guard 自动化和脚本
6. 你将如何回填任务清单和自测清单
```
