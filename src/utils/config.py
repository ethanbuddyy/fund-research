import re
import yaml
import os
from pathlib import Path

# 形如 YOUR_FRED_API_KEY_HERE / YOUR-XXX-HERE 的占位符：非空字符串（truthy），
# 若被下游当成有效 key 会发起注定 401 的请求。回退到 example 时尤其常见。
_PLACEHOLDER_RE = re.compile(r"^YOUR[_\- ].*HERE$", re.IGNORECASE)

# 顶层 API Key 字段 → 对应的环境变量名（环境变量优先于配置文件）
_API_KEY_ENV = {
    "fred_api_key": "FRED_API_KEY",
    "finnhub_api_key": "FINNHUB_API_KEY",
    "alphavantage_api_key": "ALPHAVANTAGE_API_KEY",
}


def _is_placeholder(value) -> bool:
    return isinstance(value, str) and bool(_PLACEHOLDER_RE.match(value.strip()))


def load_config() -> dict:
    base = Path(__file__).parent.parent.parent
    config_path = base / "config" / "settings.yaml"
    if not config_path.exists():
        example = base / "config" / "settings.yaml.example"
        if example.exists():
            print("[WARN] config/settings.yaml 不存在，使用 settings.yaml.example"
                  "（请复制为 settings.yaml 并填写 API Key）")
            config_path = example
        else:
            raise FileNotFoundError(
                f"配置文件 {config_path} 不存在，"
                "请将 config/settings.yaml.example 复制为 config/settings.yaml 并完成配置"
            )
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # API Key 解析：环境变量优先；占位符值显式置空，避免被下游误当作有效 key。
    for key, env_name in _API_KEY_ENV.items():
        env_val = os.environ.get(env_name)
        if env_val:
            cfg[key] = env_val
            continue
        if _is_placeholder(cfg.get(key)):
            print(f"[WARN] 配置项 {key} 仍是占位符（{cfg.get(key)!r}），"
                  f"已置空 → 相关数据源将降级为模拟/回退（可设置环境变量 {env_name} 启用真实数据）")
            cfg[key] = ""
    return cfg


def get_db_path() -> str:
    cfg = load_config()
    base = Path(__file__).parent.parent.parent
    return str(base / cfg["db_path"])
