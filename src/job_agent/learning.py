"""Learn answers the human gives on application forms.

When you fill a question the agent could not answer and then submit, the
answer is saved in config/learned_answers.yaml. Next time a form asks the same
kind of question, the agent may reuse it (you still see it before Submit).
Company-specific and sensitive questions are never saved.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SNAPSHOT_JS = r"""
() => [...document.querySelectorAll('[data-ja-key]')].map(el => ({
  k: el.getAttribute('data-ja-key'),
  tag: el.tagName,
  type: el.type || '',
  opt: el.getAttribute('data-ja-option'),
  checked: !!el.checked,
  pressed: el.getAttribute('aria-pressed') === 'true' || el.hasAttribute('data-on') || /selected|active|_active/i.test(el.className || ''),
  value: el.tagName === 'SELECT' ? (el.selectedOptions[0] ? el.selectedOptions[0].text : '') : (el.type === 'file' ? (el.files && el.files.length ? 'FILE' : '') : (el.value || '')),
}))
"""

# Never learn these: they are personal, legal, or different for every company.
NEVER_LEARN = re.compile(
    r"convict|criminal|offen[cs]e|clearance|disab|veteran|gender|race|ethnic|pronoun|sexual|religio|"
    r"why do you|why are you|why would|what attracted|motivat|cover letter|tell us about|describe|"
    r"referr|who referred|password|signature|social security|national id|passport",
    re.I,
)


def reduce_snapshot(raw: list[dict[str, Any]], fields: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = {f["key"]: f["kind"] for f in fields}
    values: dict[str, Any] = {}
    for item in raw:
        key, kind = item["k"], kinds.get(item["k"])
        if kind is None:
            continue
        if kind == "yesno_buttons":
            if item["pressed"]:
                values[key] = item["opt"]
        elif kind == "radio":
            if item["checked"]:
                values[key] = item["opt"]
        elif kind == "checkbox_group":
            if item["checked"]:
                values.setdefault(key, []).append(item["opt"])
        elif kind == "checkbox_single":
            values[key] = "checked" if item["checked"] else "unchecked"
        else:
            values[key] = item["value"]
    return values


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("*", "")).strip()


def load_learned(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("answers", []))


def save_learned(path: Path, answers: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Answers you typed on real application forms. The agent reuses them on similar questions.\n"
        "# Edit or delete any line. Personal, legal and company-specific questions are never saved.\n"
    )
    path.write_text(header + yaml.safe_dump({"answers": answers}, sort_keys=False, allow_unicode=True), encoding="utf-8")


def learn_from_form(
    path: Path,
    fields: list[dict[str, Any]],
    final_values: dict[str, Any],
    agent_values: dict[str, Any],
    company: str,
) -> list[dict[str, Any]]:
    """Save answers the human typed or changed. Returns the new/updated entries."""
    learned = load_learned(path)
    index = {_norm(a["question"]): a for a in learned}
    changed = []
    company_word = _norm(company).split(" ")[0] if company else ""
    for f in fields:
        key, label, kind = f["key"], f.get("label", ""), f["kind"]
        value = final_values.get(key)
        if kind == "file" or value in (None, "", [], "unchecked"):
            continue
        if agent_values.get(key) == value:
            continue  # the agent already knew this
        if not label or NEVER_LEARN.search(label) or kind == "textarea":
            continue
        if company_word and len(company_word) > 2 and company_word in _norm(label).split(" "):
            continue  # "Have you worked at <company>?" style questions
        if isinstance(value, str) and len(value) > 200:
            continue
        entry = {
            "id": f"L{len(learned) + 1}",
            "question": label,
            "answer": value,
            "options_seen": f.get("options", [])[:20],
            "learned_from": company,
            "updated": datetime.now(UTC).strftime("%Y-%m-%d"),
        }
        old = index.get(_norm(label))
        if old:
            entry["id"] = old["id"]
            old.update(entry)
            changed.append(old)
        else:
            learned.append(entry)
            index[_norm(label)] = entry
            changed.append(entry)
    if changed:
        save_learned(path, learned)
    return changed
