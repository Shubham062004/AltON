import asyncio
from openai import AsyncOpenAI
import codecs

async def test():
    client = AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="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"
    )
    
    stream = await client.chat.completions.create(
        model="meta/llama-3.1-8b-instruct",
        messages=[{"role": "user", "content": "Translate 'Prime Minister' to Hindi. Only write the word in Hindi, nothing else."}],
        stream=True,
        temperature=0.1,
    )
    
    result = ""
    with codecs.open('hindi_out.txt', 'w', encoding='utf-8') as f:
        async for chunk in stream:
            if getattr(chunk.choices[0].delta, "content", None):
                txt = chunk.choices[0].delta.content
                f.write(f"Chunk: {repr(txt)}\n")
                result += txt
        f.write(f"\nFinal: {result}\n")

asyncio.run(test())
