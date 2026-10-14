"""Time-to-first-token and total time of the chat LLM for the same real prompt, per model / reasoning effort.
`python -m eval.llm_latency PROMPT.json MODEL[:effort] ...` where PROMPT.json is the Context Inspector's output."""
import asyncio
import json
import sys
import time

from app.config import settings
from app.providers.llm import OpenAICompatLLMProvider


async def one(messages, spec: str):
    model, _, effort = spec.partition(":")
    p = OpenAICompatLLMProvider("groq", settings.groq_api_key, model, 1500, 0, 60.0, reasoning_effort=effort or "low")
    t0, first, n = time.perf_counter(), None, 0
    try:
        async for tok in p.stream(messages):
            if first is None:
                first = time.perf_counter() - t0
            n += len(tok)
    except Exception as exc:
        return f"{spec:34} FAILED {str(exc)[:110]}"
    return f"{spec:34} first token {first or 0:5.2f}s   total {time.perf_counter() - t0:5.2f}s   {n:5d} chars"


async def main():
    turn = json.load(open(sys.argv[1], encoding="utf-8"))["turn"]
    print(f"prompt ~{turn['prompt_tokens_estimate']} tokens")
    for spec in sys.argv[2:]:
        print(await one(turn["messages"], spec))
        await asyncio.sleep(1)

asyncio.run(main())
