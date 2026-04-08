from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator


class ConfigError(Exception):
    pass


class AlertSchedule(BaseModel):
    start: str
    end: str


class CameraConfig(BaseModel):
    name: str
    rtsp_url: str
    alert_schedules: list[AlertSchedule]
    detect_outside_schedule: bool = True


class AlertConfig(BaseModel):
    cooldown_minutes: int
    stranger_frames_threshold: int
    stranger_window_seconds: int


class VideoConfig(BaseModel):
    pre_seconds: int
    post_seconds: int
    output_format: str


class StorageConfig(BaseModel):
    max_evidence_size_gb: int

    @field_validator("max_evidence_size_gb")
    @classmethod
    def must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_evidence_size_gb 必须大于 0")
        return v


class StreamConfig(BaseModel):
    reconnect_interval_seconds: int


class AppConfig(BaseModel):
    cameras: list[CameraConfig]
    alert: AlertConfig
    faces_dir: str
    evidence_dir: str
    video: VideoConfig
    storage: StorageConfig
    stream: StreamConfig


def _ensure_unique_camera_names(config: AppConfig) -> None:
    names = [c.name for c in config.cameras]
    if len(names) != len(set(names)):
        raise ConfigError("摄像头名称重复")


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        config = AppConfig.model_validate(raw)
    except Exception as e:
        raise ConfigError(f"配置校验失败: {e}") from e
    _ensure_unique_camera_names(config)
    return config
