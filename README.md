# 萤石摄像头智能监控告警系统

在 Mac 本机通过 RTSP 协议接入萤石摄像头，识别家人与陌生人，在告警时段内检测到陌生人时输出命令行文字告警，并保存截图和短视频证据。

## 环境准备

### Python 依赖

```bash
# 需要 Python 3.11+
pip install -r requirements.txt
```

### FFmpeg（可选，建议安装以获得更好的 RTSP 兼容性）

```bash
brew install ffmpeg
```

### face_recognition（可选，用于人脸识别）

```bash
pip install face_recognition
```

如果不安装 `face_recognition`，系统仍可运行，但所有检测到的人形都会被视为陌生人。

## 快速开始

### 1. 准备配置文件

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入实际的 RTSP 地址和告警时段
```

### 2. 准备家人照片

```bash
mkdir -p known_faces/爸爸 known_faces/妈妈
# 将每人 3-5 张照片放入对应目录
```

### 3. 启动检查

```bash
python main.py --camera 客厅 --check
```

启动检查模式会验证：
- 配置文件是否正确
- 指定摄像头是否存在
- 人脸库是否有效
- RTSP 连接是否可用

### 4. 启动监控

```bash
python main.py --camera 客厅
```

按 `Ctrl+C` 优雅退出。

## 命令行参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--camera` | 是 | 要监控的摄像头名称 |
| `--check` | 否 | 仅做启动检查 |
| `--config` | 否 | 配置文件路径（默认 `config.yaml`） |

## 运行测试

```bash
pytest -q
```

## 项目结构

```
main.py                 # 程序入口
config.yaml             # 配置文件（需自行创建）
config.example.yaml     # 配置示例
known_faces/            # 家人照片目录
evidence/               # 告警证据目录
src/
  config.py             # 配置加载与校验
  scheduler.py          # 告警时段判断
  evidence.py           # 证据磁盘配额清理
  face_registry.py      # 家人人脸目录扫描
  stream.py             # 视频流重连控制
  alerts.py             # 告警冷却策略
  notifier.py           # 命令行文字告警输出
  vision.py             # 陌生人事件聚合
  monitor.py            # 监控编排主流程
tests/                  # 自动化测试
```
