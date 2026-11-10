"""Stable canonical serialization and content fingerprints."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON text.

    The representation is intentionally independent of dictionary insertion
    order and rejects NaN/Infinity so a fingerprint cannot silently change
    meaning across platforms.
    """
    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def content_hash(payload: Mapping[str, Any], *excluded: str) -> str:
    """Hash a mapping while excluding fields that contain their own hash."""
    clean = {key: value for key, value in payload.items() if key not in set(excluded)}
    return sha256_json(clean)


def hash_token(value: str) -> str:
    """Return the filesystem-safe hexadecimal part of a sha256 token."""
    return value.removeprefix("sha256:")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
