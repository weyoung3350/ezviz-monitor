"""电话告警适配层。

定义统一的电话告警接口（PhoneAlertClient），
提供真实实现（AliyunVmsClient）和测试用 mock（MockPhoneAlertClient）。

使用方式：
    client = create_phone_alert_client(config.phone_alert)
    result = client.call(event)
    if not result.success:
        logger.error("电话告警失败: %s", result.error)
"""

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class PhoneAlertEvent:
    """电话告警事件，包含触发告警所需的全部上下文。"""
    person_name: str
    camera_name: str
    rule_name: str
    event_time: datetime


@dataclass
class PhoneAlertResult:
    """电话告警调用结果。"""
    success: bool
    error: str = ""


class PhoneAlertClient:
    """电话告警客户端接口。所有实现必须提供 call 方法。"""

    def call(self, event: PhoneAlertEvent) -> PhoneAlertResult:
        raise NotImplementedError


class AliyunVmsClient(PhoneAlertClient):
    """阿里云语音服务（VMS）电话告警实现。"""

    def __init__(
        self,
        template_code: str,
        called_numbers: list[str],
    ) -> None:
        if not template_code:
            raise ValueError("AliyunVmsClient: template_code 不能为空")
        if not called_numbers:
            raise ValueError("AliyunVmsClient: called_numbers 不能为空")
        self._template_code = template_code
        self._called_numbers = called_numbers

    def call(self, event: PhoneAlertEvent) -> PhoneAlertResult:
        """发起真实电话告警。

        当前阿里云 VMS SDK 尚未接入，返回明确的失败结果。
        接入后此方法应调用 SDK 并根据实际响应返回成功或失败。
        """
        logger.warning(
            "阿里云 VMS SDK 尚未接入，电话告警未实际拨出: "
            "号码=%s 模板=%s 人物=%s 摄像头=%s",
            self._called_numbers, self._template_code,
            event.person_name, event.camera_name,
        )
        return PhoneAlertResult(
            success=False,
            error="阿里云 VMS SDK 尚未接入，电话告警未实际拨出",
        )


class MockPhoneAlertClient(PhoneAlertClient):
    """测试用 mock 客户端。可配置成功或失败。"""

    def __init__(self, should_succeed: bool = True, error_message: str = "") -> None:
        self._should_succeed = should_succeed
        self._error_message = error_message
        self.call_history: list[PhoneAlertEvent] = []

    def call(self, event: PhoneAlertEvent) -> PhoneAlertResult:
        self.call_history.append(event)
        if self._should_succeed:
            return PhoneAlertResult(success=True)
        return PhoneAlertResult(success=False, error=self._error_message)


def create_phone_alert_client(phone_config) -> PhoneAlertClient:
    """根据配置创建对应的电话告警客户端。

    Args:
        phone_config: AppConfig.phone_alert (PhoneAlertConfig)

    Returns:
        PhoneAlertClient 实例

    Raises:
        ValueError: provider 不支持时
    """
    provider = phone_config.provider

    if provider == "aliyun_vms":
        return AliyunVmsClient(
            template_code=phone_config.template_code,
            called_numbers=phone_config.called_numbers,
        )
    elif provider == "mock":
        should_succeed = getattr(phone_config, "mock_should_succeed", True)
        error_message = getattr(phone_config, "mock_error_message", "")
        return MockPhoneAlertClient(
            should_succeed=should_succeed,
            error_message=error_message,
        )
    else:
        raise ValueError(f"不支持的电话告警 provider: {provider!r}")
