from main import build_parser


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
