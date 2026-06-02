"""LLM client factory — supports anthropic and openai-compatible backends."""
import os

from ..utils.config import load_config

_client = None
_provider: str | None = None

_SUPPORTED = ("anthropic", "deepseek", "openai")


def get_provider() -> str:
    global _provider
    if _provider is None:
        cfg = load_config().get("ai_analysis", {})
        p = cfg.get("provider", "anthropic").lower()
        if p not in _SUPPORTED:
            raise ValueError(f"不支持的 provider: {p}，可选: {' / '.join(_SUPPORTED)}")
        _provider = p
    return _provider


def get_client():
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def _build_client():
    provider = get_provider()
    cfg = load_config().get("ai_analysis", {})

    if provider == "anthropic":
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key", "")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY 未配置。"
                "请设置环境变量 ANTHROPIC_API_KEY 或在 config/settings.yaml 中填写 ai_analysis.anthropic_api_key"
            )
        return anthropic.Anthropic(api_key=api_key, timeout=120.0)

    # deepseek / openai — both are OpenAI-compatible
    from openai import OpenAI
    env_var = "DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"
    api_key = os.environ.get(env_var) or cfg.get("api_key", "")
    if not api_key:
        raise ValueError(
            f"{env_var} 未配置。"
            f"请设置环境变量 {env_var} 或在 config/settings.yaml 中填写 ai_analysis.api_key"
        )
    _default_base_urls = {
        "deepseek": "https://api.deepseek.com",
        "openai": "https://api.openai.com/v1",
    }
    base_url = cfg.get("base_url") or _default_base_urls[provider]
    return OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)


def is_configured() -> bool:
    try:
        get_client()
        return True
    except (ValueError, ImportError):
        return False


def reset_client() -> None:
    global _client, _provider
    _client = None
    _provider = None
