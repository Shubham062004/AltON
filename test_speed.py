import time
import requests

url = 'https://integrate.api.nvidia.com/v1/chat/completions'
headers = {
    'Authorization': 'Bearer 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',
    'Content-Type': 'application/json'
}

for model in ['meta/llama-3.1-70b-instruct', 'meta/llama-3.1-8b-instruct']:
    data = {
        'model': model,
        'messages': [{'role':'user', 'content':'write a long story about a dog'}],
        'stream': True,
        'max_tokens': 512
    }
    start = time.time()
    tokens = 0
    with requests.post(url, headers=headers, json=data, stream=True) as r:
        first_token_time = None
        for chunk in r.iter_content(chunk_size=None):
            if chunk:
                if not first_token_time:
                    first_token_time = time.time() - start
                tokens += 1
    total_time = time.time() - start
    print(f"[{model}] TTFT: {first_token_time:.2f}s | Total: {total_time:.2f}s | Chunks: {tokens}")
