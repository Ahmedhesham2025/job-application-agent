"""Assist mode: fill a real application form in a visible browser, then let the
human solve the CAPTCHA and press Submit.

The agent never clicks Submit. Every job site in the pipeline (Greenhouse,
Ashby, Lever) protects its form with a CAPTCHA, so a human click is required.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import ApplicationStore
from .learning import SNAPSHOT_JS, learn_from_form, reduce_snapshot
from .form_js import EXTRACT_FIELDS_JS, PAGE_SIGNALS_JS, banner_js, mark_todo_js
from .models import ApplicationPlan, FormAnswer, FormDraft, JobPosting
from .policy import evaluate_application, hostname_allowed, idempotency_key, normalized_scalar

NEVER_ANSWER = {
    "criminal_history",
    "security_clearance",
    "assessment",
    "recorded_interview",
    "legal_attestation",
    "unknown",
}
DECLINE_WORDS = ("prefer not", "decline", "don't wish", "do not wish", "not to say", "not to disclose", "rather not")
YES_WORDS = ("yes", "true", "i do", "i am", "i will", "i would", "checked")
NO_WORDS = ("no", "false", "i do not", "i don't", "i am not", "i'm not", "unchecked")


# ---------------------------------------------------------------- deterministic checks

def _starts(text: str, words: tuple[str, ...]) -> bool:
    return any(text == w or re.match(re.escape(w) + r"\b", text) for w in words)


def _means(option: str, flag: bool) -> bool:
    """True when the option text says yes (flag=True) or no (flag=False)."""
    text = normalized_scalar(option)
    if _starts(text, NO_WORDS):
        return not flag
    if _starts(text, YES_WORDS):
        return flag
    return False


def consistent_with_fact(value: Any, fact_value: Any) -> bool:
    """Does the chosen answer say the same thing as the stored fact?"""
    if isinstance(value, list):
        return all(consistent_with_fact(v, fact_value) for v in value)
    if isinstance(fact_value, bool):
        if isinstance(value, bool):
            return value == fact_value
        return _means(str(value), fact_value)
    a, b = normalized_scalar(value), normalized_scalar(fact_value)
    if not a or not b:
        return False
    # Same degree level ("Bachelor's Degree" vs "Bachelor of Engineering ...").
    for level in ("bachelor", "master", "phd", "doctor", "diploma"):
        if level in a and level in b:
            return True
    # Same duration ("1 Month" vs "30 days", "2 weeks" vs "14 days").
    da, db = _days(a), _days(b)
    if da is not None and db is not None:
        return abs(da - db) <= 3
    if any(w in b for w in DECLINE_WORDS):
        # Any "prefer not to say / decline" option matches a decline fact.
        return any(w in a for w in DECLINE_WORDS)
    if a == b or b in a or a in b:
        return True
    digits_a, digits_b = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    return bool(digits_a) and digits_a == digits_b


def _days(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(day|week|month)", text)
    if not m:
        m2 = re.fullmatch(r"(one|a|two|three) (day|week|month)s?.*", text)
        if not m2:
            return None
        n = {"one": 1, "a": 1, "two": 2, "three": 3}[m2.group(1)]
        unit = m2.group(2)
    else:
        n, unit = int(m.group(1)), m.group(2)
    return n * {"day": 1, "week": 7, "month": 30}[unit]


@dataclass
class Review:
    fill: list[FormAnswer] = field(default_factory=list)
    todo: list[dict[str, Any]] = field(default_factory=list)
    fact_answers: list[FormAnswer] = field(default_factory=list)


def review_answers(
    fields: list[dict[str, Any]], draft: FormDraft, profile: dict[str, Any]
) -> Review:
    """Keep only answers that pass the rules; everything else goes to the human."""
    facts = profile.get("facts", {})
    by_key = {f["key"]: f for f in fields}
    answered: dict[str, FormAnswer] = {}
    review = Review()

    for answer in draft.answers:
        f = by_key.get(answer.key)
        if f is None or answer.key in answered:
            continue
        problem = None
        if f["kind"] == "file" and answer.value in ("RESUME", "COVER_LETTER"):
            answered[answer.key] = answer
            review.fill.append(answer)
            continue
        if answer.basis == "skip" or answer.value in (None, "", []):
            problem = answer.note or "agent could not answer"
        elif answer.category.value in NEVER_ANSWER:
            problem = f"{answer.category.value} questions are always left to you"
        elif answer.basis == "fact":
            fact = facts.get(answer.fact_id or "")
            if not fact or not fact.get("verified", False):
                problem = f"no verified fact '{answer.fact_id}'"
            elif f["kind"] not in {"file"} and not consistent_with_fact(answer.value, fact.get("value")):
                problem = f"answer '{answer.value}' does not match fact {answer.fact_id}"
            elif answer.category.value in {"demographic", "disability", "veteran_status"} and not any(
                w in normalized_scalar(answer.value) for w in DECLINE_WORDS
            ):
                problem = "personal questions are only answered with 'prefer not to say'"
        elif answer.basis == "learned":
            learned = {a["id"]: a for a in profile.get("learned_answers", [])}
            entry = learned.get(answer.fact_id or "")
            if entry is None:
                problem = f"no learned answer '{answer.fact_id}'"
            elif answer.category.value in NEVER_ANSWER:
                problem = f"{answer.category.value} questions are always left to you"
            else:
                want = entry["answer"]
                same = (
                    sorted(map(normalized_scalar, want)) == sorted(map(normalized_scalar, answer.value))
                    if isinstance(want, list) and isinstance(answer.value, list)
                    else normalized_scalar(want) == normalized_scalar(answer.value)
                    or consistent_with_fact(answer.value, want)
                )
                if not same:
                    problem = f"answer '{answer.value}' differs from what you answered before ('{want}')"
        elif answer.basis == "open_answer":
            if answer.category.value != "open_answer":
                problem = "open answers must use the open_answer category"
        else:
            problem = f"unknown basis '{answer.basis}'"

        options = f.get("options") or []
        if not problem and options and f["kind"] in {"select", "radio", "yesno_buttons", "checkbox_group"}:
            chosen = answer.value if isinstance(answer.value, list) else [answer.value]
            normal = {normalized_scalar(o) for o in options}
            if any(normalized_scalar(c) not in normal for c in chosen):
                problem = f"answer '{answer.value}' is not one of the options"

        if problem:
            review.todo.append({"key": f["key"], "label": f.get("label", ""), "required": f.get("required", False), "why": problem})
        else:
            answered[answer.key] = answer
            review.fill.append(answer)
            if answer.basis == "fact" and f["kind"] != "file":
                review.fact_answers.append(answer)

    for f in fields:
        if f["key"] not in answered and not any(t["key"] == f["key"] for t in review.todo):
            if f.get("required"):
                review.todo.append({"key": f["key"], "label": f.get("label", ""), "required": True, "why": "no answer given"})
    return review


def plan_from_review(
    job: JobPosting, match_score: int, profile: dict[str, Any], review: Review, cover_letter: str
) -> ApplicationPlan:
    """Fact answers go through the original policy gate (fact must match exactly)."""
    facts = profile["facts"]
    answers = []
    for a in review.fact_answers:
        answers.append(
            {
                "field_name": a.key,
                "question": a.key,
                "value": facts[a.fact_id]["value"],
                "fact_id": a.fact_id,
                "category": a.category.value,
            }
        )
    source = profile["source_document"]
    return ApplicationPlan.model_validate(
        {
            "candidate_id": profile["candidate_id"],
            "source_job_id": job.source_job_id,
            "company": job.company,
            "title": job.title,
            "application_url": str(job.application_url),
            "match_score": match_score,
            "resume_path": source["path"],
            "resume_sha256": source["sha256"],
            "cover_letter": cover_letter,
            "answers": answers,
        }
    )


# ---------------------------------------------------------------- browser work

async def _open_combobox_options(page, key: str) -> list[str]:
    box = page.locator(f'[data-ja-key="{key}"]').first
    try:
        await box.click(timeout=3000)
        await page.wait_for_timeout(500)
        texts = await page.locator('[role="option"]:visible').all_inner_texts()
        await page.keyboard.press("Escape")
        return [t.strip() for t in texts if t.strip()][:60]
    except Exception:
        return []


async def collect_fields(page) -> list[dict[str, Any]]:
    fields = await page.evaluate(EXTRACT_FIELDS_JS)
    for f in fields:
        if f["kind"] == "combobox":
            f["options"] = await _open_combobox_options(page, f["key"])
    return fields


async def _fill_one(page, f: dict[str, Any], answer: FormAnswer, resume: Path, cover: Path | None) -> bool:
    page.set_default_timeout(5000)
    loc = page.locator(f'[data-ja-key="{f["key"]}"]')
    kind = f["kind"]
    value = answer.value
    try:
        if kind == "file":
            target = resume if value == "RESUME" else cover if value == "COVER_LETTER" else None
            if target is None:
                return False
            await loc.first.set_input_files(str(target))
        elif kind in {"text", "email", "tel", "url", "number", "textarea", "search"}:
            text = str(value)
            if kind == "number":
                text = re.sub(r"[^\d.]", "", text)
            await loc.first.fill(text)
        elif kind == "select":
            await loc.first.select_option(label=_match_option(str(value), f.get("options", [])))
        elif kind == "combobox":
            choice = _match_option(str(value), f.get("options", [])) if f.get("options") else str(value)
            await loc.first.click()
            await loc.first.fill(choice)
            await page.wait_for_timeout(600)
            option = page.locator('[role="option"]:visible', has_text=re.compile(r"^\s*" + re.escape(choice) + r"\s*$")).first
            if not await option.count():
                # Location boxes list longer names ("Cairo, Cairo Governorate, Egypt").
                first_word = re.split(r"[ ,]+", choice.strip())[0]
                option = page.locator('[role="option"]:visible', has_text=first_word).first
            if await option.count():
                await option.click()
            else:
                await page.keyboard.press("Enter")
        elif kind in {"radio", "yesno_buttons"}:
            choice = _match_option(str(value), f.get("options", []))
            target = page.locator(f'[data-ja-key="{f["key"]}"][data-ja-option="{_css(choice)}"]').first
            if kind == "radio":
                await target.check(force=True)
            else:
                await target.click()
        elif kind == "checkbox_group":
            for item in value if isinstance(value, list) else [value]:
                choice = _match_option(str(item), f.get("options", []))
                await page.locator(f'[data-ja-key="{f["key"]}"][data-ja-option="{_css(choice)}"]').first.check(force=True)
        elif kind == "checkbox_single":
            want = value is True or _means(str(value), True)
            if want:
                await loc.first.check(force=True)
            else:
                await loc.first.uncheck(force=True)
        else:
            return False
        return True
    except Exception:
        return False


def _css(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _match_option(value: str, options: list[str]) -> str:
    want = normalized_scalar(value)
    for o in options:
        if normalized_scalar(o) == want:
            return o
    for o in options:
        if want and want in normalized_scalar(o):
            return o
    return value


async def write_cover_letter_pdf(pw, text: str, path: Path, profile: dict[str, Any]) -> Path:
    facts = profile["facts"]
    name = facts["full_name"]["value"]
    contact = f'{facts["email"]["value"]} | {facts["phone"]["value"]} | {facts["linkedin_url"]["value"]}'
    paragraphs = "".join(f"<p>{p.strip()}</p>" for p in text.split("\n") if p.strip())
    html = (
        "<html><body style='font:11pt Arial;margin:40px;line-height:1.45'>"
        f"<h2 style='margin:0'>{name}</h2><div style='color:#555'>{contact}</div><br>{paragraphs}</body></html>"
    )
    # PDF printing only works in a headless browser, so use a separate one.
    browser = await pw.chromium.launch(headless=True)
    try:
        page = await browser.new_page()
        await page.set_content(html)
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.pdf(path=str(path), format="A4")
    finally:
        await browser.close()
    return path


@dataclass
class AssistResult:
    status: str
    detail: dict[str, Any]


async def assist_application(
    job: JobPosting,
    match_score: int,
    policy: dict[str, Any],
    profile: dict[str, Any],
    store: ApplicationStore,
    artifacts: Path,
    browser_profile: Path,
    drafter,
    wait_minutes: int = 30,
    headless: bool = False,
    ask_human=None,
    prepare_context=None,
    learned_path: Path | None = None,
) -> AssistResult:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Run: pip install playwright  then  python -m playwright install chromium") from exc

    submission = policy["submission"]
    allowed = submission.get("allowed_domains", [])
    if not hostname_allowed(str(job.application_url), allowed):
        return AssistResult("POLICY_BLOCKED", {"reasons": ["domain_not_allowlisted"]})

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", f"{job.company}-{job.source_job_id}".lower())[:60]
    out = artifacts / "assist" / f"{stamp}-{slug}"
    out.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        launch = dict(user_data_dir=str(browser_profile), headless=headless, viewport={"width": 1280, "height": 900})
        context = None
        for attempt in range(4):
            # The previous Chrome window may still be closing and holding the profile.
            for channel in ("chrome", None):
                try:
                    extra = {"channel": channel} if channel else {}
                    context = await pw.chromium.launch_persistent_context(**extra, **launch)
                    break
                except Exception:
                    context = None
            if context is not None:
                break
            await asyncio.sleep(3)
        if context is None:
            raise RuntimeError("Could not start Chrome. Close all agent Chrome windows and try again.")
        if prepare_context is not None:
            await prepare_context(context)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(str(job.application_url), wait_until="domcontentloaded")
        form_selector = "input:not([type=hidden]), textarea, select"
        try:
            await page.wait_for_selector(form_selector, timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        signals = await page.evaluate(PAGE_SIGNALS_JS)
        if not hostname_allowed(signals["url"], allowed):
            await context.close()
            return AssistResult("REVIEW_REQUIRED", {"review_triggers": ["external_redirect"], "url": signals["url"]})
        if signals["closed_posting"]:
            await context.close()
            return AssistResult("JOB_CLOSED", {"url": signals["url"]})
        if signals["login_wall"]:
            await context.close()
            return AssistResult("REVIEW_REQUIRED", {"review_triggers": ["login_challenge"]})

        # Greenhouse shows the form under an "Apply" button on the job page.
        if not await page.locator("form input, form textarea").count():
            apply = page.get_by_role("button", name=re.compile(r"^apply", re.I)).first
            if await apply.count():
                await apply.click()
                await page.wait_for_timeout(1500)
        # Ashby job pages keep the form on a separate "Application" tab.
        if not await page.locator(form_selector).count():
            tab = page.get_by_role("tab", name=re.compile(r"application", re.I)).first
            link = page.get_by_role("link", name=re.compile(r"^(apply|application)", re.I)).first
            for target in (tab, link):
                if await target.count():
                    await target.click()
                    try:
                        await page.wait_for_selector(form_selector, timeout=10000)
                    except Exception:
                        pass
                    break
        if not await page.locator(form_selector).count() and "ashbyhq.com" in page.url and not page.url.rstrip("/").endswith("/application"):
            await page.goto(page.url.split("?")[0].rstrip("/") + "/application", wait_until="domcontentloaded")
            try:
                await page.wait_for_selector(form_selector, timeout=15000)
            except Exception:
                pass

        fields = await collect_fields(page)
        (out / "fields.json").write_text(json.dumps(fields, indent=2, ensure_ascii=False), encoding="utf-8")
        if not fields:
            await context.close()
            return AssistResult("REVIEW_REQUIRED", {"review_triggers": ["selector_changed"], "note": "no form fields found"})

        draft = await drafter(job, fields, profile)
        review = review_answers(fields, draft, profile)
        plan = plan_from_review(job, match_score, profile, review, draft.cover_letter)
        verdict = evaluate_application(plan, policy, profile, store)
        (out / "draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
        (out / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        if not verdict.allowed:
            await context.close()
            return AssistResult("POLICY_BLOCKED", {"reasons": verdict.reasons, "artifacts": str(out)})

        key = idempotency_key(plan)
        if not store.reserve(key, plan.candidate_id, plan.source_job_id, plan.company):
            await context.close()
            return AssistResult("POLICY_BLOCKED", {"reasons": ["duplicate_application"]})

        try:
            cover = await write_cover_letter_pdf(pw, draft.cover_letter, out / "cover_letter.pdf", profile)
            (out / "cover_letter.txt").write_text(draft.cover_letter, encoding="utf-8")
            resume = Path(profile["source_document"]["path"])

            by_key = {f["key"]: f for f in fields}
            filled = 0
            for answer in review.fill:
                if await _fill_one(page, by_key[answer.key], answer, resume, cover):
                    filled += 1
                else:
                    review.todo.append({"key": answer.key, "label": by_key[answer.key].get("label", ""), "required": by_key[answer.key].get("required", False), "why": "could not type the answer"})

            todo_labels = [f'{t["label"] or t["key"]}{" (required)" if t["required"] else ""}' for t in review.todo]
            try:
                await page.screenshot(path=str(out / "1-filled.png"), full_page=True)
                await page.evaluate(mark_todo_js([t["key"] for t in review.todo]))
                await page.evaluate(banner_js(filled, todo_labels))
            except Exception:
                pass  # the window may already be closed; the wait loop handles it

            summary = {
                "company": job.company,
                "title": job.title,
                "url": str(job.application_url),
                "filled": filled,
                "needs_you": review.todo,
                "artifacts": str(out),
            }
            (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\n>>> {job.company} — {job.title}")
            print(f"    Filled {filled} fields. Needs you: {len(review.todo)}")
            for label in todo_labels:
                print(f"      - {label}")
            print("    Check the form, solve the CAPTCHA, press Submit. Close the window when done.")
        except Exception:
            store.release(key)
            await context.close()
            raise

        agent_values: dict[str, Any] = {}
        try:
            agent_values = reduce_snapshot(await page.evaluate(SNAPSHOT_JS), fields)
        except Exception:
            pass
        final_values: dict[str, Any] = dict(agent_values)

        status = "ABANDONED"
        deadline = asyncio.get_running_loop().time() + wait_minutes * 60
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            if page.is_closed():
                break
            try:
                s = await page.evaluate(PAGE_SIGNALS_JS)
                if not s["confirmation"]:
                    # Keep the latest answers you typed, to learn from after Submit.
                    snap = reduce_snapshot(await page.evaluate(SNAPSHOT_JS), fields)
                    if snap:
                        final_values = snap
            except Exception:
                continue
            if s["confirmation"]:
                status = "SUBMITTED"
                await page.screenshot(path=str(out / "2-confirmation.png"), full_page=True)
                await page.wait_for_timeout(1500)
                break
        try:
            await context.close()
        except Exception:
            pass
    if status == "ABANDONED" and ask_human is not None:
        if await ask_human(f"Did you submit the {job.company} application? (y/n): "):
            status = "SUBMITTED"
        elif await ask_human(f"Skip this {job.company} job forever? (y/n): "):
            status = "SKIPPED"

    learned_now = []
    if status == "SUBMITTED" and learned_path is not None:
        try:
            learned_now = learn_from_form(learned_path, fields, final_values, agent_values, job.company)
        except Exception:
            learned_now = []
        for entry in learned_now:
            print(f"    Learned: {entry['question'][:70]} -> {entry['answer']}")

    receipt = {
        "status": status,
        "source_job_id": job.source_job_id,
        "company": job.company,
        "application_url": str(job.application_url),
        "idempotency_key": key,
        "submitted_at": datetime.now(UTC).isoformat() if status == "SUBMITTED" else None,
        "confirmation": "Confirmation page seen" if status == "SUBMITTED" else "No confirmation seen",
        "artifacts": [str(out)],
    }
    if status in {"SUBMITTED", "SKIPPED"}:
        store.record_receipt(key, receipt)
    else:
        store.release(key)
    store.audit("ASSIST_FINISHED", receipt)
    return AssistResult(status, {**summary, "receipt": receipt, "learned": learned_now})
