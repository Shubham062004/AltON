import requests
import json

url = 'https://integrate.api.nvidia.com/v1/chat/completions'
headers = {
    'Authorization': 'Bearer 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',
    'Content-Type': 'application/json'
}
data = {
    'model': 'z-ai/glm-5.2',
    'messages': [{'role':'user', 'content':'what is polymorphism'}],
    'stream': True
}
try:
    with requests.post(url, headers=headers, json=data, stream=True, timeout=10) as r:
        with open('test_out.txt', 'w', encoding='utf-8') as f:
            f.write(f"Status: {r.status_code}\n")
            for chunk in r.iter_content(chunk_size=None):
                if chunk:
                    f.write(chunk.decode('utf-8'))
except Exception as e:
    with open('test_out.txt', 'w', encoding='utf-8') as f:
        f.write(f"Error: {e}")
