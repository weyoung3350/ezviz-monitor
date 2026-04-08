import argparse
import logging
import sys
from pathlib import Path

from src.config import ConfigError, load_config
from src.face_registry import FaceRegistryError, scan_face_directories
from src.monitor import run_check, run_monitor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="萤石摄像头智能监控告警系统",
    )
    parser.add_argument(
        "--camera",
        required=True,
        help="要监控的摄像头名称（需与 config.yaml 中一致）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅做启动检查，不进入持续监控",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径（默认 config.yaml）",
    )
    return parser


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def main() -> int:
    _setup_logging()
    logger = logging.getLogger(__name__)

    parser = build_parser()
    args = parser.parse_args()

    # 加载配置
    config_path = Path(args.config)
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        logger.error("配置文件不存在: %s", config_path)
        return 1
    except ConfigError as e:
        logger.error("配置校验失败: %s", e)
        return 1

    logger.info("配置加载成功，共 %d 个摄像头", len(config.cameras))

    # 查找指定摄像头
    camera = None
    for c in config.cameras:
        if c.name == args.camera:
            camera = c
            break

    if camera is None:
        logger.error("未找到摄像头: %s（可用: %s）", args.camera, [c.name for c in config.cameras])
        return 1

    # 扫描人脸目录
    faces_dir = Path(config.faces_dir)
    try:
        face_scan = scan_face_directories(faces_dir)
    except FaceRegistryError as e:
        logger.error("人脸库加载失败: %s", e)
        return 1

    logger.info("人脸库加载成功: %s", face_scan.people)
    for w in face_scan.warnings:
        logger.warning("人脸库警告: %s", w)

    # 启动检查模式 or 持续监控
    if args.check:
        ok = run_check(config, camera, face_scan)
        return 0 if ok else 1

    try:
        run_monitor(config, camera, face_scan)
    except KeyboardInterrupt:
        logger.info("程序退出")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
