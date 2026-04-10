"""
统一通知服务 — AppDaemon App

监听 notify_service_request 事件，将通知分发到 Telegram / 电话通道。
支持静默规则、force_sound、图片发送、双通道独立容错。

业务侧只需 fire_event("notify_service_request", ...)，
本服务统一处理下发逻辑并发布 notify_service_result 结果事件。
"""

import json
from datetime import datetime

import appdaemon.plugins.hass.hassapi as hass


class NotifyService(hass.Hass):

    def initialize(self):
        # ── Telegram 配置 ──
        # HA 2026 版 telegram_bot 服务需要 entity_id 而非 chat_id
        self.telegram_entity_id = self.args.get("telegram_entity_id", "")
        self.telegram_parse_mode = self.args.get("telegram_parse_mode", "markdown")

        # ── 静默时段 ──
        self.silent_start = self.args.get("silent_start", "23:00")
        self.silent_end = self.args.get("silent_end", "07:00")

        # ── 电话通道配置 ──
        self.phone_enabled = self.args.get("phone_enabled", False)
        self.vms_access_key_id = self.args.get("vms_access_key_id", "")
        self.vms_access_key_secret = self.args.get("vms_access_key_secret", "")
        self.vms_called_number = self.args.get("vms_called_number", "")
        self.vms_tts_code = self.args.get("vms_tts_code", "")
        self.vms_called_show_number = self.args.get("vms_called_show_number", "")

        # ── VMS 客户端延迟初始化 ──
        self._vms_client = None
        if self.phone_enabled:
            self._init_vms_client()

        # ── 监听通知请求事件 ──
        self.listen_event(self.on_notify_request, "notify_service_request")
        self.log("NotifyService 已启动 | "
                 f"静默时段 {self.silent_start}~{self.silent_end} | "
                 f"电话通道 {'已启用' if self.phone_enabled else '已关闭'}")

    # ══════════════════════════════════════════════════════════
    #  VMS 客户端初始化
    # ══════════════════════════════════════════════════════════

    def _init_vms_client(self):
        """延迟初始化阿里云 VMS 客户端，避免 SDK 未安装时阻断启动。"""
        try:
            from alibabacloud_dyvmsapi20170525.client import Client
            from alibabacloud_tea_openapi import models as open_api_models

            config = open_api_models.Config(
                access_key_id=self.vms_access_key_id,
                access_key_secret=self.vms_access_key_secret,
            )
            config.endpoint = "dyvmsapi.aliyuncs.com"
            self._vms_client = Client(config)
            self.log("VMS 客户端初始化成功")
        except ImportError:
            self.log("阿里云 VMS SDK 未安装，电话通道不可用", level="WARNING")
        except Exception as e:
            self.log(f"VMS 客户端初始化失败: {e}", level="WARNING")

    # ══════════════════════════════════════════════════════════
    #  事件处理入口
    # ══════════════════════════════════════════════════════════

    def on_notify_request(self, event_name, data, kwargs):
        """处理 notify_service_request 事件，分发到各通道。"""
        channel = data.get("channel", "telegram")
        message = data.get("message", "")
        title = data.get("title", "")
        image_path = data.get("image_path", "")
        phone_alert_name = data.get("phone_alert_name", "")
        force_sound = data.get("force_sound", False)
        request_id = data.get("request_id", "")
        source = data.get("source", "")

        self.log(f"收到通知请求 | request_id={request_id} source={source} "
                 f"channel={channel} force_sound={force_sound}")

        # ── 初始化结果 ──
        telegram_result = {"attempted": False, "success": False, "error": None}
        phone_result = {"attempted": False, "success": False, "error": None}

        # ── Telegram 通道 ──
        if channel in ("telegram", "all"):
            if not message:
                telegram_result["attempted"] = True
                telegram_result["error"] = "message 为空，无法发送 Telegram"
                self.log(f"Telegram 跳过: message 为空 | request_id={request_id}",
                         level="ERROR")
            else:
                telegram_result = self._send_telegram(
                    message=message,
                    title=title,
                    image_path=image_path,
                    force_sound=force_sound,
                    request_id=request_id,
                )

        # ── 电话通道 ──
        if channel in ("phone", "all"):
            # 回退 phone_alert_name
            if not phone_alert_name:
                phone_alert_name = title or message[:20] or "HA通知"
            phone_result = self._send_phone(
                phone_alert_name=phone_alert_name,
                request_id=request_id,
            )

        # ── 发布结果事件 ──
        self.fire_event("notify_service_result", **{
            "request_id": request_id,
            "source": source,
            "channel": channel,
            "telegram": telegram_result,
            "phone": phone_result,
        })
        self.log(f"通知完成 | request_id={request_id} "
                 f"telegram={telegram_result} phone={phone_result}")

    # ══════════════════════════════════════════════════════════
    #  Telegram 通道
    # ══════════════════════════════════════════════════════════

    def _send_telegram(self, message, title, image_path, force_sound, request_id):
        """发送 Telegram 通知，支持图片和静默规则。"""
        result = {"attempted": True, "success": False, "error": None}

        # 判断是否静默
        disable_notification = False
        if not force_sound and self._is_silent_time():
            disable_notification = True

        # 组合标题和正文
        full_message = f"*{title}*\n{message}" if title else message

        try:
            if image_path:
                # 有图片时先尝试发送图片消息
                try:
                    service_data = {
                        "entity_id": self.telegram_entity_id,
                        "file": image_path,
                        "caption": full_message,
                        "disable_notification": disable_notification,
                    }
                    if self.telegram_parse_mode:
                        service_data["parse_mode"] = self.telegram_parse_mode
                    self.call_service("telegram_bot/send_photo", **service_data)
                    result["success"] = True
                    self.log(f"Telegram 图片消息已发送 | request_id={request_id}")
                    return result
                except Exception as img_err:
                    # 图片发送失败，补发文字说明
                    self.log(f"Telegram 图片发送失败，尝试补发文字 | "
                             f"request_id={request_id} error={img_err}",
                             level="WARNING")
                    fallback_msg = f"{full_message}\n\n(图片未能成功送达: {image_path})"
                    service_data = {
                        "entity_id": self.telegram_entity_id,
                        "message": fallback_msg,
                        "disable_notification": disable_notification,
                    }
                    if self.telegram_parse_mode:
                        service_data["parse_mode"] = self.telegram_parse_mode
                    self.call_service("telegram_bot/send_message", **service_data)
                    result["success"] = True
                    result["error"] = f"图片发送失败已补发文字: {img_err}"
                    self.log(f"Telegram 文字兜底已发送 | request_id={request_id}")
                    return result
            else:
                # 纯文字消息
                service_data = {
                    "entity_id": self.telegram_entity_id,
                    "message": full_message,
                    "disable_notification": disable_notification,
                }
                if self.telegram_parse_mode:
                    service_data["parse_mode"] = self.telegram_parse_mode
                self.call_service("telegram_bot/send_message", **service_data)
                result["success"] = True
                self.log(f"Telegram 文字消息已发送 | request_id={request_id}")
                return result

        except Exception as e:
            result["error"] = str(e)
            self.log(f"Telegram 发送失败 | request_id={request_id} error={e}",
                     level="ERROR")
            return result

    # ══════════════════════════════════════════════════════════
    #  电话通道
    # ══════════════════════════════════════════════════════════

    def _send_phone(self, phone_alert_name, request_id):
        """发起电话告警，支持 phone_enabled 开关和 VMS SDK 调用。"""
        result = {"attempted": False, "success": False, "error": None}

        # 检查电话通道开关
        if not self.phone_enabled:
            result["error"] = "phone channel disabled"
            self.log(f"电话通道未启用，跳过 | request_id={request_id}")
            return result

        result["attempted"] = True

        # 检查 VMS 客户端是否就绪
        if self._vms_client is None:
            result["error"] = "VMS 客户端未初始化（SDK 未安装或凭据错误）"
            self.log(f"VMS 客户端未就绪，无法拨打 | request_id={request_id}",
                     level="ERROR")
            return result

        try:
            # 延迟导入 VMS models，避免 SDK 未安装时阻断
            from alibabacloud_dyvmsapi20170525 import models as dyvmsapi_models

            tts_param = json.dumps({"alert_name": phone_alert_name})

            request = dyvmsapi_models.SingleCallByTtsRequest(
                called_number=self.vms_called_number,
                called_show_number=self.vms_called_show_number,
                tts_code=self.vms_tts_code,
                tts_param=tts_param,
            )

            self.log(f"正在拨打电话 | 号码={self.vms_called_number} "
                     f"模板={self.vms_tts_code} alert_name={phone_alert_name} "
                     f"request_id={request_id}")

            response = self._vms_client.single_call_by_tts(request)
            body = response.body

            if body.code == "OK":
                result["success"] = True
                self.log(f"电话呼叫成功 | CallId={body.call_id} "
                         f"request_id={request_id}")
            else:
                result["error"] = f"VMS 返回错误: {body.code} — {body.message}"
                self.log(f"电话呼叫失败 | code={body.code} msg={body.message} "
                         f"request_id={request_id}", level="ERROR")

        except Exception as e:
            result["error"] = str(e)
            self.log(f"电话呼叫异常 | request_id={request_id} error={e}",
                     level="ERROR")

        return result

    # ══════════════════════════════════════════════════════════
    #  静默时段判断
    # ══════════════════════════════════════════════════════════

    def _is_silent_time(self):
        """判断当前时间是否在静默时段内，支持跨天（如 23:00~07:00）。"""
        now = datetime.now().time()

        start_parts = self.silent_start.split(":")
        end_parts = self.silent_end.split(":")
        start = datetime.now().replace(
            hour=int(start_parts[0]), minute=int(start_parts[1]),
            second=0, microsecond=0,
        ).time()
        end = datetime.now().replace(
            hour=int(end_parts[0]), minute=int(end_parts[1]),
            second=0, microsecond=0,
        ).time()

        if start <= end:
            # 不跨天，如 08:00~12:00
            return start <= now <= end
        else:
            # 跨天，如 23:00~07:00
            return now >= start or now <= end
