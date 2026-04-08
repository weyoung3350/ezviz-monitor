import pytest
from pathlib import Path

from src.config import load_config, ConfigError


VALID_CONFIG = """\
cameras:
  - name: "电梯厅"
    rtsp_url: "rtsp://admin:ACNPUB@192.168.77.117:554/h265"
    monitor_rules:
      - rule_name: "杨孝治夜间外出监护"
        person_name: "杨孝治"
        alert_schedules:
          - start: "22:00"
            end: "07:00"
        actions:
          - "phone_call"
          - "terminal_log"
          - "save_evidence"
alert:
  cooldown_minutes: 5
  person_frames_threshold: 2
  person_window_seconds: 2
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
profiles:
  - name: "杨孝治"
    role: "重点监护对象"
    gender: "male"
    age: 77
    mobility: "slow"
    notes: "阿尔兹海默"
phone_alert:
  provider: "aliyun_vms"
  enabled: true
  template_code: "TTS_xxx"
  called_numbers:
    - "13800000000"
"""


# --- 正常加载 ---

def test_load_full_config(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text(VALID_CONFIG, encoding="utf-8")
    config = load_config(f)

    assert config.cameras[0].name == "电梯厅"
    rule = config.cameras[0].monitor_rules[0]
    assert rule.rule_name == "杨孝治夜间外出监护"
    assert rule.person_name == "杨孝治"
    assert rule.alert_schedules[0].start == "22:00"
    assert rule.alert_schedules[0].end == "07:00"
    assert "phone_call" in rule.actions


def test_load_profiles(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text(VALID_CONFIG, encoding="utf-8")
    config = load_config(f)

    assert len(config.profiles) == 1
    assert config.profiles[0].name == "杨孝治"
    assert config.profiles[0].age == 77


def test_load_phone_alert(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text(VALID_CONFIG, encoding="utf-8")
    config = load_config(f)

    assert config.phone_alert is not None
    assert config.phone_alert.enabled is True
    assert config.phone_alert.provider == "aliyun_vms"
    assert "13800000000" in config.phone_alert.called_numbers


def test_load_alert_config(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text(VALID_CONFIG, encoding="utf-8")
    config = load_config(f)

    assert config.alert.cooldown_minutes == 5
    assert config.alert.person_frames_threshold == 2
    assert config.alert.person_window_seconds == 2


def test_cross_day_schedule(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text(VALID_CONFIG, encoding="utf-8")
    config = load_config(f)
    schedule = config.cameras[0].monitor_rules[0].alert_schedules[0]
    assert schedule.start == "22:00"
    assert schedule.end == "07:00"


# --- 摄像头名称重复 ---

def test_duplicate_camera_names_raises(tmp_path: Path):
    text = VALID_CONFIG.replace(
        'cameras:\n  - name: "电梯厅"',
        'cameras:\n  - name: "电梯厅"\n    rtsp_url: "rtsp://dup1"\n  - name: "电梯厅"',
    )
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="重复"):
        load_config(f)


# --- monitor_rules 缺失 ---

def test_no_monitor_rules_raises(tmp_path: Path):
    text = VALID_CONFIG.replace(
        """    monitor_rules:
      - rule_name: "杨孝治夜间外出监护"
        person_name: "杨孝治"
        alert_schedules:
          - start: "22:00"
            end: "07:00"
        actions:
          - "phone_call"
          - "terminal_log"
          - "save_evidence"
""",
        "",
    )
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="monitor_rules"):
        load_config(f)


# --- person_name 缺失 ---

def test_missing_person_name_raises(tmp_path: Path):
    text = VALID_CONFIG.replace('person_name: "杨孝治"', "")
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(f)


# --- actions 为空 ---

def test_empty_actions_raises(tmp_path: Path):
    text = VALID_CONFIG.replace(
        """        actions:
          - "phone_call"
          - "terminal_log"
          - "save_evidence"
""",
        "        actions: []\n",
    )
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(f)


# --- phone_alert 启用但关键字段缺失 ---

def test_phone_alert_enabled_missing_provider_raises(tmp_path: Path):
    text = VALID_CONFIG.replace('provider: "aliyun_vms"', 'provider: ""')
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="provider"):
        load_config(f)


def test_phone_alert_enabled_missing_template_raises(tmp_path: Path):
    text = VALID_CONFIG.replace('template_code: "TTS_xxx"', 'template_code: ""')
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="template_code"):
        load_config(f)


def test_phone_alert_enabled_missing_numbers_raises(tmp_path: Path):
    text = VALID_CONFIG.replace(
        """  called_numbers:
    - "13800000000"
""",
        "  called_numbers: []\n",
    )
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="called_numbers"):
        load_config(f)


# --- phone_alert 未启用时不校验详细字段 ---

def test_phone_alert_disabled_skips_validation(tmp_path: Path):
    text = VALID_CONFIG.replace("enabled: true", "enabled: false")
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    config = load_config(f)
    assert config.phone_alert.enabled is False


# --- phone_alert 完全缺失时不报错（可选节点）---

def test_phone_alert_absent_is_ok(tmp_path: Path):
    text = VALID_CONFIG.replace(
        """phone_alert:
  provider: "aliyun_vms"
  enabled: true
  template_code: "TTS_xxx"
  called_numbers:
    - "13800000000"
""",
        "",
    )
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    config = load_config(f)
    assert config.phone_alert is None


# --- 时间格式校验（复用 AlertSchedule 验证器）---

def test_invalid_time_format_raises(tmp_path: Path):
    text = VALID_CONFIG.replace('start: "22:00"', 'start: "abc"')
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="HH:MM"):
        load_config(f)


def test_time_out_of_range_raises(tmp_path: Path):
    text = VALID_CONFIG.replace('start: "22:00"', 'start: "25:00"')
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="超出范围"):
        load_config(f)


# --- storage ---

def test_invalid_max_evidence_size_raises(tmp_path: Path):
    text = VALID_CONFIG.replace("max_evidence_size_gb: 20", "max_evidence_size_gb: 0")
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(f)


# --- 缺少必填顶层字段 ---

def test_missing_required_top_level_raises(tmp_path: Path):
    text = """\
cameras:
  - name: "电梯厅"
    rtsp_url: "rtsp://example"
"""
    f = tmp_path / "config.yaml"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(f)


# --- 旧配置格式（陌生人告警）应明确失败 ---

def test_old_stranger_config_format_raises(tmp_path: Path):
    old_config = """\
cameras:
  - name: "客厅"
    rtsp_url: "rtsp://example"
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
    f = tmp_path / "config.yaml"
    f.write_text(old_config, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(f)
