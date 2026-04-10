# PROJECT_MEMORY

## 项目概况

- 项目名称：老人夜间外出监护
- 当前阶段：文档已切换到 HA 一期方案，通知层主线切到 AppDaemon `NotifyService`
- 运行环境：Home Assistant
- 当前一期目标：夜间门锁告警 + 夜间童锁守护

## 已确认需求

- 一期主目标是“杨孝治夜间独自外出风险”
- 一期主线为 HA 自动化触发 + AppDaemon 统一通知服务
- 门锁主触发信号：
  - `event.xiaomi_cn_1150511669_s20pro_lock_event_e_2_1020`
  - 操作方式 = `9`
  - 锁动作 = `2`
- 告警时段默认：`23:00 ~ 07:30`
- 电梯厅摄像头默认实体：`camera.dian_ti_ting_mainstream`
- 当前已验证通道是 Telegram；电话通道纳入统一通知服务架构，真实启用待联调
- 同一门锁告警规则默认 5 分钟冷却
- 告警触发后默认连续抓拍 5 次，每次间隔 5 秒
- 夜间童锁守护默认每 10 分钟检查一次
- 若童锁未开启，则发送 Telegram 提醒手动操作
- 同一童锁提醒默认 30 分钟冷却
- 二期 AI 增强暂只保留方向，不冻结具体实现

## 当前主文档

- 需求文档：`docs/需求文档-v2-HA自动化.md`
- 实现草案：`docs/plans/2026-04-10-HA一期自动化实现草案.md`
- 统一通知服务设计：`docs/notify-service-design.md`
- 设备与系统清单：`docs/设备与系统清单.md`
- 验收测试方案：`docs/验收测试方案.md`
- Claude Code 启动提示词：`docs/Claude-Code-启动提示词.md`
- Claude Code 执行说明：`docs/claude-code-执行说明.md`
- 任务清单：`docs/任务清单.md`
- 自测清单：`docs/自测清单.md`

## 当前建议架构

- `automation.night_door_unlock_alert`
  - 监听门内开锁
  - 判断夜间时段与冷却
  - 调用告警脚本或发出通知服务请求
- `script.send_night_unlock_alert`
  - 发送统一通知请求前的抓拍与门状态汇总
  - 连续抓拍
  - 发出 `notify_service_request`
- `appdaemon.NotifyService`
  - 统一处理 Telegram / 电话通道
  - 统一处理静默规则、强制响铃和通道容错
- `automation.night_child_lock_guard`
  - 按固定频率检查童锁
- `script.notify_child_lock_missing`
  - 读取童锁状态并发出通知服务请求
  - 受 30 分钟冷却限制

## 当前实现状态

- 文档主线已从旧 Python 方案切换到 HA 一期方案
- 通知层主线已从“YAML 直接发 Telegram”切到 `NotifyService`
- docs 已开始收敛，旧交付流程文档准备清理
- HA 一期实现草案与通知服务设计已输出
- 当前 HA 上已有 YAML 自动化版本，后续需切换接入统一通知服务

## 待验证事项

- 童锁状态在 HA 中对应的真实实体是什么
- AppDaemon `NotifyService` 的 Telegram 接入最终走 `telegram_bot` 还是 `notify` 封装
- 快照文件写入路径和 Telegram 图片访问路径如何配置最稳
- 夜间门内开锁事件的真实属性在 HA 中是否稳定一致
- 童锁未开启提醒的 30 分钟冷却实现是否与实际自动化一致
- 电话通道在 AppDaemon 中的真实可用性和依赖安装状态

## 当前已知风险

- `xiaomi_home` 当前未暴露童锁开启服务，一期无法实现自动补开
- 摄像头抓拍路径与 Telegram 发送链路如果不通，告警体验会退化为纯文字
- 统一通知服务切入后，现有 HA 自动化和脚本需要同步改造，否则文档与实现会分叉
- 二期 AI 是否真的需要引入，仍要以一期实际运行结果决定

## 当前验收结论

- 当前仍处于方案与文档收敛阶段
- 一期通知层架构已变更，需按新主线重新组织实现与验证
- 二期不作为当前阻塞项

## 下一步

- 先统一需求文档、实现草案、验收与 Claude 执行文档口径
- 再实现 AppDaemon `NotifyService` 并把夜间监护一期切过去
- 最后做一期联调与验收记录
