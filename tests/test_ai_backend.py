"""AI backend（provider 无关工具调用包装）健壮性测试（issue #5）。

backend._call_openai 为兼容 DeepSeek 思考型/非思考型模型做了三级回退：
  ① auto tool_choice → ② 强制 function → ③ inline-schema 纯 JSON 兜底。
这套逻辑依赖各 provider 的具体返回/报错行为，provider 升级极易悄悄打破。这里用
假 client 把三条路径各钉一遍，并覆盖 _parse_json（markdown 去壳 + 修复）与
_extract_tool_result（tool_calls / JSON content / 散文→None）。
"""
import json
from types import SimpleNamespace

import pytest

from src.ai import backend


TOOL = {
    "name": "emit",
    "description": "emit structured result",
    "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
}


# ── _parse_json ──────────────────────────────────────────────

class TestParseJson:
    def test_plain_json(self):
        assert backend._parse_json('{"x": 1}') == {"x": 1}

    def test_strips_markdown_fence(self):
        assert backend._parse_json('```json\n{"x": 2}\n```') == {"x": 2}

    def test_strips_bare_fence(self):
        assert backend._parse_json('```\n{"x": 3}\n```') == {"x": 3}

    def test_repairs_trailing_comma(self):
        # json_repair 修掉尾逗号；若库缺失则应抛 ValueError（不静默吞）
        try:
            assert backend._parse_json('{"x": 4,}') == {"x": 4}
        except ValueError:
            pass

    def test_irreparable_raises_valueerror(self):
        with pytest.raises(ValueError):
            backend._parse_json("this is not json at all <<<")


# ── _extract_tool_result ─────────────────────────────────────

def _resp(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class TestExtractToolResult:
    def test_prefers_tool_calls(self):
        msg = SimpleNamespace(
            tool_calls=[SimpleNamespace(function=SimpleNamespace(arguments='{"x": 1}'))],
            content="ignored",
        )
        assert backend._extract_tool_result(_resp(msg), "emit") == '{"x": 1}'

    def test_json_looking_content_when_no_tool_calls(self):
        msg = SimpleNamespace(tool_calls=None, content='```json\n{"x": 2}\n```')
        out = backend._extract_tool_result(_resp(msg), "emit")
        assert out and out.strip().startswith("```")  # 原文返回，留给 _parse_json 去壳

    def test_prose_returns_none(self):
        msg = SimpleNamespace(tool_calls=None, content="这是一段自然语言回答，没有 JSON。")
        assert backend._extract_tool_result(_resp(msg), "emit") is None

    def test_empty_content_returns_none(self):
        msg = SimpleNamespace(tool_calls=None, content="")
        assert backend._extract_tool_result(_resp(msg), "emit") is None


# ── _call_openai 三级回退 ─────────────────────────────────────

class _FakeCompletions:
    """按预设脚本依次返回 message；记录每次 tool_choice 以断言回退路径。"""
    def __init__(self, scripted_messages):
        self._msgs = list(scripted_messages)
        self.tool_choices = []

    def create(self, *, model, max_tokens, messages, tools, tool_choice):
        self.tool_choices.append(tool_choice)
        msg = self._msgs.pop(0)
        return _resp(msg)


class _FakeClient:
    def __init__(self, scripted_messages):
        self.chat = SimpleNamespace(completions=_FakeCompletions(scripted_messages))


def _call(client):
    return backend._call_openai(client, "sys", ["user"], TOOL, "deepseek-chat", 1000)


class TestCallOpenAiFallback:
    def test_tier1_auto_tool_calls(self):
        # ① auto 直接给 tool_calls → 一次成功
        msg = SimpleNamespace(
            tool_calls=[SimpleNamespace(function=SimpleNamespace(arguments='{"x": 11}'))],
            content=None,
        )
        client = _FakeClient([msg])
        assert _call(client) == {"x": 11}
        assert client.chat.completions.tool_choices == ["auto"]

    def test_tier2_forced_function(self):
        # ① auto 返回散文（None）→ ② 强制 function 成功
        prose = SimpleNamespace(tool_calls=None, content="先讲一段理由……")
        forced = SimpleNamespace(
            tool_calls=[SimpleNamespace(function=SimpleNamespace(arguments='{"x": 22}'))],
            content=None,
        )
        client = _FakeClient([prose, forced])
        assert _call(client) == {"x": 22}
        choices = client.chat.completions.tool_choices
        assert choices[0] == "auto"
        assert choices[1]["function"]["name"] == "emit"  # 强制指定该函数

    def test_tier3_inline_schema_json(self):
        # ① auto 散文 → ② 强制工具被拒（思考型模型 400）→ ③ inline-schema 纯 JSON
        prose1 = SimpleNamespace(tool_calls=None, content="思考中……")
        json3 = SimpleNamespace(tool_calls=None, content='结果如下：{"x": 33} 完毕')

        class _Picky(_FakeCompletions):
            def create(self, *, tool_choice, **kw):
                self.tool_choices.append(tool_choice)
                if isinstance(tool_choice, dict):  # 强制 function → 思考型模型拒绝
                    raise RuntimeError("400 Thinking mode does not support this tool_choice")
                return _resp(self._msgs.pop(0))

        client = SimpleNamespace(chat=SimpleNamespace(
            completions=_Picky([prose1, json3])))
        # ③ 截取首个 { 到末个 }，解析出 {"x": 33}
        assert _call(client) == {"x": 33}

    def test_all_tiers_exhausted_raises(self):
        prose = SimpleNamespace(tool_calls=None, content="纯散文，无 JSON。")
        client = _FakeClient([prose, prose, prose])
        with pytest.raises(ValueError):
            _call(client)
