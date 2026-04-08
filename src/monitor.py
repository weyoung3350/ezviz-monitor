"""监控编排主流程。

规则驱动：摄像头 + 时段 + 目标人物命中 → 电话告警 + 证据保存 + 终端日志。
"""

import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from src.alerts import AlertCooldown
from src.config import AppConfig, CameraConfig, MonitorRule
from src.evidence import enforce_evidence_quota
from src.face_registry import FaceDirectoryScan
from src.notifier import print_alert
from src.phone_alert import PhoneAlertClient, PhoneAlertEvent, create_phone_alert_client
from src.scheduler import is_in_schedule
from src.status_panel import StatusData, StatusPanel
from src.stream import StreamState, should_retry_connect
from src.vision import PersonHitWindow

logger = logging.getLogger(__name__)


# --- 规则触发判断 ---

def should_trigger_rule(
    in_schedule: bool,
    target_hit: bool,
    cooldown_allows: bool,
) -> bool:
    """判断是否应触发一条监护规则。三个条件全部满足才触发。"""
    return in_schedule and target_hit and cooldown_allows


# --- 帧缓冲 ---

class FrameBuffer:
    """环形帧缓冲区，保存最近 N 秒的帧用于告警前视频。"""

    def __init__(self, max_seconds: float, fps: float = 10.0) -> None:
        self._max_frames = int(max_seconds * fps)
        self._buffer: deque[tuple[float, np.ndarray]] = deque(maxlen=self._max_frames)
        self._fps = fps

    def add(self, timestamp: float, frame: np.ndarray) -> None:
        self._buffer.append((timestamp, frame))

    def get_frames(self) -> list[tuple[float, np.ndarray]]:
        return list(self._buffer)

    @property
    def fps(self) -> float:
        return self._fps


# --- 证据保存 ---

def _save_snapshot(frame: np.ndarray, evidence_dir: Path, camera_name: str, now: datetime) -> Path:
    cam_dir = evidence_dir / camera_name
    cam_dir.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y-%m-%d_%H-%M-%S")
    path = cam_dir / f"{ts}_snapshot.jpg"
    cv2.imwrite(str(path), frame)
    logger.info("截图已保存: %s", path)
    return path


def _save_clip(
    pre_frames: list[tuple[float, np.ndarray]],
    post_frames: list[tuple[float, np.ndarray]],
    evidence_dir: Path,
    camera_name: str,
    now: datetime,
    fps: float,
) -> Path:
    cam_dir = evidence_dir / camera_name
    cam_dir.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y-%m-%d_%H-%M-%S")
    path = cam_dir / f"{ts}_clip.mp4"

    all_frames = pre_frames + post_frames
    if not all_frames:
        logger.warning("无帧可写入短视频")
        return path

    h, w = all_frames[0][1].shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (w, h))

    for _, frame in all_frames:
        writer.write(frame)
    writer.release()

    logger.info("短视频已保存: %s (%d 帧)", path, len(all_frames))
    return path


# --- 人形检测 ---

def _try_detect_person(frame: np.ndarray, person_detector) -> bool:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)
    rects, _ = person_detector.detectMultiScale(
        small, winStride=(8, 8), padding=(4, 4), scale=1.05,
    )
    return len(rects) > 0


# --- 人脸识别：返回人名或 None ---

def _try_identify_person(
    frame: np.ndarray,
    known_encodings: list[tuple[str, np.ndarray]],
) -> str | None:
    """尝试人脸识别，返回识别到的人名。未检测到人脸或无匹配时返回 None。"""
    import face_recognition

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    face_locations = face_recognition.face_locations(rgb, model="hog")

    if not face_locations:
        return None

    face_encodings = face_recognition.face_encodings(rgb, face_locations)

    for encoding in face_encodings:
        for name, known_enc in known_encodings:
            matches = face_recognition.compare_faces([known_enc], encoding, tolerance=0.6)
            if matches[0]:
                logger.debug("识别到人物: %s", name)
                return name

    logger.debug("检测到人脸但未匹配已知人物")
    return None


# --- 依赖检查 ---

def ensure_face_recognition_available() -> None:
    try:
        import face_recognition  # noqa: F401
    except ImportError:
        raise ImportError(
            "face_recognition 未安装。需求要求人物识别能力，此依赖为必需项。\n"
            "安装方式: pip install face_recognition\n"
            "如需编译 dlib: brew install cmake && pip install dlib face_recognition"
        )


# --- 人脸编码加载 ---

def _load_image_with_exif(path: Path) -> np.ndarray:
    """加载图片并处理 EXIF 旋转，确保像素方向与视觉方向一致。"""
    from PIL import Image, ImageOps
    pil_img = Image.open(path)
    pil_img = ImageOps.exif_transpose(pil_img)
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    return np.array(pil_img)


def _load_face_encodings(scan: FaceDirectoryScan, faces_dir: Path) -> list[tuple[str, np.ndarray]]:
    import face_recognition

    encodings = []
    for person_name in scan.people:
        person_dir = faces_dir / person_name
        for img_path in person_dir.iterdir():
            if img_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                try:
                    image = _load_image_with_exif(img_path)
                    encs = face_recognition.face_encodings(image)
                    if encs:
                        encodings.append((person_name, encs[0]))
                        logger.info("已加载人脸编码: %s / %s", person_name, img_path.name)
                    else:
                        logger.warning("未检测到人脸: %s", img_path)
                except Exception as e:
                    logger.warning("加载人脸失败: %s - %s", img_path, e)

    logger.info("共加载 %d 个人脸编码", len(encodings))
    return encodings


# --- 主监控循环 ---

def run_monitor(config: AppConfig, camera: CameraConfig, face_scan: FaceDirectoryScan) -> None:
    ensure_face_recognition_available()
    logger.info("启动监控: 摄像头=%s RTSP=%s", camera.name, camera.rtsp_url)

    if not camera.monitor_rules:
        logger.warning("摄像头 '%s' 没有配置 monitor_rules，将仅拉流不触发告警", camera.name)

    evidence_dir = Path(config.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    faces_dir = Path(config.faces_dir)

    # 初始化电话告警客户端
    phone_client = create_phone_alert_client(config.phone_alert)

    # 为每条规则创建独立的 PersonHitWindow 和冷却
    rule_windows: dict[str, PersonHitWindow] = {}
    rule_cooldowns: dict[str, AlertCooldown] = {}
    for rule in camera.monitor_rules:
        rule_windows[rule.rule_name] = PersonHitWindow(
            target_name=rule.person_name,
            frame_threshold=config.alert.person_frames_threshold,
            window_seconds=config.alert.person_window_seconds,
        )
        rule_cooldowns[rule.rule_name] = AlertCooldown(minutes=config.alert.cooldown_minutes)
        logger.info("已加载规则: %s (目标=%s)", rule.rule_name, rule.person_name)

    frame_buffer = FrameBuffer(max_seconds=config.video.pre_seconds)
    person_detector = cv2.HOGDescriptor()
    person_detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    known_encodings = _load_face_encodings(face_scan, faces_dir)

    max_evidence_bytes = config.storage.max_evidence_size_gb * 1024 * 1024 * 1024
    reconnect_interval = config.stream.reconnect_interval_seconds
    post_seconds = config.video.post_seconds

    stream_state = StreamState(last_error_at=0.0, reconnect_interval_seconds=reconnect_interval)
    cap: cv2.VideoCapture | None = None

    analysis_fps = 2.0
    last_analysis_time = 0.0
    buffer_fps = 10.0

    # 状态面板
    from src.evidence import get_directory_size
    rule_names = ", ".join(r.rule_name for r in camera.monitor_rules) or "无"
    initial_evidence_mb = get_directory_size(evidence_dir) / (1024 * 1024)
    status = StatusData(
        camera_name=camera.name,
        rule_name=rule_names,
        phone_status=phone_client.readiness_status(),
        evidence_size_mb=initial_evidence_mb,
    )
    panel = StatusPanel(status)
    panel.start()

    try:
        while True:
            # 连接 / 重连
            if cap is None or not cap.isOpened():
                status.rtsp_status = "重连中"
                now_mono = time.monotonic()
                if not should_retry_connect(stream_state, now_mono):
                    time.sleep(1)
                    continue

                logger.info("正在连接 RTSP: %s", camera.rtsp_url)
                cap = cv2.VideoCapture(camera.rtsp_url, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    logger.error("RTSP 连接失败: %s", camera.rtsp_url)
                    status.rtsp_status = "连接失败"
                    stream_state.last_error_at = time.monotonic()
                    cap = None
                    continue

                logger.info("RTSP 连接成功: %s", camera.rtsp_url)
                status.rtsp_status = "已连接"

            ret, frame = cap.read()
            if not ret:
                logger.warning("读取帧失败，准备重连")
                status.rtsp_status = "断流"
                stream_state.last_error_at = time.monotonic()
                cap.release()
                cap = None
                continue

            now_mono = time.monotonic()
            now_dt = datetime.now()

            frame_buffer.add(now_mono, frame)

            if now_mono - last_analysis_time < 1.0 / analysis_fps:
                continue
            last_analysis_time = now_mono

            # 第一阶段：人形检测
            has_person = _try_detect_person(frame, person_detector)
            status.frames_analyzed += 1
            status.last_analysis_time = now_dt.strftime("%H:%M:%S")

            if not has_person:
                status.last_identity = "无人"
                for w in rule_windows.values():
                    w.record(None, now_mono)
                continue

            # 第二阶段：人脸识别 → 人名
            identified_name = _try_identify_person(frame, known_encodings)

            if identified_name is None:
                status.last_identity = "有人·不确定"
                logger.debug("检测到人形但未识别身份，记录为不确定")
            else:
                status.last_identity = f"识别: {identified_name}"

            # 第三阶段：对每条规则做投票和触发判断
            for rule in camera.monitor_rules:
                window = rule_windows[rule.rule_name]
                cooldown = rule_cooldowns[rule.rule_name]
                cooldown_key = f"{camera.name}:{rule.rule_name}"

                # 先记录帧（不消费命中状态）
                window.record(identified_name, now_mono)

                if not window.is_hit():
                    continue

                # 命中了，再判断时段和冷却
                schedules = [{"start": s.start, "end": s.end} for s in rule.alert_schedules]
                in_schedule = is_in_schedule(schedules, now_dt)
                cooldown_allows = cooldown.should_trigger(cooldown_key, now_dt)

                if not should_trigger_rule(in_schedule, True, cooldown_allows):
                    if not in_schedule:
                        logger.debug("规则 '%s' 命中但非告警时段，命中状态保留", rule.rule_name)
                    elif not cooldown_allows:
                        logger.debug("规则 '%s' 命中但在冷却期内", rule.rule_name)
                        window.consume()  # 冷却期内消费，避免冷却结束后立刻重触发
                    continue

                # --- 触发告警：消费命中状态 ---
                window.consume()
                cooldown.record(cooldown_key, now_dt)
                logger.info("规则触发: %s (人物=%s 摄像头=%s)", rule.rule_name, rule.person_name, camera.name)

                # 1. 电话告警（配置阶段已强制要求 phone_call 在 actions 中）
                phone_event = PhoneAlertEvent(
                    person_name=rule.person_name,
                    camera_name=camera.name,
                    rule_name=rule.rule_name,
                    event_time=now_dt,
                )
                phone_result = phone_client.call(phone_event)
                if phone_result.success:
                    phone_result_text = "拨打成功"
                else:
                    phone_result_text = f"拨打失败: {phone_result.error}"
                    logger.error("电话告警失败: %s", phone_result.error)

                status.last_alert_time = now_dt.strftime("%H:%M:%S")
                status.phone_status = phone_result_text

                # 2. 证据保存（无论电话是否成功）
                snapshot_path = _save_snapshot(frame, evidence_dir, camera.name, now_dt)
                pre_frames = frame_buffer.get_frames()

                post_frames: list[tuple[float, np.ndarray]] = []
                post_start = time.monotonic()
                while time.monotonic() - post_start < post_seconds:
                    if cap is None or not cap.isOpened():
                        break
                    ret2, frame2 = cap.read()
                    if not ret2:
                        break
                    post_frames.append((time.monotonic(), frame2))

                clip_path = _save_clip(
                    pre_frames, post_frames, evidence_dir, camera.name, now_dt, fps=buffer_fps,
                )

                # 3. 终端日志
                if "terminal_log" in rule.actions:
                    print_alert(
                        camera_name=camera.name,
                        event_time=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        person_name=rule.person_name,
                        rule_name=rule.rule_name,
                        evidence_path=str(clip_path),
                        phone_result=phone_result_text,
                    )

                # 4. 证据配额清理
                enforce_evidence_quota(evidence_dir, max_evidence_bytes)

                # 更新证据占用
                from src.evidence import get_directory_size
                status.evidence_size_mb = get_directory_size(evidence_dir) / (1024 * 1024)

    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出...")
    finally:
        panel.stop()
        if cap is not None and cap.isOpened():
            cap.release()
        logger.info("监控已停止: %s", camera.name)


# --- 启动检查 ---

def run_check(config: AppConfig, camera: CameraConfig, face_scan: FaceDirectoryScan) -> bool:
    ensure_face_recognition_available()
    logger.info("=== 启动检查模式 ===")
    ok = True

    logger.info("摄像头: %s", camera.name)
    logger.info("RTSP: %s", camera.rtsp_url)

    # 规则检查
    if camera.monitor_rules:
        for rule in camera.monitor_rules:
            schedules = [{"start": s.start, "end": s.end} for s in rule.alert_schedules]
            logger.info("规则: %s (目标=%s 时段=%s 动作=%s)",
                        rule.rule_name, rule.person_name, schedules, rule.actions)
    else:
        logger.warning("摄像头 '%s' 没有配置 monitor_rules", camera.name)

    logger.info("证据目录: %s", config.evidence_dir)
    logger.info("最大证据容量: %d GB", config.storage.max_evidence_size_gb)

    # 人脸库检查
    logger.info("人脸库: %s", face_scan.people)
    if face_scan.warnings:
        for w in face_scan.warnings:
            logger.warning("人脸库警告: %s", w)

    # 电话告警检查
    pa = config.phone_alert
    logger.info("电话告警: provider=%s enabled=%s", pa.provider, pa.enabled)
    try:
        client = create_phone_alert_client(pa)
        logger.info("电话告警: %s", client.readiness_status())
    except Exception as e:
        logger.error("电话告警客户端初始化失败: %s", e)
        ok = False

    # RTSP 连接检查
    logger.info("正在测试 RTSP 连接...")
    cap = cv2.VideoCapture(camera.rtsp_url, cv2.CAP_FFMPEG)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            h, w = frame.shape[:2]
            logger.info("RTSP 连接成功，分辨率: %dx%d", w, h)
        else:
            logger.error("RTSP 已连接但读取帧失败")
            ok = False
        cap.release()
    else:
        logger.error("RTSP 连接失败: %s", camera.rtsp_url)
        ok = False

    # 证据目录
    evidence_dir = Path(config.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    logger.info("证据目录已就绪: %s", evidence_dir.resolve())

    if ok:
        logger.info("=== 启动检查通过 ===")
    else:
        logger.error("=== 启动检查失败 ===")

    return ok
