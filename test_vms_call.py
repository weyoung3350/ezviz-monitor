"""阿里云语音通知服务 (VMS) 测试脚本"""
import json
from alibabacloud_dyvmsapi20170525.client import Client
from alibabacloud_dyvmsapi20170525 import models as dyvmsapi_models
from alibabacloud_tea_openapi import models as open_api_models

# 配置 — 从环境变量读取，不在代码中硬编码
import os

ACCESS_KEY_ID = os.environ.get("ALIYUN_ACCESS_KEY_ID", "")
ACCESS_KEY_SECRET = os.environ.get("ALIYUN_ACCESS_KEY_SECRET", "")
CALLED_NUMBER = os.environ.get("VMS_CALLED_NUMBER", "")
TTS_CODE = os.environ.get("VMS_TTS_CODE", "TTS_295415053")
CALLED_SHOW_NUMBER = os.environ.get("VMS_CALLED_SHOW_NUMBER", "")
TTS_PARAM = json.dumps({"alert_name": "夜间门内开锁测试"})

def create_client():
    config = open_api_models.Config(
        access_key_id=ACCESS_KEY_ID,
        access_key_secret=ACCESS_KEY_SECRET,
    )
    config.endpoint = "dyvmsapi.aliyuncs.com"
    return Client(config)

def main():
    client = create_client()

    request = dyvmsapi_models.SingleCallByTtsRequest(
        called_number=CALLED_NUMBER,
        called_show_number=CALLED_SHOW_NUMBER,
        tts_code=TTS_CODE,
        tts_param=TTS_PARAM,
    )

    print(f"正在拨打 {CALLED_NUMBER}...")
    print(f"模板: {TTS_CODE}")
    print(f"参数: {TTS_PARAM}")
    print()

    try:
        response = client.single_call_by_tts(request)
        body = response.body
        print(f"RequestId: {body.request_id}")
        print(f"Code: {body.code}")
        print(f"Message: {body.message}")
        print(f"CallId: {body.call_id}")

        if body.code == "OK":
            print("\n✅ 呼叫请求已发送，请等待来电。")
        else:
            print(f"\n❌ 呼叫失败: {body.code} — {body.message}")
    except Exception as e:
        print(f"\n❌ 异常: {e}")

if __name__ == "__main__":
    main()
