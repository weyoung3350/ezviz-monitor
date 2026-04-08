from datetime import datetime

import pytest

from src.phone_alert import (
    AliyunVmsClient,
    MockPhoneAlertClient,
    PhoneAlertEvent,
    create_phone_alert_client,
)


def _make_event() -> PhoneAlertEvent:
    return PhoneAlertEvent(
        person_name="杨孝治",
        camera_name="电梯厅",
        rule_name="杨孝治夜间外出监护",
        event_time=datetime(2026, 4, 8, 23, 15, 0),
    )


# --- MockPhoneAlertClient ---

def test_mock_success():
    client = MockPhoneAlertClient(should_succeed=True)
    result = client.call(_make_event())
    assert result.success is True
    assert result.error == ""
    assert len(client.call_history) == 1
    assert client.call_history[0].person_name == "杨孝治"


def test_mock_failure():
    client = MockPhoneAlertClient(should_succeed=False, error_message="网络超时")
    result = client.call(_make_event())
    assert result.success is False
    assert "网络超时" in result.error


def test_mock_records_multiple_calls():
    client = MockPhoneAlertClient(should_succeed=True)
    client.call(_make_event())
    client.call(_make_event())
    assert len(client.call_history) == 2


# --- AliyunVmsClient ---

def test_aliyun_client_init_requires_template_code():
    with pytest.raises(ValueError, match="template_code"):
        AliyunVmsClient(template_code="", called_numbers=["13800000000"])


def test_aliyun_client_init_requires_called_numbers():
    with pytest.raises(ValueError, match="called_numbers"):
        AliyunVmsClient(template_code="TTS_xxx", called_numbers=[])


def test_aliyun_client_call_returns_result():
    """骨架实现应返回成功（SDK 未接入时仅输出日志）。"""
    client = AliyunVmsClient(template_code="TTS_xxx", called_numbers=["13800000000"])
    result = client.call(_make_event())
    assert result.success is True


# --- create_phone_alert_client 工厂 ---

def test_create_aliyun_client():
    class FakeConfig:
        provider = "aliyun_vms"
        template_code = "TTS_xxx"
        called_numbers = ["13800000000"]

    client = create_phone_alert_client(FakeConfig())
    assert isinstance(client, AliyunVmsClient)


def test_create_mock_client():
    class FakeConfig:
        provider = "mock"
        template_code = ""
        called_numbers = []

    client = create_phone_alert_client(FakeConfig())
    assert isinstance(client, MockPhoneAlertClient)


def test_create_unsupported_provider_raises():
    class FakeConfig:
        provider = "twilio"
        template_code = ""
        called_numbers = []

    with pytest.raises(ValueError, match="不支持.*twilio"):
        create_phone_alert_client(FakeConfig())


# --- PhoneAlertEvent 字段完整性 ---

def test_event_fields():
    event = _make_event()
    assert event.person_name == "杨孝治"
    assert event.camera_name == "电梯厅"
    assert event.rule_name == "杨孝治夜间外出监护"
    assert event.event_time == datetime(2026, 4, 8, 23, 15, 0)
