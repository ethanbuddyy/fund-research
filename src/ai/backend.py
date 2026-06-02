"""Provider-agnostic tool-call wrapper for Phase 1 / Phase 2."""
import json
import re

from .client import get_client, get_provider
from .cache_strategy import build_cached_block, build_uncached_block


def _parse_json(raw: str) -> dict:
    """Parse JSON from model output, with repair fallback for DeepSeek quirks."""
    # If the model wrapped the JSON in a markdown code block, strip it.
    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r'\s*```$', '', raw.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        try:
            from json_repair import repair_json
            return json.loads(repair_json(raw))
        except Exception:
            raise ValueError(
                f"无效 JSON（长度 {len(raw)}，位置 {e.pos}）: {e.msg}\n"
                f"  片段: {repr(raw[max(0, e.pos-40):e.pos+40])}"
            ) from e


def _to_openai_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        },
    }


def call_with_tools(
    system: str,
    user_parts: list[str],
    tool: dict,
    model: str,
    max_tokens: int,
    *,
    cache_system: bool = True,
    cache_first_user: bool = False,
) -> dict:
    """Call the configured LLM with tool use; return the tool input dict."""
    provider = get_provider()
    client = get_client()

    if provider == "anthropic":
        return _call_anthropic(
            client, system, user_parts, tool, model, max_tokens,
            cache_system=cache_system,
            cache_first_user=cache_first_user,
        )
    return _call_openai(client, system, user_parts, tool, model, max_tokens)


def _call_anthropic(client, system, user_parts, tool, model, max_tokens, *, cache_system, cache_first_user):
    sys_blocks = [build_cached_block(system) if cache_system else build_uncached_block(system)]
    content = []
    for i, part in enumerate(user_parts):
        if i == 0 and cache_first_user:
            content.append(build_cached_block(part))
        else:
            content.append(build_uncached_block(part))

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=sys_blocks,
        messages=[{"role": "user", "content": content}],
        tools=[tool],
        tool_choice={"type": "any"},
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return block.input
    raise ValueError(f"响应中未找到 tool_use block: {tool['name']}")


def _call_openai(client, system, user_parts, tool, model, max_tokens):
    user_text = "\n\n".join(user_parts)
    oai_tool = _to_openai_tool(tool)

    # Thinking/reasoning models (e.g. deepseek-v4-pro) reject forced tool_choice.
    # Use "auto" so they can decide, then extract the result from wherever it lands.
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        tools=[oai_tool],
        tool_choice="auto",
    )
    msg = response.choices[0].message

    # Prefer tool_calls; fall back to raw content (thinking models may inline JSON).
    if msg.tool_calls:
        raw = msg.tool_calls[0].function.arguments
    elif msg.content:
        raw = msg.content
    else:
        raise ValueError("响应中既无 tool_calls 也无 content")

    return _parse_json(raw)
