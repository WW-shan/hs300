from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.factor_research.fingerprints import canonical_json, content_hash, sha256_json, utc_now
from app.factor_research.models import FactorResearchModel


def test_canonical_json_is_stable_for_key_order() -> None:
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_content_hash_ignores_excluded_fields() -> None:
    payload = {"a": 1, "b": 2, "content_sha256": "old"}
    assert content_hash(payload, "content_sha256") == sha256_json({"a": 1, "b": 2})


def test_utc_now_has_utc_contract() -> None:
    value = utc_now()
    assert value.endswith("Z")
    assert "T" in value


def test_models_are_frozen_and_reject_extra_fields() -> None:
    class Demo(FactorResearchModel):
        value: int

    item = Demo(value=1)
    assert item.value == 1
    with pytest.raises(ValidationError):
        Demo(value=1, unexpected=True)
    with pytest.raises(ValidationError):
        item.value = 2
