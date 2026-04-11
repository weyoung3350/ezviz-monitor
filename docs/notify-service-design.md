# HA 统一通知服务 NotifyService — 实施设计稿

> 当前版本是一期正式实施文档，不是预研稿。
> 当前主线：`HA 自动化 / script + AppDaemon NotifyService`。
> 2026-04-11 起通知主通道从 Telegram 切换为钉钉自定义机器人 + iOS Companion App 推送。

## 1. 目的

`NotifyService` 是一期的统一通知层，负责把业务侧发出的通知请求转换成实际的钉钉 / iOS 推送 / 电话下发。

当前要解决的不是"做一个通用通知平台"，而是先把夜间监护一期的通知链路收口：

- 业务 YAML 不再直接调用任何具体通知服务（`notify.mobile_app_*` / 钉钉 webhook / VMS SDK）
- 夜间门内开锁告警统一走 `notify_service_request`
- 静默规则、强制响铃、图片发送、通道容错都放到同一层处理

## 2. 当前范围

当前一期内，`NotifyService` 只服务 1 条业务链路：

1. 夜间门内开锁告警

当前一期内，`NotifyService` 只要求做到：

- 监听 `notify_service_request`
- 支持钉钉通道（自定义机器人 + 加签）
- 支持 iOS Companion App 推送通道
- 支持电话通道
- 支持 `channel` 字段为字符串或字符串数组
- 支持 `force_sound`（iOS critical alert；钉钉通道忽略）
- 支持图片路径（钉钉 markdown 内嵌 URL；iOS 推送 attachment）
- 支持三通道独立容错
- 记录明确日志

当前一期明确不做：

- 多接收人（钉钉群机器人仅此一个群）
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
  - 负责统一下发钉钉 / iOS 推送 / 电话
  - 负责静默规则和 `force_sound`
  - 负责通道错误隔离

禁止的实现方式：

- 业务 YAML 再次直接调用 `notify.mobile_app_*` / 任何具体通知服务
- 在多个业务脚本里重复写静默规则
- 在业务层自己判断"电话失败后怎么办"

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
  request_id: "night_unlock_20260411_013733"   # 可选，建议业务侧传
  source: "night_guard.unlock_alert"           # 可选，建议业务侧传

  # 单通道字符串 / 多通道数组 / "all" 三通道并发
  channel: "dingtalk"
  # 或：
  # channel: ["dingtalk", "ios_push"]
  # 或：
  # channel: "all"

  title: "夜间门内开锁告警"                    # 可选
  message: "时间：01:37\n门状态：已确认开门"    # 钉钉和 iOS 推送需要
  image_path: "/config/www/night_alert_x.jpg"  # 可选

  phone_alert_name: "夜间门内开锁"             # 电话需要，缺失时自动兜底

  force_sound: true                            # 可选，默认 false
```

### 5.2 参数约束

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `channel` | string / list | 是 | `dingtalk` / `ios_push` / `phone` / `all`，或上述值的数组 |
| `message` | string | 条件必填 | `channel` 包含 `dingtalk` 或 `ios_push` 时必填 |
| `title` | string | 否 | 消息标题，可为空 |
| `image_path` | string | 否 | 本地图片路径（如 `/config/www/xxx.jpg`），存在时：钉钉嵌入 markdown 图片 URL，iOS 推送作为附件 |
| `phone_alert_name` | string | 条件必填 | `channel` 包含 `phone` 时建议传；缺失时自动回退 |
| `force_sound` | bool | 否 | 默认 `false`。true 时 iOS 推送走 critical alert；钉钉通道忽略此字段 |
| `request_id` | string | 否 | 用于日志关联 |
| `source` | string | 否 | 用于区分业务来源 |

### 5.3 通道语义

- 单字符串 `"dingtalk"`：只发钉钉
- 数组 `["dingtalk", "ios_push"]`：并发发钉钉和 iOS 推送，不打电话
- `"all"`：等价于同时发钉钉 + iOS 推送 + 电话
- 未列出的通道一律视为关闭，不会被触发
- 合法值集合：`{"dingtalk", "ios_push", "phone", "all"}`
- 未知通道名（例如 `"ding"`、`"sms"`）会被过滤并在日志里记录 ERROR
- 空数组 `[]` / 全部未知值 / 非字符串非数组的输入 → 直接放弃分发，结果事件中三通道全部标记 `error=解析错误`

### 5.4 默认回退规则

- `dingtalk` / `ios_push`
  - `message` 为空时，该通道记 `attempted` 但 `error="message 为空"`
- `phone`
  - `phone_alert_name` 为空时，按 `title -> message 前 20 字 -> "HA通知"` 回退
- 多通道组合时，各通道独立应用自己的回退规则

## 6. 结果事件

`NotifyService` 在处理完成后，应发出结果事件：

```yaml
event_type: notify_service_result
event_data:
  request_id: "night_unlock_20260411_013733"
  source: "night_guard.unlock_alert"
  channels: ["dingtalk", "ios_push", "phone"]
  dingtalk:
    attempted: true
    success: true
    error: null
  ios_push:
    attempted: true
    success: true
    error: null
  phone:
    attempted: true
    success: false
    error: "phone channel disabled"
```

要求：

- 三个通道都要有 `attempted / success / error`
- 未被激活的通道标记为 `attempted: false`
- 结果事件是一期联调的重要证据，不能省
- `channels` 字段记录本次请求实际解析出的通道列表

## 7. 钉钉通道设计

### 7.1 当前实现基线

钉钉底层走自定义机器人的加签 webhook：

- webhook URL 形如 `https://oapi.dingtalk.com/robot/send?access_token=<token>`
- 配合 secret 做 HMAC-SHA256 加签，签名拼接在 URL 的 `timestamp` 和 `sign` 参数上
- 发送 POST JSON 请求，使用 `text` 或 `markdown` 消息类型

实现细节：

- 用 Python 标准库 `hmac` + `hashlib` + `base64` 做签名
- 用 `urllib.request` 发送 POST，零额外依赖
- 超时 10 秒

### 7.2 消息策略

钉钉处理规则：

1. `image_path` 为空
   - 走 `text` 消息类型，正文 `【{title}】\n{message}`（无 title 时只有 message）
2. `image_path` 以 `/config/www/` 开头
   - 走 `markdown` 消息类型
   - 标题使用 `title`（缺省回退 "HA 告警"）
   - 正文 `# {title}\n\n{message}\n\n![snapshot]({image_url})`
   - `image_url` 由 `image_path.replace("/config/www/", dingtalk_image_base_url)` 得到
   - `dingtalk_image_base_url` 默认 `http://192.168.77.253:8123/local/`（内网）
3. `image_path` 不以 `/config/www/` 开头
   - 记录 WARNING 日志
   - 降级为纯文字消息（同 1），避免拼出无效 URL 却记成功

### 7.3 内网图片的风险

当前 `dingtalk_image_base_url` 为内网地址，钉钉服务器无法拉取。已知的后果：

- 钉钉 markdown 消息本身会送达，但其中的 `![snapshot](...)` 图片在钉钉客户端显示为"加载失败"
- 用户仍能看到标题和正文文字

如果这个表现不可接受，后续方案：

- 方案 A：改为 HA 外网地址（Nabu Casa / 内网穿透）
- 方案 B：图片先上传至图床 / OSS，`_send_dingtalk` 接收可用的外网 URL

### 7.4 静默规则

钉钉 webhook 没有"静默/响铃"控制，静音由接收方手机端钉钉 App 自行管理。

处理规则：

- `force_sound` 对钉钉通道**不生效**，日志需记录以便追溯
- 真正需要绕过静音的"主告警响铃"由 `phone` 通道负责

## 8. iOS Companion App 推送通道设计

### 8.1 基线

底层走 HA Companion iOS App 的 `notify.mobile_app_*` 服务，由配置项 `ios_push_service` 指定具体的 notify 服务名。

### 8.2 消息策略

- `image_path` 为空 → 纯文字推送
- `image_path` 不为空 → 文字推送 + `data.push.attachment.url`，url 由 `/config/www/xxx` 替换为 `/local/xxx`，HA Companion App 会从 HA 内网地址拉取

### 8.3 静默与强响铃规则

- `force_sound = true`
  - `data.push.sound = {name: default, critical: 1, volume: 1.0}`
  - 需要用户端在 iOS 设置里授予"重要提醒"（Critical Alerts）权限
- `force_sound = false` + 静默时段
  - `data.push.sound = "none"`
- `force_sound = false` + 非静默时段
  - 不设置 sound，使用系统默认

## 9. 电话通道设计

### 9.1 一期定位

电话通道是"主告警响铃"的唯一兜底手段，一期必须保持可用。

一期要求做到：

- `NotifyService` 有电话通道代码路径
- 配置缺失或 SDK 不可用时，有明确 WARNING / ERROR
- 电话失败不影响钉钉和 iOS 推送

### 9.2 启用条件

只有在以下条件都满足时才真正发起电话：

- `channel` 包含 `phone`（或 `"all"`）
- `phone_enabled: true`
- VMS 凭据完整
- 依赖包 `alibabacloud_dyvmsapi20170525` 已安装

否则：

- 不抛异常阻断整个请求
- 在结果事件中把 `phone.success = false`
- `error` 写明原因，例如 `phone channel disabled` / `VMS 客户端未初始化（SDK 未安装或凭据错误）`

## 10. 容错原则

独立容错是硬要求：

- 钉钉、iOS 推送、电话任一失败，不影响其他两路
- `channel=all` 时任何单通道失败都不能让整个请求直接中断
- 单通道内部的异常必须在该通道的 `_send_*` 方法里 `try/except` 捕获

业务上要达到的效果：

- 只要钉钉或 iOS 推送任一可用，夜间监护一期至少还能发出非响铃提醒
- 只要电话通道可用，重要告警一定会响铃
- 任何一路故障都不会变成单点阻断

## 10a. 异步调度原则

所有第三方调用（HTTP、SDK、`self.call_service`）必须不阻塞 AppDaemon 事件循环：

- `on_notify_request` 固定为 `async def`，由 AppDaemon 在事件循环上 `await` 执行
- 每个激活的通道用 **AppDaemon 原生** `self.run_in_executor(partial(self._send_*, ...))` 派发到 AppDaemon 内部线程池
- 多通道用 `asyncio.gather(*tasks, return_exceptions=True)` 并发等待
- 各 `_send_dingtalk` / `_send_ios_push` / `_send_phone` 保持 sync 实现（内部仍可直接用 urllib / self.call_service / VMS SDK），借助线程池隔离阻塞行为
- `asyncio.gather(return_exceptions=True)` 保证单通道未捕获异常不会取消其他通道
- 线程异常统一包装为 `{"attempted": True, "success": False, "error": "线程异常: ..."}`，结果事件仍然能发布

**为什么用 AppDaemon 原生 `run_in_executor` 而不是 `asyncio.to_thread`**：

- `asyncio.to_thread` 走的是 Python 默认线程池，任务生命周期与 AppDaemon app reload/terminate 解耦
- `self.run_in_executor` 走 AppDaemon 管理的内部线程池，reload 时会被正确清理，符合 AppDaemon 的线程模型
- 两者在调用方式上几乎等价（都返回 awaitable），但后者在框架生命周期里更干净

效果：

- 单请求总耗时 ≈ max(各通道)，不再是 sum
- 事件循环不会因单次 iOS 推送 ~10 秒而被阻塞
- AppDaemon 不再报 `Excessive time spent in callback` 警告

**实测对比（2026-04-11）**：

| 场景 | 同步版本 | 异步版本 |
|------|---------|---------|
| `channel: "dingtalk"` | 约 160 ms | 约 170 ms |
| `channel: ["dingtalk", "ios_push"]` | 约 10 s（触发警告） | 约 1.4 s |

### 新增通道的扩展约束

任何未来新增通道必须遵守：

- `_send_*` 内部可以是同步阻塞的第三方调用
- 必须在 `on_notify_request` 的派发表里用 `asyncio.to_thread` 包装
- 禁止在 `on_notify_request` 主体里直接调用可能阻塞的函数
- 任何长耗时操作都应该归入通道内部，统一走线程池

## 11. 配置设计

`apps.yaml` 建议模板：

```yaml
notify_service:
  module: notify_service
  class: NotifyService

  # 钉钉自定义机器人（加签）
  dingtalk_webhook: "<dingtalk_webhook_url>"
  dingtalk_secret: "<dingtalk_secret>"
  dingtalk_image_base_url: "http://192.168.77.253:8123/local/"

  # iOS Companion App 推送
  ios_push_service: "notify.mobile_app_dna_iphone15p"

  # 静默时段
  silent_start: "23:00"
  silent_end: "07:00"

  # 电话通道
  phone_enabled: false
  vms_access_key_id: "<access_key_id>"
  vms_access_key_secret: "<access_key_secret>"
  vms_called_number: "<called_number>"
  vms_tts_code: "<tts_code>"
  vms_called_show_number: "<show_number>"
```

说明：

- 敏感信息（webhook、secret、AccessKey、手机号）只放 `apps.yaml` 实际部署文件，**不写入仓库**
- `apps.yaml.example` 只保留占位符
- 电话默认 `phone_enabled: false`，一期实际部署时根据联调结果决定是否开启

## 12. 与 night_guard 的改造关系

本次不是"先做通知服务，以后再说"。

当前一期业务必须同步改造：

### 12.1 `night_guard_automations.yaml`

保持职责不变：

- 门锁事件触发
- 夜间判断
- 冷却判断
- 调用脚本

不应再直接负责任何具体通知服务的调用。

### 12.2 `night_guard_scripts.yaml`

改造目标：

- 保留首条告警、抓拍、门状态观察、失败兜底
- 不再直接调用 `notify.mobile_app_*` 或钉钉 webhook
- 改为统一发出 `notify_service_request`

当前脚本输出三类请求：

1. **立即主告警**（首条文字）
   - `channel: "all"`
   - `force_sound: true`
   - 钉钉 + iOS 推送 + 电话三通道并发
2. **快照图片**
   - `channel: ["dingtalk", "ios_push"]`
   - `force_sound: false`
   - 带 `image_path`，只发消息不再打电话
3. **详情兜底**
   - `channel: ["dingtalk", "ios_push"]`
   - `force_sound: false`
   - 无图纯文字，兜底说明抓拍结果

## 13. 实施顺序

1. 新建 / 改写 `ha/appdaemon/apps/notify_service.py`
2. 新建 / 改写 `ha/appdaemon/apps.yaml.example`
3. 打通钉钉通道
4. 打通 iOS 推送通道
5. 补电话通道代码路径和配置占位
6. 改造 `ha/packages/night_guard_scripts.yaml`
7. 必要时微调 `ha/packages/night_guard_automations.yaml`
8. 回填 `docs/任务清单.md`
9. 回填 `docs/自测清单.md`

## 14. 验证清单

至少覆盖这些场景：

| 编号 | 场景 | 预期 |
|------|------|------|
| V1 | `channel=dingtalk` 文字消息 | 钉钉群收到 text 消息 |
| V2 | `channel=dingtalk` + image_path | 钉钉群收到 markdown 消息（图片可能加载失败但文字到达） |
| V3 | `channel=ios_push` 文字消息 | iPhone 收到 HA Companion 推送 |
| V4 | `channel=ios_push` + force_sound=true | iPhone critical alert 响铃 |
| V5 | `channel=["dingtalk","ios_push"]` | 钉钉和 iPhone 都收到，phone 不触发 |
| V6 | `channel=all` 正常路径 | 钉钉、iPhone、电话均到达 |
| V7 | `channel=phone` 且 phone_enabled=false | 不阻断，结果事件有 `phone.error` |
| V8 | `channel=all` 且电话失败 | 钉钉和 iOS 推送仍成功 |
| V9 | night_guard 门锁告警端到端 | 业务层成功发出 3 条 `notify_service_request`（主告警 all / 快照 dingtalk+ios / 详情 dingtalk+ios） |

## 15. 成功标准

这份设计落地后，一期通知链路要满足：

- 业务层不再直接耦合任何具体通知服务
- `NotifyService` 成为唯一通知入口
- 夜间门锁告警业务已接入
- 钉钉通道完成联调
- iOS 推送通道完成联调
- 电话通道完成代码路径、配置和失败隔离

## 16. 当前未决项

这些项允许在实现时确认，但不能阻塞主链路：

- 电话通道是否在一期内真实启用
- 电话 SDK 在 AppDaemon addon 内的安装细节
- 钉钉 markdown 图片能否用内网 URL 实际加载（已知大概率失败）
- iOS Critical Alert 在 `force_sound=true` 时是否稳定穿透勿扰
