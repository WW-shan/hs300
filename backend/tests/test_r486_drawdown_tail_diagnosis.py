from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r486_drawdown_tail_diagnosis_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r486_drawdown_tail_diagnosis.py"


def _module():
    spec = importlib.util.spec_from_file_location("r486_tail", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r486_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R486"
    assert payload["source_selected_label"] == "cap_1"
    assert payload["output_policy"]["active_version"] is None
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r486_drawdown_episode_detection() -> None:
    module = _module()
    curve = [
        {"date": "2024-01-02", "value": 1.0},
        {"date": "2024-01-03", "value": 0.95},
        {"date": "2024-01-04", "value": 0.98},
        {"date": "2024-01-05", "value": 1.02},
        {"date": "2024-01-08", "value": 0.99},
    ]
    episodes = module.drawdown_episodes(curve)
    assert len(episodes) >= 1
    assert all(episode["max_drawdown"] <= 0.0 for episode in episodes)
