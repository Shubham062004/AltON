import time
import requests

url = 'https://integrate.api.nvidia.com/v1/chat/completions'
headers = {
    'Authorization': 'Bearer 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',
    'Content-Type': 'application/json'
}
data = {
    'model': 'meta/llama-3.1-70b-instruct',
    'messages': [{'role':'user', 'content':'what is polymorphism'}],
    'stream': True,
    'max_tokens': 512
}
try:
    start = time.time()
    with requests.post(url, headers=headers, json=data, stream=True, timeout=5) as r:
        print(f"Status: {r.status_code} in {time.time()-start:.2f}s")
except Exception as e:
    print(f"Error: {e} in {time.time()-start:.2f}s")
