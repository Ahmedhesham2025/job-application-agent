from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _find_project_root() -> Path:
    configured = os.environ.get("JOB_AGENT_HOME")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path.cwd(),
        Path(__file__).resolve().parents[2],
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "config" / "policy.yaml").is_file():
            return candidate.resolve()
    return Path.cwd().resolve()


PROJECT_ROOT = _find_project_root()
DEFAULT_POLICY_PATH = PROJECT_ROOT / "config" / "policy.yaml"
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "config" / "candidate_profile.yaml"


def load_yaml(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    with resolved.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML object in {resolved}")
    return value


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    policy = load_yaml(path)
    for section in ("criteria", "discovery", "submission"):
        if section not in policy:
            raise ValueError(f"Policy is missing required section: {section}")
    return policy


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    profile = load_yaml(path)
    if "candidate_id" not in profile or "facts" not in profile:
        raise ValueError("Candidate profile must include candidate_id and facts")
    source = profile.get("source_document") or {}
    if source.get("path") and not Path(source["path"]).is_absolute():
        # A relative CV path is read from the project folder.
        source["path"] = str((PROJECT_ROOT / source["path"]).resolve())
    return profile
