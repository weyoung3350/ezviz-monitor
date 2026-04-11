"""
统一通知服务 — AppDaemon App

监听 notify_service_request 事件，将通知分发到 钉钉 / iOS 推送 / 电话通道。
支持静默规则、force_sound、图片发送、多通道独立容错。

业务侧只需 fire_event("notify_service_request", ...)，
本服务统一处理下发逻辑并发布 notify_service_result 结果事件。

channel 字段支持：
  - 单个字符串："dingtalk" / "ios_push" / "phone" / "all"
  - 字符串数组：["dingtalk", "ios_push"]（多通道并发，不含未列出的通道）
  - "all" 等价于 ["dingtalk", "ios_push", "phone"]

异步调度：
  - on_notify_request 是 async 回调，三个通道通过 asyncio.to_thread + gather 并发执行
  - 各 _send_* 方法保持 sync 实现（内部调用第三方 SDK / HTTP / self.call_service），
    由 asyncio.to_thread 将其派发到线程池执行，避免阻塞 AppDaemon 事件循环。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime

import appdaemon.plugins.hass.hassapi as hass


class NotifyService(hass.Hass):

    def initialize(self):
        # ── 钉钉自定义机器人配置 ──
        self.dingtalk_webhook = self.args.get("dingtalk_webhook", "")
        self.dingtalk_secret = self.args.get("dingtalk_secret", "")
        self.dingtalk_image_base_url = self.args.get(
            "dingtalk_image_base_url",
            "http://192.168.77.253:8123/local/",
        )
        self.dingtalk_enabled = bool(self.dingtalk_webhook and self.dingtalk_secret)

        # ── iOS Companion App 推送配置 ──
        self.ios_push_service = self.args.get("ios_push_service", "")
        self.ios_push_enabled = bool(self.ios_push_service)

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
                 f"钉钉 {'已启用' if self.dingtalk_enabled else '已关闭'} | "
                 f"iOS推送 {'已启用' if self.ios_push_enabled else '已关闭'} | "
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
    #  通道解析
    # ══════════════════════════════════════════════════════════

    def _resolve_channels(self, channel_raw):
        """将 channel 字段规范化为小写字符串列表。"""
        if isinstance(channel_raw, str):
            return [channel_raw.strip().lower()]
        if isinstance(channel_raw, (list, tuple)):
            return [str(c).strip().lower() for c in channel_raw]
        return ["dingtalk"]

    def _channel_active(self, channels, name):
        return "all" in channels or name in channels

    # ══════════════════════════════════════════════════════════
    #  事件处理入口（异步，三通道并发）
    # ══════════════════════════════════════════════════════════

    async def on_notify_request(self, event_name, data, kwargs):
        """处理 notify_service_request 事件，三通道并发分发。

        同步的 _send_dingtalk / _send_ios_push / _send_phone 通过
        asyncio.to_thread 派发到线程池，由 asyncio.gather 并发等待，
        总耗时 ≈ max(各通道)，不会阻塞 AppDaemon 事件循环。
        """
        channels = self._resolve_channels(data.get("channel", "dingtalk"))
        message = data.get("message", "")
        title = data.get("title", "")
        image_path = data.get("image_path", "")
        phone_alert_name = data.get("phone_alert_name", "")
        force_sound = data.get("force_sound", False)
        request_id = data.get("request_id", "")
        source = data.get("source", "")

        self.log(f"收到通知请求 | request_id={request_id} source={source} "
                 f"channels={channels} force_sound={force_sound}")

        # ── 初始化结果 ──
        dingtalk_result = {"attempted": False, "success": False, "error": None}
        ios_push_result = {"attempted": False, "success": False, "error": None}
        phone_result = {"attempted": False, "success": False, "error": None}

        # ── 构造并发任务 ──
        tasks: dict[str, asyncio.Task] = {}

        # 钉钉通道
        if self._channel_active(channels, "dingtalk"):
            if not message:
                dingtalk_result = {
                    "attempted": True,
                    "success": False,
                    "error": "message 为空，无法发送钉钉",
                }
                self.log(f"钉钉 跳过: message 为空 | request_id={request_id}",
                         level="ERROR")
            else:
                tasks["dingtalk"] = asyncio.create_task(asyncio.to_thread(
                    self._send_dingtalk,
                    message=message,
                    title=title,
                    image_path=image_path,
                    request_id=request_id,
                ))

        # iOS Companion App 推送通道
        if self._channel_active(channels, "ios_push"):
            if not message:
                ios_push_result = {
                    "attempted": True,
                    "success": False,
                    "error": "message 为空",
                }
            else:
                tasks["ios_push"] = asyncio.create_task(asyncio.to_thread(
                    self._send_ios_push,
                    message=message,
                    title=title,
                    image_path=image_path,
                    force_sound=force_sound,
                    request_id=request_id,
                ))

        # 电话通道
        if self._channel_active(channels, "phone"):
            if not phone_alert_name:
                phone_alert_name = title or message[:20] or "HA通知"
            tasks["phone"] = asyncio.create_task(asyncio.to_thread(
                self._send_phone,
                phone_alert_name=phone_alert_name,
                request_id=request_id,
            ))

        # ── 并发等待全部通道结果（单通道异常不影响其他通道） ──
        if tasks:
            names = list(tasks.keys())
            results = await asyncio.gather(
                *tasks.values(),
                return_exceptions=True,
            )
            for name, result in zip(names, results):
                if isinstance(result, Exception):
                    result = {
                        "attempted": True,
                        "success": False,
                        "error": f"线程异常: {result!r}",
                    }
                    self.log(f"通道 {name} 线程异常 | request_id={request_id} "
                             f"error={result['error']}", level="ERROR")
                if name == "dingtalk":
                    dingtalk_result = result
                elif name == "ios_push":
                    ios_push_result = result
                elif name == "phone":
                    phone_result = result

        # ── 发布结果事件 ──
        self.fire_event("notify_service_result", **{
            "request_id": request_id,
            "source": source,
            "channels": channels,
            "dingtalk": dingtalk_result,
            "ios_push": ios_push_result,
            "phone": phone_result,
        })
        self.log(f"通知完成 | request_id={request_id} "
                 f"dingtalk={dingtalk_result} ios_push={ios_push_result} "
                 f"phone={phone_result}")

    # ══════════════════════════════════════════════════════════
    #  钉钉通道（自定义机器人 + 加签）
    # ══════════════════════════════════════════════════════════

    def _send_dingtalk(self, message, title, image_path, request_id):
        """发送钉钉自定义机器人通知。

        有 image_path 时走 markdown 消息内嵌图片 URL；
        无图时走 text 消息。force_sound 在钉钉通道上不生效（钉钉 webhook 无响铃控制），
        真正需要响铃仍由 phone 通道负责。
        """
        result = {"attempted": True, "success": False, "error": None}

        if not self.dingtalk_enabled:
            result["error"] = "dingtalk webhook/secret 未配置"
            self.log(f"钉钉未配置，跳过 | request_id={request_id}", level="WARNING")
            return result

        try:
            signed_url = self._build_dingtalk_signed_url()

            if image_path:
                # 将 /config/www/xxx.jpg 转为 {dingtalk_image_base_url}xxx.jpg
                image_url = image_path.replace(
                    "/config/www/", self.dingtalk_image_base_url
                )
                md_title = title or "HA 告警"
                md_text = f"# {md_title}\n\n{message}\n\n![snapshot]({image_url})"
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": md_title,
                        "text": md_text,
                    },
                }
            else:
                text_content = f"【{title}】\n{message}" if title else message
                payload = {
                    "msgtype": "text",
                    "text": {"content": text_content},
                }

            req = urllib.request.Request(
                signed_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                response_text = response.read().decode("utf-8")
                response_data = json.loads(response_text)

            if response_data.get("errcode") == 0:
                result["success"] = True
                self.log(f"钉钉消息已发送 | request_id={request_id} "
                         f"has_image={bool(image_path)}")
            else:
                result["error"] = (f"errcode={response_data.get('errcode')} "
                                   f"errmsg={response_data.get('errmsg')}")
                self.log(f"钉钉发送失败 | request_id={request_id} "
                         f"{result['error']}", level="ERROR")

        except Exception as e:
            result["error"] = str(e)
            self.log(f"钉钉发送异常 | request_id={request_id} error={e}",
                     level="ERROR")

        return result

    def _build_dingtalk_signed_url(self):
        """按钉钉自定义机器人签名规范构造带 timestamp+sign 的 URL。"""
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.dingtalk_secret}"
        hmac_code = hmac.new(
            self.dingtalk_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        separator = "&" if "?" in self.dingtalk_webhook else "?"
        return f"{self.dingtalk_webhook}{separator}timestamp={timestamp}&sign={sign}"

    # ══════════════════════════════════════════════════════════
    #  iOS Companion App 推送通道
    # ══════════════════════════════════════════════════════════

    def _send_ios_push(self, message, title, image_path, force_sound, request_id):
        """通过 HA Companion App 发送 iOS 推送通知。"""
        result = {"attempted": False, "success": False, "error": None}

        if not self.ios_push_enabled:
            result["error"] = "iOS push not configured"
            return result

        result["attempted"] = True

        try:
            # 构建通知数据
            service_data = {
                "message": message,
                "title": title or "Home Assistant",
            }

            # iOS 推送的声音控制
            push_data = {}
            if force_sound:
                push_data["push"] = {"sound": {"name": "default", "critical": 1, "volume": 1.0}}
            elif self._is_silent_time():
                push_data["push"] = {"sound": "none"}

            # 图片附件（HA Companion App 支持通过 /local/ 路径发图）
            if image_path:
                # 将 /config/www/xxx.jpg 转为 /local/xxx.jpg URL
                local_url = image_path.replace("/config/www/", "/local/")
                push_data["attachment"] = {"url": local_url, "content-type": "jpeg"}

            if push_data:
                service_data["data"] = push_data

            # 调用 notify 服务（如 notify.mobile_app_dna_iphone15p）
            self.call_service(
                self.ios_push_service.replace(".", "/", 1),
                **service_data
            )
            result["success"] = True
            self.log(f"iOS 推送已发送 | request_id={request_id}")

        except Exception as e:
            result["error"] = str(e)
            self.log(f"iOS 推送失败 | request_id={request_id} error={e}",
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
