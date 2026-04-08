import pytest
from pathlib import Path

from src.config import load_config, ConfigError


VALID_CONFIG = """\
cameras:
  - name: "大门"
    rtsp_url: "rtsp://admin:password@192.168.1.100:554/h264"
    alert_schedules:
      - start: "22:00"
        end: "07:00"
alert:
  cooldown_minutes: 5
  stranger_frames_threshold: 3
  stranger_window_seconds: 2
faces_dir: "./known_faces"
evidence_dir: "./evidence"
video:
  pre_seconds: 5
  post_seconds: 5
  output_format: "mp4"
storage:
  max_evidence_size_gb: 20
stream:
  reconnect_interval_seconds: 10
"""


def test_load_config_reads_camera_and_storage_limit(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")

    config = load_config(config_file)

    assert config.cameras[0].name == "大门"
    assert config.storage.max_evidence_size_gb == 20


def test_load_config_reads_all_fields(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")

    config = load_config(config_file)

    assert config.alert.cooldown_minutes == 5
    assert config.alert.stranger_frames_threshold == 3
    assert config.alert.stranger_window_seconds == 2
    assert config.faces_dir == "./known_faces"
    assert config.evidence_dir == "./evidence"
    assert config.video.pre_seconds == 5
    assert config.video.post_seconds == 5
    assert config.video.output_format == "mp4"
    assert config.stream.reconnect_interval_seconds == 10
    assert config.cameras[0].alert_schedules[0].start == "22:00"
    assert config.cameras[0].alert_schedules[0].end == "07:00"


def test_duplicate_camera_names_raises(tmp_path: Path):
    config_text = """\
cameras:
  - name: "大门"
    rtsp_url: "rtsp://example1"
    alert_schedules:
      - start: "22:00"
        end: "07:00"
  - name: "大门"
    rtsp_url: "rtsp://example2"
    alert_schedules:
      - start: "00:00"
        end: "06:00"
alert:
  cooldown_minutes: 5
  stranger_frames_threshold: 3
  stranger_window_seconds: 2
faces_dir: "./known_faces"
evidence_dir: "./evidence"
video:
  pre_seconds: 5
  post_seconds: 5
  output_format: "mp4"
storage:
  max_evidence_size_gb: 20
stream:
  reconnect_interval_seconds: 10
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_text, encoding="utf-8")

    with pytest.raises(ConfigError, match="重复"):
        load_config(config_file)


def test_invalid_max_evidence_size_raises(tmp_path: Path):
    config_text = VALID_CONFIG.replace("max_evidence_size_gb: 20", "max_evidence_size_gb: 0")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_text, encoding="utf-8")

    with pytest.raises((ConfigError, Exception)):
        load_config(config_file)


def test_missing_required_field_raises(tmp_path: Path):
    config_text = """\
cameras:
  - name: "大门"
    rtsp_url: "rtsp://example"
    alert_schedules:
      - start: "22:00"
        end: "07:00"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_text, encoding="utf-8")

    with pytest.raises(Exception):
        load_config(config_file)


def test_cross_day_schedule_in_config(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")

    config = load_config(config_file)
    schedule = config.cameras[0].alert_schedules[0]
    assert schedule.start == "22:00"
    assert schedule.end == "07:00"
