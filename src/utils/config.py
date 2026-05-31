import yaml
import os
from pathlib import Path


def load_config() -> dict:
    base = Path(__file__).parent.parent.parent
    config_path = base / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 环境变量优先于配置文件中的 API Key
    if os.environ.get("FRED_API_KEY"):
        cfg["fred_api_key"] = os.environ["FRED_API_KEY"]
    return cfg


def get_db_path() -> str:
    cfg = load_config()
    base = Path(__file__).parent.parent.parent
    return str(base / cfg["db_path"])
