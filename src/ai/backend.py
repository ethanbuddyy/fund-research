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


def _extract_tool_result(response, tool_name: str) -> str | None:
    """取出可解析为 JSON 的原始串：优先 tool_calls，其次「看起来是 JSON」的 content。
    纯自然语言作答（非推理模型在 auto 下可能跳过工具直接散文回答）返回 None，
    由调用方强制工具重试。"""
    msg = response.choices[0].message
    if msg.tool_calls:
        return msg.tool_calls[0].function.arguments
    content = (msg.content or "").strip()
    if not content:
        return None
    stripped = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped).strip()
    return content if stripped.startswith(("{", "[")) else None


def _call_openai(client, system, user_parts, tool, model, max_tokens):
    user_text = "\n\n".join(user_parts)
    oai_tool = _to_openai_tool(tool)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]

    def _create(tool_choice):
        return client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=messages,
            tools=[oai_tool], tool_choice=tool_choice,
        )

    # ① 先 auto：推理模型（如 deepseek-reasoner）拒绝强制 tool_choice，只能用 auto。
    raw = _extract_tool_result(_create("auto"), tool["name"])

    # ② auto 下非推理模型（如 deepseek-chat）可能不调工具、直接散文作答；
    #    强制指定该函数再试一次（reasoner 第一次即成功，走不到这里，不受影响）。
    if raw is None:
        try:
            forced = {"type": "function", "function": {"name": tool["name"]}}
            raw = _extract_tool_result(_create(forced), tool["name"])
        except Exception as e:
            raise ValueError(f"强制工具调用重试失败：{e}") from e

    if raw is None:
        raise ValueError("响应中既无 tool_calls 也无可解析为 JSON 的 content")

    return _parse_json(raw)
