import yaml
import os
from pathlib import Path


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
        cfg = yaml.safe_load(f)
    # 环境变量优先于配置文件中的 API Key
    if os.environ.get("FRED_API_KEY"):
        cfg["fred_api_key"] = os.environ["FRED_API_KEY"]
    return cfg


def get_db_path() -> str:
    cfg = load_config()
    base = Path(__file__).parent.parent.parent
    return str(base / cfg["db_path"])
