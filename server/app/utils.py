import base64
import json
import re
from typing import Iterable


def normalize_tg_s_url(value: str) -> str:
    v = value.strip().lstrip("@")
    if not v:
        raise ValueError("empty channel value")

    if v.startswith("http://") or v.startswith("https://"):
        v = v.replace("http://", "https://").rstrip("/")
        if "/s/" in v:
            return v
        if "t.me/" in v:
            username = v.rsplit("/", 1)[-1]
            return f"https://t.me/s/{username}"

    if "t.me/s/" in v:
        return ("https://" + v if not v.startswith("http") else v).replace("http://", "https://").rstrip("/")

    if "t.me/" in v:
        username = v.rsplit("/", 1)[-1]
        return f"https://t.me/s/{username}"

    return f"https://t.me/s/{v}".rstrip("/")


def parse_csv(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;\n\r،]+", value or "") if x.strip()]


def normalize_proxy_url(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    # already standard
    if "://" in v and "@" in v:
        return v
    # custom: scheme://ip:port:user:pass
    m = re.match(r"^([a-zA-Z0-9]+)://([^:]+):(\d+):([^:]+):(.+)$", v)
    if m:
        scheme, host, port, user, pwd = m.groups()
        return f"{scheme}://{user}:{pwd}@{host}:{port}"
    return v


def normalize_photo_items(items: Iterable[object], limit: int = 12) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if len(out) >= max(1, limit):
            break
        if isinstance(item, dict):
            mime = str(item.get("mime", "") or "").strip().lower()
            payload = str(item.get("b64", "") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            mime = str(item[0] or "").strip().lower()
            payload = str(item[1] or "").strip()
        else:
            continue
        if not payload:
            continue
        try:
            raw = base64.b64decode(payload, validate=True)
        except Exception:
            continue
        if not raw:
            continue
        cleaned_payload = base64.b64encode(raw).decode("ascii")
        cleaned_mime = mime if mime.startswith("image/") else "image/jpeg"
        key = (cleaned_mime, cleaned_payload)
        if key in seen:
            continue
        seen.add(key)
        out.append({"mime": cleaned_mime, "b64": cleaned_payload})
    return out


def serialize_photo_items(items: Iterable[object], limit: int = 12) -> str:
    normalized = normalize_photo_items(items, limit=limit)
    if not normalized:
        return ""
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def deserialize_photo_items(raw: str, fallback_mime: str = "", fallback_b64: str = "", limit: int = 12) -> list[dict[str, str]]:
    items: list[object] = []
    text = (raw or "").strip()
    if text:
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = []
        if isinstance(parsed, list):
            items.extend(parsed)
    if fallback_b64:
        items.insert(0, {"mime": fallback_mime, "b64": fallback_b64})
    return normalize_photo_items(items, limit=limit)


def primary_photo_fields(items: Iterable[object]) -> tuple[str, str]:
    normalized = normalize_photo_items(items, limit=1)
    if not normalized:
        return "", ""
    first = normalized[0]
    return first["mime"], first["b64"]
