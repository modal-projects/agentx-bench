"""Stub chat-completions server as a Modal ASGI web app."""

import asyncio
import json
import time
import uuid

import modal

app = modal.App("stub-agentx")

image = modal.Image.debian_slim(python_version="3.12").uv_pip_install("fastapi==0.141.1")

# Canned completion
REPLY_TEXT = (
    "This stub server has no intelligence; it streams this canned reply in "
    "small chunks with synthetic timing so benchmarks can measure time to "
    "first token, inter-token latency, and throughput against a lightweight backend."
)
# Chunking and delays; output streams in at most MAX_CHUNKS equal-size chunks
# so decode time stays roughly constant regardless of requested length
MAX_CHUNKS = 16
TTFT_SECONDS = 0.01
INTER_CHUNK_SECONDS = 0.005

# There is no real tokenizer, so we fake it
APPROX_CHARS_PER_TOKEN = 4


def split_tokens(text: str) -> list[str]:
    """Split text into whitespace-delimited pseudo-tokens, keeping spacing."""
    words = text.split(" ")
    return [word + " " for word in words[:-1]] + [words[-1]]


REPLY_TOKENS = split_tokens(REPLY_TEXT)
REPLY_WORDS = REPLY_TEXT.split(" ")


def completion_tokens(payload: dict) -> tuple[list[str], str]:
    """Pick the tokens for one request, emitting the requested output length.

    Benchmark turns request an exact length (max_completion_tokens /
    max_tokens) and pass ignore_eos:true; like a real server under those
    flags, emit exactly that many tokens by cycling the canned words. With no
    requested length, emit the canned text once.
    """
    limit = payload.get("max_completion_tokens") or payload.get("max_tokens")
    if isinstance(limit, int) and limit > 0:
        words = [REPLY_WORDS[i % len(REPLY_WORDS)] for i in range(limit)]
        return [word + " " for word in words[:-1]] + [words[-1]], "length"
    return list(REPLY_TOKENS), "stop"


def estimate_prompt_tokens(messages) -> int:
    """Estimate prompt tokens from message text (string or content-part lists)."""
    chars = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            chars += sum(
                len(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    return max(1, (chars + APPROX_CHARS_PER_TOKEN - 1) // APPROX_CHARS_PER_TOKEN)


def usage_object(prompt_tokens: int, completion_token_count: int) -> dict:
    """Report token usage for token-based metrics"""
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_token_count,
        "total_tokens": prompt_tokens + completion_token_count,
    }


def base_chunk(payload: dict, completion_id: str, created: int) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": payload.get("model") or "stub",
    }


def choice_chunk(
    payload: dict,
    completion_id: str,
    created: int,
    delta: dict,
    finish_reason: str | None,
) -> dict:
    """A chunk carrying a single choice delta."""
    chunk = base_chunk(payload, completion_id, created)
    chunk["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    return chunk


def sse_frame(obj) -> str:
    """Serialize one object as an SSE data frame."""
    return f"data: {json.dumps(obj)}\n\n"


async def stream_completion(payload: dict):
    """Yield SSE frames for one streaming chat-completion request."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    tokens, finish_reason = completion_tokens(payload)
    usage = usage_object(
        estimate_prompt_tokens(payload.get("messages")), len(tokens)
    )

    await asyncio.sleep(TTFT_SECONDS)
    yield sse_frame(choice_chunk(payload, completion_id, created, {"role": "assistant"}, None))

    per_chunk = max(1, -(-len(tokens) // MAX_CHUNKS))
    for start in range(0, len(tokens), per_chunk):
        await asyncio.sleep(INTER_CHUNK_SECONDS)
        piece = "".join(tokens[start : start + per_chunk])
        yield sse_frame(choice_chunk(payload, completion_id, created, {"content": piece}, None))

    yield sse_frame(choice_chunk(payload, completion_id, created, {}, finish_reason))

    stream_options = payload.get("stream_options") or {}
    if stream_options.get("include_usage"):
        chunk = base_chunk(payload, completion_id, created)
        chunk["choices"] = []
        chunk["usage"] = usage
        yield sse_frame(chunk)

    yield "data: [DONE]\n\n"


def completion_response(payload: dict) -> dict:
    """Build a complete non-streaming chat-completion response."""
    tokens, finish_reason = completion_tokens(payload)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.get("model") or "stub",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(tokens)},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage_object(
            estimate_prompt_tokens(payload.get("messages")), len(tokens)
        ),
    }


@app.function(image=image)
@modal.asgi_app()
def serve():
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse

    web = FastAPI()

    @web.get("/")
    async def health():
        return {"status": "ok"}

    @web.get("/v1/models")
    async def models():
        return {
            "object": "list",
            "data": [{"id": "stub", "object": "model"}],
        }

    async def chat(request: Request):
        payload = await request.json()
        if payload.get("stream"):
            return StreamingResponse(
                stream_completion(payload),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        return completion_response(payload)

    web.add_api_route("/v1/chat/completions", chat, methods=["POST"])
    web.add_api_route("/chat/completions", chat, methods=["POST"])
    return web
