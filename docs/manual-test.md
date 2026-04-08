# 手工验证指南

## 1. RTSP 连接验证

使用 ffplay 验证摄像头 RTSP 连接是否可用：

```bash
# 客厅摄像头（默认开发测试摄像头）
ffplay "rtsp://admin:HFUIJD@192.168.77.118:554/h264/ch1/main/av_stream"

# 四楼阳台
ffplay "rtsp://admin:IJVZKK@192.168.77.43:554/h264/ch1/main/av_stream"

# 三楼阳台
ffplay "rtsp://admin:GDOOUB@192.168.77.115:554/h264/ch1/main/av_stream"

# 电梯厅
ffplay "rtsp://admin:ACNPUB@192.168.77.117:554/h264/ch1/main/av_stream"

# 露台
ffplay "rtsp://admin:GSJAOM@192.168.77.157:554/h264/ch1/main/av_stream"
```

如果 ffplay 无法播放，检查：
- Mac 和摄像头是否在同一局域网
- 摄像头 IP 和验证码是否正确
- 摄像头是否已开启 RTSP 功能

## 2. 启动检查验证

```bash
# 确保 config.yaml 已创建
cp config.example.yaml config.yaml

# 确保至少有一个家人照片目录
mkdir -p known_faces/测试人员
# 放入至少一张照片到 known_faces/测试人员/ 目录

# 运行启动检查
python main.py --camera 客厅 --check
```

预期输出：
- 摄像头配置信息
- face_recognition 可用性检查（缺失时程序直接报错退出，不会进入监控）
- RTSP 连接成功/失败
- 人脸库加载结果（包含每人加载了多少个人脸编码）
- 证据目录状态
- 最终检查结果（通过/失败）

注意：`face_recognition` 是必需依赖。如果未安装，`--check` 和正常监控都会在启动时报错退出。

## 3. 文字告警格式验证

启动监控后，在摄像头前走动触发陌生人检测：

```bash
python main.py --camera 客厅
```

预期：
- 终端输出醒目的 `[告警]` 文本
- 包含摄像头名称、时间戳、证据路径
- 与普通日志有明显视觉区分（使用分隔线包围）

告警输出示例：
```
============================================================
  [告警] 检测到陌生人!
  摄像头: 客厅
  时间:   2026-04-08 23:15:02
  证据:   ./evidence/客厅/2026-04-08_23-15-02_clip.mp4
============================================================
```

## 4. 证据目录上限验证

1. 在 `config.yaml` 中临时设置一个很小的上限：
   ```yaml
   storage:
     max_evidence_size_gb: 0.001  # 约 1MB
   ```

2. 启动监控，多次触发告警

3. 检查 `evidence/` 目录：
   ```bash
   du -sh evidence/
   ls -lt evidence/客厅/
   ```

4. 预期：最旧的证据文件被自动删除，目录总占用不超过设定上限

5. 验证完成后恢复正常上限值

## 5. Ctrl+C 退出验证

1. 启动监控：`python main.py --camera 客厅`
2. 按 `Ctrl+C`
3. 预期：
   - 日志输出退出信息
   - 程序正常结束，无异常堆栈
   - 未完成的文件写入尽量完成

## 6. 自动化测试

```bash
# 运行全部测试
pytest -q

# 运行单个模块测试
pytest tests/test_config.py -v
pytest tests/test_scheduler.py -v
pytest tests/test_evidence.py -v
pytest tests/test_face_registry.py -v
pytest tests/test_stream.py -v
pytest tests/test_alerts.py -v
pytest tests/test_notifier.py -v
pytest tests/test_vision.py -v
pytest tests/test_monitor.py -v
pytest tests/test_main_cli.py -v
```
