import os, json
from openai import OpenAI

def main():
    client = OpenAI(
        base_url='https://integrate.api.nvidia.com/v1',
        api_key='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'
    )
    stream = client.chat.completions.create(
        model='z-ai/glm-5.2',
        messages=[{'role': 'user', 'content': 'what is polymorphism'}],
        stream=True,
        temperature=1,
        top_p=1,
        max_tokens=16384,
        seed=42
    )
    for chunk in stream:
        print(chunk)

main()
