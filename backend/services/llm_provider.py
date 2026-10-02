import os
import json
import asyncio
import httpx
from typing import AsyncGenerator, List, Dict, Any

class MultiLLMRouter:
    """
    Unified Multi-LLM Provider Router for ULTRON OS.
    
    Priority Sequence (Cheapest & Fastest Capable Provider First):
    1. Ollama (Local zero-cost LLM)
    2. Groq (Free Tier / High Speed API)
    3. Gemini (Free Tier / Flash Models)
    4. Claude (Anthropic API if key set)
    5. OpenAI (GPT-4o / GPT-4o-mini if key set)
    6. NVIDIA API (NVIDIA NIM endpoints)
    """

    def __init__(self):
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    @staticmethod
    async def is_ollama_available() -> bool:
        """Check if local Ollama daemon is running."""
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(f"http://localhost:11434/api/tags")
                return res.status_code == 200
        except Exception:
            return False

    async def stream_completion(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        log_callback=None
    ) -> AsyncGenerator[str, None]:
        """
        Streams completion tokens trying providers in priority order.
        Yields text chunks.
        """
        formatted_messages = [{"role": "system", "content": system_prompt}] + messages

        # ---------------- 1. OLLAMA LOCAL (Priority #1) ----------------
        if await MultiLLMRouter.is_ollama_available():
            ollama_models = [
                os.getenv("OLLAMA_MODEL", "llama3.2"),
                "llama3",
                "mistral",
                "qwen2.5",
                "phi3"
            ]
            for omodel in ollama_models:
                try:
                    if log_callback:
                        await log_callback(f"[LLM ROUTER]: Trying Ollama local model '{omodel}'...")
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        payload = {
                            "model": omodel,
                            "messages": formatted_messages,
                            "stream": True
                        }
                        async with client.stream("POST", f"{self.ollama_base_url}/api/chat", json=payload) as resp:
                            if resp.status_code == 200:
                                async for line in resp.aiter_lines():
                                    if line.strip():
                                        data = json.loads(line)
                                        content = data.get("message", {}).get("content", "")
                                        if content:
                                            yield content
                                return
                except Exception as e:
                    print(f"[LLM ROUTER - OLLAMA FAIL on {omodel}]: {e}")
                    continue

        # ---------------- 2. GROQ API (Priority #2) ----------------
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            try:
                from groq import AsyncGroq
                if log_callback:
                    await log_callback("[LLM ROUTER]: Trying Groq API (Priority #2)...")
                groq_client = AsyncGroq(api_key=groq_key)
                groq_models = ["llama-3.3-70b-versatile", "llama3-8b-8192", "mixtral-8x7b-32768"]
                
                for gmodel in groq_models:
                    try:
                        stream = await groq_client.chat.completions.create(
                            messages=formatted_messages,
                            model=gmodel,
                            stream=True
                        )
                        async for chunk in stream:
                            if chunk.choices and chunk.choices[0].delta.content:
                                yield chunk.choices[0].delta.content
                        return
                    except Exception as ge:
                        print(f"[LLM ROUTER - GROQ FAIL on {gmodel}]: {ge}")
                        continue
            except Exception as e:
                print(f"[LLM ROUTER - GROQ CLIENT FAIL]: {e}")

        # ---------------- 3. GEMINI API (Priority #3) ----------------
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            try:
                import google.generativeai as genai
                if log_callback:
                    await log_callback("[LLM ROUTER]: Trying Gemini API (Priority #3)...")
                genai.configure(api_key=gemini_key)
                gemini_models = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-flash-latest"]
                
                for gm_name in gemini_models:
                    try:
                        gemini_model = genai.GenerativeModel(gm_name)
                        full_prompt = system_prompt + "\n\nConversation History:\n"
                        for h in messages:
                            role = "User" if h.get("role") == "user" else "Assistant"
                            full_prompt += f"{role}: {h.get('content', '')}\n"
                        
                        res = gemini_model.generate_content(full_prompt, stream=True)
                        for chunk in res:
                            if chunk.text:
                                yield chunk.text
                        return
                    except Exception as ge:
                        print(f"[LLM ROUTER - GEMINI FAIL on {gm_name}]: {ge}")
                        continue
            except Exception as e:
                print(f"[LLM ROUTER - GEMINI CLIENT FAIL]: {e}")

        # ---------------- 4. CLAUDE / ANTHROPIC (Priority #4) ----------------
        claude_key = os.getenv("ANTHROPIC_API_KEY")
        if claude_key:
            try:
                import anthropic
                if log_callback:
                    await log_callback("[LLM ROUTER]: Trying Anthropic Claude API (Priority #4)...")
                async_anthropic = anthropic.AsyncAnthropic(api_key=claude_key)
                async with async_anthropic.messages.stream(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=4096,
                    system=system_prompt,
                    messages=messages
                ) as stream:
                    async for text in stream.text_stream:
                        yield text
                return
            except Exception as e:
                print(f"[LLM ROUTER - CLAUDE FAIL]: {e}")

        # ---------------- 5. OPENAI API (Priority #5) ----------------
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                from openai import AsyncOpenAI
                if log_callback:
                    await log_callback("[LLM ROUTER]: Trying OpenAI API (Priority #5)...")
                oai_client = AsyncOpenAI(api_key=openai_key)
                stream = await oai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=formatted_messages,
                    stream=True
                )
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except Exception as e:
                print(f"[LLM ROUTER - OPENAI FAIL]: {e}")

        # ---------------- 6. NVIDIA API (Priority #6) ----------------
        nvidia_key = os.getenv("NVIDIA_API_KEY")
        if nvidia_key:
            try:
                from openai import AsyncOpenAI
                if log_callback:
                    await log_callback("[LLM ROUTER]: Trying NVIDIA API (Priority #6)...")
                nclient = AsyncOpenAI(
                    base_url="https://integrate.api.nvidia.com/v1",
                    api_key=nvidia_key
                )
                nvidia_models = [
                    "mistralai/mistral-nemotron",
                    "meta/llama-3.2-3b-instruct",
                    "ibm/granite-3.0-8b-instruct"
                ]
                for nmodel in nvidia_models:
                    try:
                        stream = await nclient.chat.completions.create(
                            model=nmodel,
                            messages=formatted_messages,
                            stream=True
                        )
                        async for chunk in stream:
                            if chunk.choices and chunk.choices[0].delta.content:
                                yield chunk.choices[0].delta.content
                        return
                    except Exception as ne:
                        print(f"[LLM ROUTER - NVIDIA FAIL on {nmodel}]: {ne}")
                        continue
            except Exception as e:
                print(f"[LLM ROUTER - NVIDIA CLIENT FAIL]: {e}")

        # If all providers fail
        yield "All configured LLM providers (Ollama, Groq, Gemini, Claude, OpenAI, NVIDIA) were unreachable or failed. Please check network connectivity or API key configuration."
