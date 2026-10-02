_client = None

def get_groq_client():
    global _client
    api_key = os.getenv("GROQ_API_KEY")
    if api_key and not _client:
        _client = AsyncGroq(api_key=api_key)
    return _client

async def stream_chat(messages, system_prompt="You are ULTRON, a highly advanced AI assistant."):
    """
    Streams a response from Groq using Llama 3.
    """
    client = get_groq_client()
    if not client:
        yield "Groq API key not configured on backend."
        return

    formatted_messages = [{"role": "system", "content": system_prompt}] + messages
    
    stream = await client.chat.completions.create(
        messages=formatted_messages,
        model="llama3-8b-8192",
        stream=True,
    )
    
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content
