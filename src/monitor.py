import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from src.alerts import AlertCooldown
from src.config import AppConfig, CameraConfig
from src.evidence import enforce_evidence_quota
from src.face_registry import FaceDirectoryScan
from src.notifier import print_alert
from src.scheduler import is_in_schedule
from src.stream import StreamState, should_retry_connect
from src.vision import StrangerDecisionWindow

logger = logging.getLogger(__name__)


def should_alert_for_detection(
    in_schedule: bool,
    stranger_event: bool,
    cooldown_allows: bool,
) -> bool:
    return in_schedule and stranger_event and cooldown_allows


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


def _try_detect_person(frame: np.ndarray, person_detector) -> bool:
    """检测画面中是否有人。使用 HOG 人形检测器。"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 缩小图片以提高检测速度
    small = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)
    rects, _ = person_detector.detectMultiScale(
        small,
        winStride=(8, 8),
        padding=(4, 4),
        scale=1.05,
    )
    return len(rects) > 0


def _try_recognize_family(
    frame: np.ndarray,
    known_encodings: list[tuple[str, np.ndarray]],
) -> bool | None:
    """尝试人脸识别。返回 True=家人, False=陌生人, None=未检测到人脸。"""
    try:
        import face_recognition
    except ImportError:
        logger.debug("face_recognition 未安装，跳过人脸识别")
        return None

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    # 缩小以提高速度
    small = cv2.resize(rgb, (0, 0), fx=0.5, fy=0.5)
    face_locations = face_recognition.face_locations(small, model="hog")

    if not face_locations:
        return None

    face_encodings = face_recognition.face_encodings(small, face_locations)

    for encoding in face_encodings:
        for name, known_enc in known_encodings:
            matches = face_recognition.compare_faces([known_enc], encoding, tolerance=0.6)
            if matches[0]:
                logger.debug("识别到家人: %s", name)
                return True

    return False


def _load_face_encodings(scan: FaceDirectoryScan, faces_dir: Path) -> list[tuple[str, np.ndarray]]:
    """加载家人照片的人脸编码。"""
    try:
        import face_recognition
    except ImportError:
        logger.warning("face_recognition 未安装，将无法进行人脸识别（所有有人画面均视为陌生人）")
        return []

    encodings = []
    for person_name in scan.people:
        person_dir = faces_dir / person_name
        for img_path in person_dir.iterdir():
            if img_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                try:
                    image = face_recognition.load_image_file(str(img_path))
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


def run_monitor(config: AppConfig, camera: CameraConfig, face_scan: FaceDirectoryScan) -> None:
    """主监控循环。"""
    logger.info("启动监控: 摄像头=%s RTSP=%s", camera.name, camera.rtsp_url)

    evidence_dir = Path(config.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    faces_dir = Path(config.faces_dir)

    # 初始化组件
    cooldown = AlertCooldown(minutes=config.alert.cooldown_minutes)
    decision_window = StrangerDecisionWindow(
        frame_threshold=config.alert.stranger_frames_threshold,
        window_seconds=config.alert.stranger_window_seconds,
    )
    frame_buffer = FrameBuffer(max_seconds=config.video.pre_seconds)
    person_detector = cv2.HOGDescriptor()
    person_detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    known_encodings = _load_face_encodings(face_scan, faces_dir)

    max_evidence_bytes = config.storage.max_evidence_size_gb * 1024 * 1024 * 1024
    reconnect_interval = config.stream.reconnect_interval_seconds
    post_seconds = config.video.post_seconds

    schedules = [{"start": s.start, "end": s.end} for s in camera.alert_schedules]

    stream_state = StreamState(last_error_at=0.0, reconnect_interval_seconds=reconnect_interval)
    cap: cv2.VideoCapture | None = None

    # 采样间隔控制：不需要每帧都分析
    analysis_fps = 2.0  # 每秒分析 2 帧
    last_analysis_time = 0.0
    buffer_fps = 10.0  # 缓冲区以 10fps 录帧

    try:
        while True:
            # 连接 / 重连
            if cap is None or not cap.isOpened():
                now_mono = time.monotonic()
                if not should_retry_connect(stream_state, now_mono):
                    time.sleep(1)
                    continue

                logger.info("正在连接 RTSP: %s", camera.rtsp_url)
                cap = cv2.VideoCapture(camera.rtsp_url, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    logger.error("RTSP 连接失败: %s", camera.rtsp_url)
                    stream_state.last_error_at = time.monotonic()
                    cap = None
                    continue

                logger.info("RTSP 连接成功: %s", camera.rtsp_url)

            ret, frame = cap.read()
            if not ret:
                logger.warning("读取帧失败，准备重连")
                stream_state.last_error_at = time.monotonic()
                cap.release()
                cap = None
                continue

            now_mono = time.monotonic()
            now_dt = datetime.now()

            # 缓冲帧（用于告警前视频）
            frame_buffer.add(now_mono, frame)

            # 采样控制
            if now_mono - last_analysis_time < 1.0 / analysis_fps:
                continue
            last_analysis_time = now_mono

            # 第一阶段：人形检测
            has_person = _try_detect_person(frame, person_detector)
            if not has_person:
                decision_window.record(False, now_mono)
                continue

            # 第二阶段：人脸识别
            face_result = _try_recognize_family(frame, known_encodings)

            if face_result is True:
                # 家人，不告警
                decision_window.record(False, now_mono)
                logger.debug("检测到家人，不告警")
                continue

            # 无人脸或陌生人脸 → 按陌生人处理
            is_stranger_event = decision_window.record(True, now_mono)

            if not is_stranger_event:
                continue

            # 检查时段和冷却
            in_schedule = is_in_schedule(schedules, now_dt)
            cooldown_allows = cooldown.should_trigger(camera.name, now_dt)

            if not should_alert_for_detection(in_schedule, is_stranger_event, cooldown_allows):
                if not in_schedule:
                    logger.debug("非告警时段，不输出告警")
                elif not cooldown_allows:
                    logger.debug("告警冷却中，不输出新告警")
                continue

            # 触发告警
            cooldown.record(camera.name, now_dt)

            # 保存截图
            snapshot_path = _save_snapshot(frame, evidence_dir, camera.name, now_dt)

            # 收集告警后帧
            post_frames: list[tuple[float, np.ndarray]] = []
            post_start = time.monotonic()
            while time.monotonic() - post_start < post_seconds:
                if cap is None or not cap.isOpened():
                    break
                ret2, frame2 = cap.read()
                if not ret2:
                    break
                post_frames.append((time.monotonic(), frame2))
                frame_buffer.add(time.monotonic(), frame2)

            # 保存短视频
            clip_path = _save_clip(
                frame_buffer.get_frames(),
                post_frames,
                evidence_dir,
                camera.name,
                now_dt,
                fps=buffer_fps,
            )

            # 输出文字告警
            print_alert(
                camera_name=camera.name,
                event_time=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                evidence_path=str(clip_path),
            )

            # 证据配额清理
            enforce_evidence_quota(evidence_dir, max_evidence_bytes)

    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出...")
    finally:
        if cap is not None and cap.isOpened():
            cap.release()
        logger.info("监控已停止: %s", camera.name)


def run_check(config: AppConfig, camera: CameraConfig, face_scan: FaceDirectoryScan) -> bool:
    """启动检查模式：校验配置和连接，不进入持续监控循环。"""
    logger.info("=== 启动检查模式 ===")
    ok = True

    # 检查配置
    logger.info("摄像头: %s", camera.name)
    logger.info("RTSP: %s", camera.rtsp_url)
    logger.info("告警时段: %s", [{"start": s.start, "end": s.end} for s in camera.alert_schedules])
    logger.info("证据目录: %s", config.evidence_dir)
    logger.info("最大证据容量: %d GB", config.storage.max_evidence_size_gb)

    # 检查人脸库
    logger.info("家人人脸库: %s", face_scan.people)
    if face_scan.warnings:
        for w in face_scan.warnings:
            logger.warning("人脸库警告: %s", w)

    # 检查 RTSP 连接
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

    # 检查证据目录
    evidence_dir = Path(config.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    logger.info("证据目录已就绪: %s", evidence_dir.resolve())

    if ok:
        logger.info("=== 启动检查通过 ===")
    else:
        logger.error("=== 启动检查失败 ===")

    return ok
