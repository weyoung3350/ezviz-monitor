import re
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator


class ConfigError(Exception):
    pass


class AlertSchedule(BaseModel):
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}", v):
            raise ValueError(f"时间格式必须为 HH:MM，实际值: {v!r}")
        parts = v.split(":")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"时间值超出范围: {v!r}（时 0-23，分 0-59）")
        return v


class MonitorRule(BaseModel):
    rule_name: str
    person_name: str
    alert_schedules: list[AlertSchedule]
    actions: list[str]

    @field_validator("actions")
    @classmethod
    def actions_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("monitor_rules.actions 不能为空")
        return v


class CameraConfig(BaseModel):
    name: str
    rtsp_url: str
    monitor_rules: list[MonitorRule] = []


class AlertConfig(BaseModel):
    cooldown_minutes: int
    person_frames_threshold: int
    person_window_seconds: int


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


class ProfileConfig(BaseModel):
    name: str
    role: str = ""
    gender: str = ""
    age: int = 0
    mobility: str = ""
    notes: str = ""


class PhoneAlertConfig(BaseModel):
    provider: str
    enabled: bool = False
    template_code: str = ""
    called_numbers: list[str] = []

    @field_validator("called_numbers")
    @classmethod
    def numbers_when_enabled(cls, v: list[str], info) -> list[str]:
        # 在模型校验阶段只做格式检查，启用状态的业务校验在 load_config 中做
        return v


class AppConfig(BaseModel):
    cameras: list[CameraConfig]
    alert: AlertConfig
    faces_dir: str
    evidence_dir: str
    video: VideoConfig
    storage: StorageConfig
    stream: StreamConfig
    profiles: list[ProfileConfig] = []
    phone_alert: PhoneAlertConfig


def _ensure_unique_camera_names(config: AppConfig) -> None:
    names = [c.name for c in config.cameras]
    if len(names) != len(set(names)):
        raise ConfigError("摄像头名称重复")


def _ensure_phone_alert_complete(config: AppConfig) -> None:
    pa = config.phone_alert
    if not pa.enabled:
        raise ConfigError("phone_alert.enabled 必须为 true（电话告警是一期核心能力）")
    if not pa.provider:
        raise ConfigError("phone_alert.enabled=true 但 provider 为空")
    if not pa.template_code:
        raise ConfigError("phone_alert.enabled=true 但 template_code 为空")
    if not pa.called_numbers:
        raise ConfigError("phone_alert.enabled=true 但 called_numbers 为空")


def _ensure_monitor_rules_valid(config: AppConfig) -> None:
    profile_names = {p.name for p in config.profiles}
    all_rules = []
    for cam in config.cameras:
        for rule in cam.monitor_rules:
            all_rules.append(rule)
            if not rule.alert_schedules:
                raise ConfigError(
                    f"摄像头 '{cam.name}' 的规则 '{rule.rule_name}' 缺少 alert_schedules"
                )
            if rule.person_name not in profile_names:
                raise ConfigError(
                    f"规则 '{rule.rule_name}' 的 person_name '{rule.person_name}' "
                    f"在 profiles 中不存在"
                )
            if "phone_call" not in rule.actions:
                raise ConfigError(
                    f"规则 '{rule.rule_name}' 的 actions 必须包含 'phone_call'"
                    f"（电话告警是一期核心能力）"
                )
    if not all_rules:
        raise ConfigError("至少需要配置一条 monitor_rules")
    person_names_in_rules = {r.person_name for r in all_rules}
    if "杨孝治" not in person_names_in_rules:
        raise ConfigError("monitor_rules 中必须包含至少一条针对 '杨孝治' 的规则")


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        config = AppConfig.model_validate(raw)
    except Exception as e:
        raise ConfigError(f"配置校验失败: {e}") from e
    _ensure_unique_camera_names(config)
    _ensure_monitor_rules_valid(config)
    _ensure_phone_alert_complete(config)
    return config
