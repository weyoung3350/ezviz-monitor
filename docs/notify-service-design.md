# HA 统一通知服务 NotifyService — 实施设计稿

> 当前版本是一期正式实施文档，不是预研稿。  
> 当前主线：`HA 自动化 / script + AppDaemon NotifyService`。

## 1. 目的

`NotifyService` 是一期的统一通知层，负责把业务侧发出的通知请求转换成实际的 Telegram / 电话下发。

当前要解决的不是“做一个通用通知平台”，而是先把夜间监护一期的通知链路收口：

- 业务 YAML 不再直接调用 `telegram_bot.send_message/send_photo`
- 夜间门内开锁告警统一走 `notify_service_request`
- 静默规则、强制响铃、图片发送、电话容错都放到同一层处理

## 2. 当前范围

当前一期内，`NotifyService` 只服务 1 条业务链路：

1. 夜间门内开锁告警

当前一期内，`NotifyService` 只要求做到：

- 监听 `notify_service_request`
- 支持 Telegram 通道
- 支持电话通道接口位
- 支持 `force_sound`
- 支持图片路径
- 支持双通道独立容错
- 记录明确日志

当前一期明确不做：

- 多接收人
- 通知模板中心
- 持久化通知历史
- 自动重试
- 防刷合并
- 邮件、微信、短信等更多通道

## 3. 主线边界

职责分层固定如下：

- HA `automation`
  - 负责触发、时段判断、冷却判断
- HA `script`
  - 负责抓拍、门状态观察、整理通知参数
- AppDaemon `NotifyService`
  - 负责统一下发 Telegram / 电话
  - 负责静默规则和 `force_sound`
  - 负责通道错误隔离

禁止的实现方式：

- 业务 YAML 再次直接调用 `telegram_bot.send_message/send_photo`
- 在多个业务脚本里重复写静默规则
- 在业务层自己判断“电话失败后怎么办”

## 4. 文件落点

本次实施涉及的目标文件固定为：

- `ha/appdaemon/apps/notify_service.py`
- `ha/appdaemon/apps.yaml.example`
- `ha/packages/night_guard_automations.yaml`
- `ha/packages/night_guard_scripts.yaml`
- `docs/notify-service-design.md`

部署到 HA 时的目标位置：

- `ha/appdaemon/apps/notify_service.py`
  - `/addon_configs/a0d7b954_appdaemon/apps/notify_service.py`
- `ha/appdaemon/apps.yaml.example`
  - 内容合并到 `/addon_configs/a0d7b954_appdaemon/apps/apps.yaml`

## 5. 统一事件接口

### 5.1 请求事件

事件名固定为：

```yaml
event_type: notify_service_request
```

事件体定义如下：

```yaml
event_data:
  request_id: "night_unlock_20260410_013733"   # 可选，建议业务侧传
  source: "night_guard.unlock_alert"           # 可选，建议业务侧传

  channel: "telegram"                          # telegram / phone / all

  title: "夜间门内开锁告警"                    # 可选
  message: "时间：01:37\n门状态：已确认开门"    # Telegram 需要
  image_path: "/config/www/night_alert_x.jpg"  # 可选

  phone_alert_name: "夜间门内开锁"             # 电话需要，缺失时自动兜底

  force_sound: true                            # 可选，默认 false
```

### 5.2 参数约束

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `channel` | string | 是 | `telegram` / `phone` / `all` |
| `message` | string | 条件必填 | `channel` 包含 `telegram` 时必填 |
| `title` | string | 否 | Telegram 标题，可为空 |
| `image_path` | string | 否 | 本地图片路径，存在时尝试发图 |
| `phone_alert_name` | string | 条件必填 | `channel` 包含 `phone` 时建议传；缺失时自动回退 |
| `force_sound` | bool | 否 | 默认 `false` |
| `request_id` | string | 否 | 用于日志关联 |
| `source` | string | 否 | 用于区分业务来源 |

### 5.3 默认回退规则

- `channel=telegram`
  - `message` 为空时，直接判定请求无效并记录 ERROR
- `channel=phone`
  - `phone_alert_name` 为空时，按 `title -> message 前 20 字 -> "HA通知"` 回退
- `channel=all`
  - 同时应用上面两套规则

## 6. 结果事件

`NotifyService` 在处理完成后，应发出结果事件：

```yaml
event_type: notify_service_result
event_data:
  request_id: "night_unlock_20260410_013733"
  source: "night_guard.unlock_alert"
  channel: "all"
  telegram:
    attempted: true
    success: true
    error: null
  phone:
    attempted: true
    success: false
    error: "phone channel disabled"
```

要求：

- 两个通道都要有 `attempted / success / error`
- 即使只发 Telegram，也要把 phone 标成 `attempted: false`
- 结果事件是一期联调的重要证据，不能省

## 7. Telegram 通道设计

### 7.1 当前实现基线

Telegram 底层当前已验证链路是：

- `telegram_bot.send_message`
- `telegram_bot.send_photo`

因此一期默认实现应优先直接走 `telegram_bot.*`，不要先切 `notify.*` 封装。

### 7.2 消息策略

Telegram 处理规则：

1. `image_path` 为空
   - 发送文字消息
2. `image_path` 不为空
   - 先尝试图片消息
   - 若失败，仍必须补发文字消息，明确说明图片未成功送达

### 7.3 静默规则

默认静默时段：

- `23:00 ~ 07:00`

处理规则：

- `force_sound = true`
  - `disable_notification = false`
- `force_sound = false`
  - 静默时段内：`disable_notification = true`
  - 非静默时段：`disable_notification = false`

注意：

- 夜间门内开锁告警默认 `force_sound = true`

## 8. 电话通道设计

### 8.1 一期定位

电话通道是一期架构内能力，但不是一期上线的唯一前置条件。

一期要求做到：

- `NotifyService` 有电话通道代码路径
- 配置缺失或 SDK 不可用时，有明确 WARNING / ERROR
- 电话失败不影响 Telegram

### 8.2 启用条件

只有在以下条件都满足时才真正发起电话：

- `channel` 包含 `phone`
- 电话功能开关开启
- VMS 凭据完整
- 依赖包已安装

否则：

- 不抛异常阻断整个请求
- 在结果事件中把 `phone.success = false`
- `error` 写明原因，例如 `phone channel disabled`

## 9. 容错原则

独立容错是硬要求：

- Telegram 失败，不影响电话
- 电话失败，不影响 Telegram
- `channel=all` 时任何单通道失败都不能让整个请求直接中断

业务上要达到的效果：

- 只要 Telegram 通道能用，夜间监护一期至少还能发出主通知
- 电话通道可以是增强，不允许变成单点故障

## 10. 配置设计

`apps.yaml` 建议模板：

```yaml
notify_service:
  module: notify_service
  class: NotifyService

  telegram_chat_id: "<telegram_chat_id>"
  telegram_parse_mode: "markdown"

  silent_start: "23:00"
  silent_end: "07:00"

  phone_enabled: false
  vms_access_key_id: "<access_key_id>"
  vms_access_key_secret: "<access_key_secret>"
  vms_called_number: "<called_number>"
  vms_tts_code: "<tts_code>"
  vms_called_show_number: "<show_number>"
```

说明：

- 敏感信息只放 `apps.yaml` 实际部署文件，不写入仓库
- `apps.yaml.example` 只保留占位符
- 电话默认 `phone_enabled: false`

## 11. 与 night_guard 的改造关系

本次不是“先做通知服务，以后再说”。

当前一期业务必须同步改造：

### 11.1 `night_guard_automations.yaml`

保持职责不变：

- 门锁事件触发
- 夜间判断
- 冷却判断
- 调用脚本

不应再直接负责任何 Telegram 发送细节。

### 11.2 `night_guard_scripts.yaml`

改造目标：

- 保留首条告警、抓拍、门状态观察、失败兜底
- 最终不再直接调用 Telegram
- 改为统一发出 `notify_service_request`

建议输出一类请求：

1. 夜间门内开锁告警
   - `channel: telegram`
   - `force_sound: true`
   - 有图时带 `image_path`

## 12. 实施顺序

Claude Code 应按这个顺序实现：

1. 新建 `ha/appdaemon/apps/notify_service.py`
2. 新建 `ha/appdaemon/apps.yaml.example`
3. 先打通 Telegram 通道
4. 再补电话通道的代码路径和配置占位
5. 改造 `ha/packages/night_guard_scripts.yaml`
6. 必要时微调 `ha/packages/night_guard_automations.yaml`
7. 回填 `docs/任务清单.md`
8. 回填 `docs/自测清单.md`

## 13. 验证清单

至少覆盖这些场景：

| 编号 | 场景 | 预期 |
|------|------|------|
| V1 | `channel=telegram` 文字消息 | 正常收到 Telegram |
| V2 | `channel=telegram` 图片消息 | 正常收到图片 |
| V3 | 图片发送失败 | 仍收到明确文字兜底 |
| V4 | 静默时段 + `force_sound=false` | Telegram 静默送达 |
| V5 | 静默时段 + `force_sound=true` | Telegram 强制响铃 |
| V6 | `channel=phone` 且电话未启用 | 不阻断，请求有结果事件和错误说明 |
| V7 | `channel=all` 且电话失败 | Telegram 仍成功 |
| V8 | night_guard 门锁告警 | 业务层成功发出 `notify_service_request` |
## 14. 成功标准

这份设计落地后，一期通知链路要满足：

- 业务层不再直接耦合 Telegram
- `NotifyService` 成为唯一通知入口
- 夜间门锁告警业务已接入
- Telegram 通道完成联调
- 电话通道至少完成代码路径、配置和失败隔离

## 15. 当前未决项

这些项允许在实现时确认，但不能阻塞 Telegram 主链路：

- 电话通道是否在一期内真实启用
- 电话 SDK 在 AppDaemon addon 内的安装细节
- Telegram 是否后续从 `telegram_bot.*` 切换到 `notify.*`

当前默认选择：

- Telegram 直接走 `telegram_bot.*`
- 电话默认关闭但代码路径存在
