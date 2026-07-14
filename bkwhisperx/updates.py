from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from .paths import cache_dir

REPOSITORY = "benkhodabandeh/BK-WhisperX"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases/latest"


@dataclass(slots=True)
class UpdateInfo:
    available: bool
    current_version: str
    latest_version: str = ""
    release_name: str = ""
    release_url: str = RELEASES_URL
    notes: str = ""
    error: str = ""


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+)+)", value)
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    a = _version_tuple(latest)
    b = _version_tuple(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def check_for_update(current_version: str, timeout: int = 4) -> UpdateInfo:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"BK-WhisperX/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Update response exceeded the 1 MiB safety limit.")
        payload: dict[str, Any] = json.loads(raw)
        latest = str(payload.get("tag_name", ""))
        return UpdateInfo(
            available=is_newer(latest, current_version),
            current_version=current_version,
            latest_version=latest.lstrip("vV"),
            release_name=str(payload.get("name", latest)),
            release_url=str(payload.get("html_url", RELEASES_URL)),
            notes=str(payload.get("body", ""))[:2000],
        )
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return UpdateInfo(available=False, current_version=current_version, error=str(exc))


def check_for_update_cached(
    current_version: str,
    *,
    max_age_hours: float = 24.0,
    force: bool = False,
) -> UpdateInfo:
    path = cache_dir() / "update-check.json"
    if not force:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - float(cached["checked_at"])
            if age <= max(0.0, max_age_hours) * 3600:
                data = dict(cached["result"])
                data["current_version"] = current_version
                data["available"] = is_newer(str(data.get("latest_version", "")), current_version)
                return UpdateInfo(**data)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    result = check_for_update(current_version)
    if not result.error:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps({"checked_at": time.time(), "result": asdict(result)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return result
