# 手工验证指南

## 1. 默认联调摄像头

```text
名称：电梯厅
IP：192.168.77.117
验证码：ACNPUB
编码：H265
分辨率：2304x1296
```

## 2. 启动检查

执行：

```bash
python main.py --camera 电梯厅 --check
```

预期输出至少包含：

- 指定摄像头名称和 RTSP 地址
- 监护规则信息（杨孝治 / 时段 / 动作）
- 人脸库检查结果（杨孝治样本是否存在）
- 电话告警客户端初始化结果（当前阿里云 VMS SDK 尚未接入，应如实反映为骨架状态，不等同于可真实拨打）
- RTSP 连通性检查结果
- 最终通过/失败

注意：`face_recognition` 是必需依赖。如果未安装，`--check` 直接报错退出。

## 3. 核心监护规则验证

执行：

```bash
python main.py --camera 电梯厅
```

程序运行后终端会持续刷新状态栏（每秒 1 次），显示摄像头、规则、运行时长、RTSP 状态、识别结果、告警状态和证据占用。

在告警时段内，让杨孝治进入电梯厅画面。

预期：

- 触发电话告警（当前 SDK 未接入，预期 phone_result 为"拨打失败: 阿里云 VMS SDK 尚未接入"）
- 终端出现醒目的 `[告警]`
- 日志至少包含：摄像头名称、人物姓名、规则名称、证据路径、电话结果
- 证据目录下生成截图和短视频

## 4. 非目标人物不触发

执行：

```bash
python main.py --camera 电梯厅
```

在告警时段内，让其他家庭成员（老婆、儿子）进入电梯厅画面。

预期：

- 不触发"杨孝治夜间外出"电话告警
- 不创建新的目标告警证据

## 5. 电话告警失败但证据仍保留

触发一次目标事件后：

预期：

- 即使电话告警返回失败，截图和短视频仍然生成
- 终端日志中 phone_result 明确标注失败原因

## 6. 证据目录上限验证

1. 在 `config.yaml` 中临时设置很小的上限：
   ```yaml
   storage:
     max_evidence_size_gb: 0.001  # 约 1MB
   ```

2. 连续触发多次告警

3. 检查 `evidence/` 目录：
   ```bash
   du -sh evidence/
   ls -lt evidence/电梯厅/
   ```

4. 预期：最旧证据被自动清理，总占用不超过上限

## 7. 优雅退出

启动监控后按 `Ctrl+C`。

预期：

- 程序输出退出说明
- 视频流和相关资源被释放
- 已开始写入的证据尽量完成落盘

## 8. 自动化测试

```bash
# 全部测试
pytest -q

# 单模块测试
pytest tests/test_config.py -v
pytest tests/test_scheduler.py -v
pytest tests/test_evidence.py -v
pytest tests/test_face_registry.py -v
pytest tests/test_stream.py -v
pytest tests/test_alerts.py -v
pytest tests/test_phone_alert.py -v
pytest tests/test_vision.py -v
pytest tests/test_notifier.py -v
pytest tests/test_monitor.py -v
pytest tests/test_main_cli.py -v
```
