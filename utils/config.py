from __future__ import annotations

import json
from os import getenv
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = BASE_DIR / "media_content"


def env_int(name: str, default: int = 0) -> int:
    value = getenv(name)
    return int(value) if value not in (None, "") else default


def env_bool(name: str, default: bool = False) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def get_admins() -> set[int]:
    raw = (getenv("ADMINS") or "").strip()
    if not raw:
        return set()
    return {int(item.strip()) for item in raw.split(",") if item.strip().lstrip("-").isdigit()}


def get_json(name: str) -> dict[str, Any]:
    with (DATA_DIR / name).open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(name: str, data: dict[str, Any]) -> None:
    with (DATA_DIR / name).open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def get_rates() -> dict[str, Any]:
    return get_json("paid_plains.json")


def get_regions() -> dict[str, str]:
    return get_json("regions.json")


def get_ad_categories() -> dict[str, str]:
    return get_json("ad_categories.json")


def get_settings_var_names() -> dict[str, str]:
    return {
        "REFERRAL_PERCENT": "Процент от реферала",
        "MIN_WITHDRAWAL_AMOUNT": "Минимальная сумма вывода",
        "DAY_GROUP_AD_LIMIT": "Кол-во рекламы в группе",
        "DAY_AD_USER_LIMIT": "Кол-во рекламы подписчикам",
    }


def active_token_and_group() -> tuple[str, int]:
    token_env = getenv("ACTIVE_VK_TOKEN_ENV", "VK_TEST_TOKEN")
    token = getenv(token_env) or getenv("VK_TEST_TOKEN") or getenv("VK_TOKEN")
    if not token:
        raise RuntimeError("VK token is not configured")

    group_env = "VK_TEST_GROUP_ID" if token_env == "VK_TEST_TOKEN" else "VK_GROUP_ID"
    group_id = env_int(group_env)
    return token, group_id
