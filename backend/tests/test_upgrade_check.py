from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "upgrade_check.py"
    spec = importlib.util.spec_from_file_location("upgrade_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_check_decodes_non_utf8_merge_tree_output(monkeypatch, tmp_path) -> None:
    module = _module()
    payload = b"changed in both\n  base   100644 abc .gitignore\n  our    100644 def .gitignore\n  their  100644 123 .gitignore\n+<<<<<<< ours\n+\x89PNG\r\n\n+>>>>>>> theirs\n"

    monkeypatch.setattr(
        module,
        "_git",
        lambda *args, **kwargs: module.subprocess.CompletedProcess(
            args, 0, stdout=payload, stderr=b""
        ),
    )

    assert module._merge_tree_conflicts(tmp_path, "base", "ours", "theirs") == [".gitignore"]
