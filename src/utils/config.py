"""設定檔載入與合併"""

import yaml
from pathlib import Path


def load_config(path: str = 'config/settings.yaml') -> dict:
    """
    載入 YAML 設定檔，並將 default_strategy 合併到每個 symbol 的 strategy 中
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"設定檔不存在: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    defaults = config.get('default_strategy', {})

    for sym in config.get('symbols', []):
        # 合併：symbol 層級的 strategy 覆蓋 default_strategy
        merged = {**defaults, **sym.get('strategy', {})}
        sym['strategy'] = merged

    return config
