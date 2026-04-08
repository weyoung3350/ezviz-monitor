from pathlib import Path
from unittest.mock import patch

from main import build_parser, main


# --- 参数解析 ---

def test_cli_accepts_camera_and_check():
    parser = build_parser()
    args = parser.parse_args(["--camera", "电梯厅", "--check"])
    assert args.camera == "电梯厅"
    assert args.check is True


def test_cli_camera_only():
    parser = build_parser()
    args = parser.parse_args(["--camera", "电梯厅"])
    assert args.camera == "电梯厅"
    assert args.check is False


def test_cli_default_config():
    parser = build_parser()
    args = parser.parse_args(["--camera", "电梯厅"])
    assert args.config == "config.yaml"


def test_cli_custom_config():
    parser = build_parser()
    args = parser.parse_args(["--camera", "电梯厅", "--config", "/tmp/test.yaml"])
    assert args.config == "/tmp/test.yaml"


# --- 启动链路集成测试的配置工具 ---

def _write_valid_config(tmp_path: Path) -> Path:
    """写入符合当前目标的最小有效配置。"""
    config_file = tmp_path / "config.yaml"
    faces_dir = tmp_path / "known_faces"
    evidence_dir = tmp_path / "evidence"

    config_file.write_text(
        f"""\
cameras:
  - name: "电梯厅"
    rtsp_url: "rtsp://fake:554/h265"
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
faces_dir: "{faces_dir}"
evidence_dir: "{evidence_dir}"
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
phone_alert:
  provider: "aliyun_vms"
  enabled: true
  template_code: "TTS_xxx"
  called_numbers:
    - "13800000000"
""",
        encoding="utf-8",
    )

    # 准备杨孝治人脸目录
    person_dir = faces_dir / "杨孝治"
    person_dir.mkdir(parents=True)
    (person_dir / "01.jpg").write_bytes(b"fake-image")

    return config_file


# --- face_recognition 缺失时 --check 返回 1 ---

def test_check_returns_1_when_face_recognition_missing(tmp_path: Path):
    config_file = _write_valid_config(tmp_path)

    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "face_recognition":
            raise ImportError("No module named 'face_recognition'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with patch("sys.argv", ["main.py", "--camera", "电梯厅", "--check", "--config", str(config_file)]):
            exit_code = main()

    assert exit_code == 1


# --- face_recognition 缺失时监控模式返回 1 ---

def test_monitor_returns_1_when_face_recognition_missing(tmp_path: Path):
    config_file = _write_valid_config(tmp_path)

    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "face_recognition":
            raise ImportError("No module named 'face_recognition'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with patch("sys.argv", ["main.py", "--camera", "电梯厅", "--config", str(config_file)]):
            exit_code = main()

    assert exit_code == 1


# --- 配置文件不存在时返回 1 ---

def test_check_returns_1_when_config_missing(tmp_path: Path):
    with patch("sys.argv", ["main.py", "--camera", "电梯厅", "--check", "--config", str(tmp_path / "nonexistent.yaml")]):
        exit_code = main()
    assert exit_code == 1


# --- 旧配置格式（陌生人告警）时返回 1 ---

def test_check_returns_1_with_old_config_format(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""\
cameras:
  - name: "客厅"
    rtsp_url: "rtsp://fake"
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
""", encoding="utf-8")

    with patch("sys.argv", ["main.py", "--camera", "客厅", "--check", "--config", str(config_file)]):
        exit_code = main()
    assert exit_code == 1
