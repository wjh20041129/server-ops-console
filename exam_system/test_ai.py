#!/usr/bin/env python3
import os
from dotenv import load_dotenv
import requests

load_dotenv()

BAILIAN_API_KEY = os.environ.get('BAILIAN_API_KEY', '')
BAILIAN_BASE_URL = os.environ.get('BAILIAN_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3')
BAILIAN_MODEL = os.environ.get('BAILIAN_MODEL', 'ark-code-latest')

print(f"测试API配置:")
print(f"API_KEY: {BAILIAN_API_KEY[:10]}...")
print(f"BASE_URL: {BAILIAN_BASE_URL}")
print(f"MODEL: {BAILIAN_MODEL}")

if not BAILIAN_API_KEY:
    print("❌ API_KEY未配置")
    exit(1)

headers = {
    'Authorization': f'Bearer {BAILIAN_API_KEY}',
    'Content-Type': 'application/json'
}

payload = {
    'model': BAILIAN_MODEL,
    'messages': [
        {"role": "user", "content": "你好，测试一下API是否正常"}
    ],
    'temperature': 0.7,
    'max_tokens': 500
}

try:
    print("正在调用API...")
    resp = requests.post(
        f'{BAILIAN_BASE_URL}/chat/completions',
        headers=headers,
        json=payload,
        timeout=30
    )
    print(f"状态码: {resp.status_code}")
    if resp.status_code == 200:
        result = resp.json()
        print(f"✅ 调用成功: {result['choices'][0]['message']['content'][:100]}...")
    else:
        print(f"❌ 调用失败: {resp.text[:500]}")
except Exception as e:
    print(f"❌ 异常: {str(e)}")
