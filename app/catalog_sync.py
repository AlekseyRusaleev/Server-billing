from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


logger = logging.getLogger(__name__)

CACHE_DIR = Path(settings.database_path).resolve().parent / "catalog_cache"
BUNDLE_CACHE = CACHE_DIR / "provider_bundle.json"
META_CACHE = CACHE_DIR / "catalog_meta.json"


def _cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def catalog_sync_status() -> dict[str, str]:
    if not META_CACHE.exists():
        return {"updated_at": "", "source": "bundled", "message": ""}
    with META_CACHE.open(encoding="utf-8") as file:
        payload = json.load(file)
    return {
        "updated_at": str(payload.get("updated_at", "")),
        "source": str(payload.get("source", "")),
        "message": str(payload.get("message", "")),
    }


def sync_provider_catalog() -> tuple[bool, str]:
    url = (settings.provider_catalog_url or "").strip()
    if not url:
        return False, "Catalog URL is not configured."

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "server-billing-manager/catalog-sync"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        message = f"Catalog sync failed: {error}"
        logger.warning(message)
        return False, message

    if not isinstance(payload, dict):
        return False, "Catalog bundle must be a JSON object."

    _cache_dir()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    bundle = {
        "updated_at": str(payload.get("updated_at") or now),
        "notice": str(payload.get("notice", "")),
        "promos": list(payload.get("promos") or []),
        "providers": list(payload.get("providers") or []),
        "plans_by_domain": dict(payload.get("plans_by_domain") or {}),
    }
    with BUNDLE_CACHE.open("w", encoding="utf-8") as file:
        json.dump(bundle, file, ensure_ascii=False, indent=2)
    with META_CACHE.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "updated_at": bundle["updated_at"],
                "source": "remote",
                "message": "Catalog updated successfully.",
                "synced_at": now,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )
    from app.provider_templates import clear_provider_catalog_cache

    clear_provider_catalog_cache()
    logger.info("Provider catalog synced from %s", url)
    return True, "Catalog updated successfully."
