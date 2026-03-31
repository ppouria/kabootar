from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

_DEFAULT_APP_NAME = "Kabootar"
_DEFAULT_VERSION_NAME = "0.6.1"
_DEFAULT_VERSION_CODE = 8
_DEFAULT_RELEASE_CHANNEL = "stable"


@dataclass(frozen=True)
class AppMeta:
    app_name: str
    version_name: str
    version_code: int
    release_channel: str
    source_path: str

    @property
    def display_version(self) -> str:
        return f"{self.version_name} ({self.version_code})"

    def as_dict(self, include_source: bool = False) -> dict[str, object]:
        payload = asdict(self)
        if not include_source:
            payload.pop("source_path", None)
        payload["display_version"] = self.display_version
        return payload


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    mei = getattr(sys, "_MEIPASS", "")
    if mei:
        paths.append(Path(str(mei)) / "version.properties")

    here = Path(__file__).resolve()
    paths.extend(
        [
            here.parent.parent / "version.properties",
            here.parents[2] / "version.properties",
            Path.cwd() / "version.properties",
        ]
    )

    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _load_properties() -> tuple[dict[str, str], str]:
    for path in _candidate_paths():
        try:
            if not path.is_file():
                continue
            data: dict[str, str] = {}
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
            if data:
                return data, str(path)
        except Exception:
            continue
    return {}, ""


@lru_cache(maxsize=1)
def app_meta() -> AppMeta:
    env_app_name = (os.getenv("KABOOTAR_APP_NAME", "") or "").strip()
    env_version_name = (os.getenv("KABOOTAR_VERSION_NAME", "") or "").strip()
    env_version_code_raw = (os.getenv("KABOOTAR_VERSION_CODE", "") or "").strip()
    env_release_channel = (os.getenv("KABOOTAR_RELEASE_CHANNEL", "") or "").strip()
    if env_app_name or env_version_name or env_version_code_raw or env_release_channel:
        try:
            env_version_code = int(env_version_code_raw or _DEFAULT_VERSION_CODE)
        except Exception:
            env_version_code = _DEFAULT_VERSION_CODE
        return AppMeta(
            app_name=env_app_name or _DEFAULT_APP_NAME,
            version_name=env_version_name or _DEFAULT_VERSION_NAME,
            version_code=max(1, env_version_code),
            release_channel=env_release_channel or _DEFAULT_RELEASE_CHANNEL,
            source_path="env",
        )

    values, source_path = _load_properties()
    try:
        version_code = int((values.get("version_code") or "").strip())
    except Exception:
        version_code = _DEFAULT_VERSION_CODE
    return AppMeta(
        app_name=(values.get("app_name") or _DEFAULT_APP_NAME).strip() or _DEFAULT_APP_NAME,
        version_name=(values.get("version_name") or _DEFAULT_VERSION_NAME).strip() or _DEFAULT_VERSION_NAME,
        version_code=max(1, version_code),
        release_channel=(values.get("release_channel") or _DEFAULT_RELEASE_CHANNEL).strip() or _DEFAULT_RELEASE_CHANNEL,
        source_path=source_path,
    )
