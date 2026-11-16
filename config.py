"""配置读写：统一放在 data/config.json，首次启动自动生成默认配置。"""

import json
import os
import copy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULTS = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
    },
    "ai": {
        "enabled": True,
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "",
        "model": "doubao-seed-1-6-250615",
        "batch_size": 6,
        "timeout": 90,
    },
    "schedule": {
        "fetch_minutes": 30,
        "auto_process": True,
        "process_limit": 40,
        "digest_time": "09:00",
    },
    "collect": {
        "max_items_per_feed": 30,
        "keep_days": 0,
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULTS)
        return copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
    except Exception:
        user_cfg = {}
    return _merge(DEFAULTS, user_cfg)


def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    merged = _merge(DEFAULTS, cfg)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    return merged


def public_config(cfg):
    """给前端返回配置时隐藏密钥明文。"""
    out = copy.deepcopy(cfg)
    key = out.get("ai", {}).get("api_key", "")
    out["ai"]["api_key_set"] = bool(key)
    out["ai"]["api_key"] = ""
    return out
