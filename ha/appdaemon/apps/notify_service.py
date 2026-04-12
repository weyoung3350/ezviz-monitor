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
  - on_notify_request 是 async 回调，三个通道通过 AppDaemon 原生 self.run_in_executor
    + asyncio.gather 并发执行
  - 各 _send_* 方法保持 sync 实现（内部调用第三方 SDK / HTTP / self.call_service），
    由 AppDaemon 自己的线程池派发，生命周期随 app reload/terminate 一起清理
"""

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.utils import formatdate
from functools import partial

import appdaemon.plugins.hass.hassapi as hass


# ── 合法通道白名单 ──
VALID_CHANNELS = {"dingtalk", "ios_push", "phone", "all"}

# ── 快照路径前缀（钉钉图片 URL 仅接受此前缀下的文件）──
SNAPSHOT_PATH_PREFIX = "/config/www/"


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

        # ── 阿里云 OSS 配置（用于钉钉 markdown 图片外网 URL）──
        self.oss_access_key_id = self.args.get("oss_access_key_id", "")
        self.oss_access_key_secret = self.args.get("oss_access_key_secret", "")
        self.oss_endpoint = self.args.get("oss_endpoint", "")
        self.oss_bucket = self.args.get("oss_bucket", "")
        self.oss_download_url = self.args.get("oss_download_url", "")
        self.oss_key_prefix = self.args.get("oss_key_prefix", "night_guard/")
        self.oss_enabled = bool(
            self.oss_access_key_id
            and self.oss_access_key_secret
            and self.oss_endpoint
            and self.oss_bucket
            and self.oss_download_url
        )

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
                 f"OSS {'已启用' if self.oss_enabled else '已关闭'} | "
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
    #  通道解析与校验
    # ══════════════════════════════════════════════════════════

    def _resolve_channels(self, channel_raw):
        """将 channel 字段规范化为合法通道名列表。

        返回 (channels, errors)：
          - channels: 规范化后的通道名列表，只包含 VALID_CHANNELS 中的值
          - errors: 解析过程中的警告消息列表，供调用方记录 ERROR 日志

        非字符串 / 非列表 → 视为非法，返回空列表 + 错误信息
        空列表 / 全部未知值 → 返回空列表 + 错误信息
        """
        errors = []

        if isinstance(channel_raw, str):
            raw_list = [channel_raw.strip().lower()]
        elif isinstance(channel_raw, (list, tuple)):
            raw_list = [str(c).strip().lower() for c in channel_raw if str(c).strip()]
        else:
            return [], [f"channel 字段类型非法: {type(channel_raw).__name__}"]

        if not raw_list:
            return [], ["channel 为空，无法分发"]

        valid = []
        unknown = []
        for c in raw_list:
            if c in VALID_CHANNELS:
                if c not in valid:  # 去重
                    valid.append(c)
            else:
                unknown.append(c)

        if unknown:
            errors.append(f"未知通道被忽略: {unknown}")
        if not valid:
            errors.append("解析后没有有效通道")

        return valid, errors

    def _channel_active(self, channels, name):
        return "all" in channels or name in channels

    # ══════════════════════════════════════════════════════════
    #  事件处理入口（异步，三通道并发）
    # ══════════════════════════════════════════════════════════

    async def on_notify_request(self, event_name, data, kwargs):
        """处理 notify_service_request 事件，三通道并发分发。

        同步的 _send_dingtalk / _send_ios_push / _send_phone 通过
        self.run_in_executor 派发到 AppDaemon 内部线程池，由 asyncio.gather
        并发等待，总耗时 ≈ max(各通道)，不会阻塞 AppDaemon 事件循环。
        使用 AppDaemon 原生 executor 而非 asyncio.to_thread，确保任务归属
        随 app reload/terminate 被正确清理。
        """
        message = data.get("message", "")
        title = data.get("title", "")
        image_path = data.get("image_path", "")
        phone_alert_name = data.get("phone_alert_name", "")
        force_sound = data.get("force_sound", False)
        request_id = data.get("request_id", "")
        source = data.get("source", "")

        # ── 通道解析与校验 ──
        channels, parse_errors = self._resolve_channels(
            data.get("channel", "dingtalk")
        )
        for err in parse_errors:
            self.log(f"通道解析错误 | request_id={request_id} {err}", level="ERROR")

        self.log(f"收到通知请求 | request_id={request_id} source={source} "
                 f"channels={channels} force_sound={force_sound}")

        # ── 初始化结果 ──
        dingtalk_result = {"attempted": False, "success": False, "error": None}
        ios_push_result = {"attempted": False, "success": False, "error": None}
        phone_result = {"attempted": False, "success": False, "error": None}

        # ── 通道解析失败：直接发布失败结果事件 ──
        if not channels:
            error_text = "; ".join(parse_errors) or "no valid channels"
            dingtalk_result["error"] = error_text
            ios_push_result["error"] = error_text
            phone_result["error"] = error_text
            self.fire_event("notify_service_result", **{
                "request_id": request_id,
                "source": source,
                "channels": [],
                "dingtalk": dingtalk_result,
                "ios_push": ios_push_result,
                "phone": phone_result,
            })
            self.log(f"通道解析失败，放弃分发 | request_id={request_id} "
                     f"error={error_text}", level="ERROR")
            return

        # ── 构造并发任务（使用 AppDaemon 原生 run_in_executor）──
        tasks: dict[str, object] = {}

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
                tasks["dingtalk"] = self.run_in_executor(partial(
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
                tasks["ios_push"] = self.run_in_executor(partial(
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
            tasks["phone"] = self.run_in_executor(partial(
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

        image_path 必须以 SNAPSHOT_PATH_PREFIX（"/config/www/"）开头，否则降级为
        纯文字消息并记录 WARNING，避免拼出无效 URL 却记为成功。
        """
        result = {"attempted": True, "success": False, "error": None}

        if not self.dingtalk_enabled:
            result["error"] = "dingtalk webhook/secret 未配置"
            self.log(f"钉钉未配置，跳过 | request_id={request_id}", level="WARNING")
            return result

        # 图片路径前缀校验 —— 不合法则降级为纯文字
        use_image = False
        if image_path:
            if image_path.startswith(SNAPSHOT_PATH_PREFIX):
                use_image = True
            else:
                self.log(
                    f"钉钉图片路径不以 {SNAPSHOT_PATH_PREFIX} 开头，降级为纯文字 | "
                    f"image_path={image_path} request_id={request_id}",
                    level="WARNING",
                )

        try:
            signed_url = self._build_dingtalk_signed_url()

            if use_image:
                # 优先走 OSS 外网 URL（钉钉客户端可访问），失败则降级到内网 URL
                oss_url = None
                if self.oss_enabled:
                    oss_url = self._upload_image_to_oss(image_path, request_id)
                if oss_url:
                    image_url = oss_url
                else:
                    image_url = image_path.replace(
                        SNAPSHOT_PATH_PREFIX, self.dingtalk_image_base_url
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
                         f"has_image={use_image}")
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
    #  阿里云 OSS 图片上传（stdlib HTTP + v1 签名）
    # ══════════════════════════════════════════════════════════

    def _upload_image_to_oss(self, local_path, request_id):
        """将本地图片上传到阿里云 OSS，返回外网可访问的 URL；失败返回 None。

        使用 OSS v1 签名（HMAC-SHA1），通过 stdlib urllib PUT object，
        避免引入 oss2 SDK 依赖。签名算法参考：
        https://help.aliyun.com/document_detail/31951.html
        """
        if not self.oss_enabled:
            return None

        # 路径翻译：调用方传入的 image_path 是 HA Core 视角的 /config/www/xxx.jpg，
        # 而 AppDaemon 容器约定把 HA config 挂在 /homeassistant/。
        # 先尝试原路径，再回退到 /homeassistant/ 翻译路径，避免硬编码。
        read_path = local_path
        if not os.path.isfile(read_path):
            alt_path = local_path.replace("/config/", "/homeassistant/", 1)
            if alt_path != local_path and os.path.isfile(alt_path):
                read_path = alt_path
            else:
                self.log(
                    f"OSS 上传跳过: 文件不存在 {local_path}（也尝试了 {alt_path}）| "
                    f"request_id={request_id}",
                    level="WARNING",
                )
                return None

        try:
            filename = os.path.basename(read_path)
            key = f"{self.oss_key_prefix}{filename}"

            with open(read_path, "rb") as f:
                body = f.read()

            content_type = (
                mimetypes.guess_type(filename)[0] or "application/octet-stream"
            )
            content_md5 = base64.b64encode(hashlib.md5(body).digest()).decode("ascii")
            date_str = formatdate(usegmt=True)
            canonical_resource = f"/{self.oss_bucket}/{key}"

            # 自定义 OSS 头需要按 key 字典序进入 CanonicalizedOSSHeaders
            # 这里设置对象 ACL 为 public-read，让钉钉 / 微信 等外部客户端可直接访问
            oss_headers = {"x-oss-object-acl": "public-read"}
            canonical_oss_headers = "".join(
                f"{k}:{v}\n" for k, v in sorted(oss_headers.items())
            )

            string_to_sign = (
                f"PUT\n{content_md5}\n{content_type}\n{date_str}\n"
                f"{canonical_oss_headers}{canonical_resource}"
            )
            signature = base64.b64encode(
                hmac.new(
                    self.oss_access_key_secret.encode("utf-8"),
                    string_to_sign.encode("utf-8"),
                    digestmod=hashlib.sha1,
                ).digest()
            ).decode("ascii")

            put_url = f"https://{self.oss_bucket}.{self.oss_endpoint}/{key}"
            req = urllib.request.Request(
                put_url,
                data=body,
                method="PUT",
                headers={
                    "Content-Type": content_type,
                    "Content-MD5": content_md5,
                    "Date": date_str,
                    "Authorization": f"OSS {self.oss_access_key_id}:{signature}",
                    "Content-Length": str(len(body)),
                    **oss_headers,
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = resp.status if hasattr(resp, "status") else resp.getcode()
                if status in (200, 201):
                    download_url = f"{self.oss_download_url.rstrip('/')}/{key}"
                    self.log(
                        f"OSS 上传成功 | request_id={request_id} "
                        f"key={key} url={download_url}"
                    )
                    return download_url
                self.log(
                    f"OSS 上传状态异常 status={status} | request_id={request_id}",
                    level="ERROR",
                )
                return None

        except Exception as e:
            self.log(
                f"OSS 上传失败 | request_id={request_id} error={e}",
                level="ERROR",
            )
            return None

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
