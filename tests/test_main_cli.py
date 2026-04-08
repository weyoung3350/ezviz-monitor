from pathlib import Path
from unittest.mock import patch

from main import build_parser, main


def test_cli_accepts_camera_and_check():
    parser = build_parser()
    args = parser.parse_args(["--camera", "客厅", "--check"])

    assert args.camera == "客厅"
    assert args.check is True


def test_cli_camera_only():
    parser = build_parser()
    args = parser.parse_args(["--camera", "大门"])

    assert args.camera == "大门"
    assert args.check is False


def test_cli_with_config():
    parser = build_parser()
    args = parser.parse_args(["--camera", "客厅", "--config", "/tmp/test.yaml"])

    assert args.config == "/tmp/test.yaml"


def test_cli_default_config():
    parser = build_parser()
    args = parser.parse_args(["--camera", "客厅"])

    assert args.config == "config.yaml"


# --- 启动链路测试：face_recognition 缺失时的行为 ---

def _write_minimal_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """\
cameras:
  - name: "客厅"
    rtsp_url: "rtsp://fake"
    alert_schedules:
      - start: "00:00"
        end: "00:00"
alert:
  cooldown_minutes: 5
  stranger_frames_threshold: 3
  stranger_window_seconds: 2
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
""".format(
            faces_dir=str(tmp_path / "known_faces"),
            evidence_dir=str(tmp_path / "evidence"),
        ),
        encoding="utf-8",
    )
    # 准备人脸目录
    person_dir = tmp_path / "known_faces" / "测试"
    person_dir.mkdir(parents=True)
    (person_dir / "01.jpg").write_bytes(b"fake-image")
    return config_file


def test_main_check_mode_returns_1_when_face_recognition_missing(tmp_path: Path):
    """模拟 face_recognition 未安装时，--check 应返回 1 且不吐 traceback。"""
    config_file = _write_minimal_config(tmp_path)

    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "face_recognition":
            raise ImportError("No module named 'face_recognition'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with patch("sys.argv", ["main.py", "--camera", "客厅", "--check", "--config", str(config_file)]):
            exit_code = main()

    assert exit_code == 1


def test_main_monitor_returns_1_when_face_recognition_missing(tmp_path: Path):
    """模拟 face_recognition 未安装时，正常监控模式应返回 1 且不吐 traceback。"""
    config_file = _write_minimal_config(tmp_path)

    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "face_recognition":
            raise ImportError("No module named 'face_recognition'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with patch("sys.argv", ["main.py", "--camera", "客厅", "--config", str(config_file)]):
            exit_code = main()

    assert exit_code == 1
