# 萤石摄像头老人夜间外出监护告警系统

在 Mac 本机通过 RTSP 协议接入萤石摄像头，重点监护老人"杨孝治"是否在指定时段出现在"电梯厅"画面中；命中后触发电话告警并保存截图和短视频证据。

> **当前状态**：阿里云 VMS 电话告警 SDK 尚未接入，`AliyunVmsClient` 为骨架实现，调用时返回失败。真实电话拨打能力需后续接入 SDK 后才可用。证据保存和终端日志不受影响。

## 当前目标

当前一期目标不是通用陌生人告警，而是：

- 单机运行
- 单摄像头监控
- RTSP 拉流
- 重点人物识别：`杨孝治`
- 重点摄像头：`电梯厅`
- 重点时段：夜间与清晨，以配置为准
- 电话告警（SDK 待接入）
- 本地日志输出
- 截图与短视频证据留存
- 证据目录磁盘上限控制

## 依赖

`face_recognition` 和 `Pillow` 是必需依赖。如果安装失败，先安装编译工具：

```bash
xcode-select --install
brew install cmake
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

如果 `face_recognition` 与 `setuptools` 版本不兼容，优先按项目文档中的风险说明处理。

## 配置

核心配置位于 `config.yaml`，当前应至少包含：

- `电梯厅` 摄像头 RTSP 地址
- `杨孝治夜间外出监护` 规则
- 电话告警配置
- 证据目录上限

详细结构见 [docs/需求文档.md](docs/需求文档.md)。

## 启动检查

```bash
python main.py --camera 电梯厅 --check
```

预期至少看到：

- 配置加载成功
- 指定摄像头存在
- RTSP 连通性检查结果
- 杨孝治样本目录检查结果
- 电话告警状态（当前会输出"SDK 尚未接入，不能真实拨打"）

## 启动监控

```bash
python main.py --camera 电梯厅
```

## 目录说明

```text
main.py
config.yaml
known_faces/
  杨孝治/
  杨为意/
  谈凤/
  杨一帆/
evidence/
src/
  config.py
  scheduler.py
  face_registry.py
  stream.py
  evidence.py
  alerts.py
  notifier.py
  vision.py
  phone_alert.py
  monitor.py
tests/
docs/
```

## 文档

- [需求文档](docs/需求文档.md)
- [实现计划](docs/plans/2026-04-08-萤石摄像头告警系统实现计划.md)
- [自测清单](docs/自测清单.md)
- [任务清单](docs/任务清单.md)
- [验收测试方案](docs/验收测试方案.md)
