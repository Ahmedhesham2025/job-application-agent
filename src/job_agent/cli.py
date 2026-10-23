from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters import DryRunAdapter
from .agents import provider_key_present, selected_model, selected_provider
from .config import PROJECT_ROOT, load_policy, load_profile
from .database import ApplicationStore
from .matching import deterministic_prefilter
from .models import ApplicationPlan, JobPosting
from .policy import evaluate_application, file_sha256
from .sources import AshbySource, GreenhouseSource, LeverSource
from .workflow import process_job, verified_candidate_view

QUEUE_PATH = PROJECT_ROOT / "data" / "apply_queue.json"


def _json_write(path: str | Path, payload: Any) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return output.resolve()


def doctor(_: argparse.Namespace) -> int:
    policy = load_policy()
    profile = load_profile()
    source = profile["source_document"]
    resume_path = Path(source["path"])
    expected_hash = str(source["sha256"]).casefold()
    actual_hash = file_sha256(resume_path) if resume_path.is_file() else None
    unverified = sorted(
        key
        for key, fact in profile["facts"].items()
        if not fact.get("verified", False)
    )
    report = {
        "candidate_id": profile["candidate_id"],
        "resume_exists": resume_path.is_file(),
        "resume_hash_matches": actual_hash == expected_hash,
        "submission_enabled": policy["submission"]["enabled"],
        "submission_mode": policy["submission"]["mode"],
        "ai_provider": selected_provider(),
        "ai_model": selected_model(),
        "provider_api_key_present": provider_key_present(),
        "unverified_facts_that_require_user_input": unverified,
        "assumed_facts_please_check": sorted(
            key for key, fact in profile["facts"].items() if fact.get("assumed")
        ),
        "resume_path": str(resume_path),
    }
    print(json.dumps(report, indent=2))
    return 0 if report["resume_exists"] and report["resume_hash_matches"] else 1


async def _fetch() -> list[JobPosting]:
    policy = load_policy()
    sources = []
    for specification in policy["discovery"].get("sources", []):
        if not specification.get("enabled", False):
            continue
        if specification["type"] == "greenhouse":
            sources.append(GreenhouseSource(specification["token"]))
        elif specification["type"] == "lever":
            sources.append(LeverSource(specification["site"]))
        elif specification["type"] == "ashby":
            sources.append(AshbySource(specification["board"]))
        else:
            raise ValueError(f"Unsupported source type: {specification['type']}")

    batches = await asyncio.gather(*(source.fetch() for source in sources))
    deduplicated: dict[tuple[str, str], JobPosting] = {}
    for batch in batches:
        for job in batch:
            deduplicated[(job.source, job.source_job_id)] = job
    return list(deduplicated.values())


def fetch(args: argparse.Namespace) -> int:
    jobs = asyncio.run(_fetch())
    path = _json_write(
        args.output,
        [job.model_dump(mode="json") for job in jobs],
    )
    print(json.dumps({"jobs": len(jobs), "output": str(path)}, indent=2))
    return 0


def prefilter(args: argparse.Namespace) -> int:
    policy = load_policy()
    payload = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    jobs = [JobPosting.model_validate(item) for item in payload]
    matched, rejected = deterministic_prefilter(jobs, policy)
    result = {
        "matched": [job.model_dump(mode="json") for job in matched],
        "rejected": rejected,
    }
    path = _json_write(args.output, result)
    print(
        json.dumps(
            {"matched": len(matched), "rejected": len(rejected), "output": str(path)},
            indent=2,
        )
    )
    return 0


def evaluate(args: argparse.Namespace) -> int:
    policy = load_policy()
    profile = load_profile()
    payload = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan = ApplicationPlan.model_validate(payload)
    verdict = evaluate_application(
        plan, policy, profile, ApplicationStore(":memory:")
    )
    print(json.dumps(verdict.model_dump(mode="json"), indent=2))
    return 0 if verdict.allowed else 2


async def _plan(args: argparse.Namespace) -> dict[str, Any]:
    if not provider_key_present():
        provider = selected_provider()
        variable = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        raise RuntimeError(f"{variable} is required for the two AI agents")
    policy = load_policy()
    profile = load_profile()
    job_payload = json.loads(Path(args.job).read_text(encoding="utf-8"))
    if isinstance(job_payload, dict) and "matched" in job_payload:
        job_payload = job_payload["matched"]
    if isinstance(job_payload, list):
        if not args.job_id:
            raise RuntimeError("--job-id is required when the input contains multiple jobs")
        candidates = [
            item for item in job_payload if str(item.get("source_job_id")) == args.job_id
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one job with source_job_id={args.job_id}")
        job_payload = candidates[0]
    job = JobPosting.model_validate(job_payload)
    database_path = PROJECT_ROOT / "data" / "job_agent.sqlite3"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = ApplicationStore(database_path)
    adapter = DryRunAdapter(
        PROJECT_ROOT / "artifacts",
        inspect_remote_forms=True,
    )
    return await process_job(job, policy, profile, store, adapter)


def plan(args: argparse.Namespace) -> int:
    result = asyncio.run(_plan(args))
    path = _json_write(args.output, result)
    print(json.dumps({"status": result["status"], "output": str(path)}, indent=2))
    return 0


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from .agents import qualify_job

    if not provider_key_present():
        raise RuntimeError("An AI API key is required to score jobs (see .env)")
    policy = load_policy()
    profile = load_profile()
    resume_hash = profile["source_document"]["sha256"]
    store = ApplicationStore(PROJECT_ROOT / "data" / "job_agent.sqlite3")

    jobs = await _fetch()
    _json_write(PROJECT_ROOT / "data" / "fetched_jobs.json", [j.model_dump(mode="json") for j in jobs])
    matched, rejected = deterministic_prefilter(jobs, policy)
    submitted = store.submitted_job_ids()
    view = verified_candidate_view(profile)

    scored = []
    for job in matched:
        if job.source_job_id in submitted:
            continue
        decision = store.get_score(job.source, job.source_job_id, resume_hash)
        if decision is None:
            try:
                result = await qualify_job(job, policy["criteria"], view)
            except Exception as exc:  # keep going if one job fails
                print(f"  ! could not score {job.company} {job.title}: {exc}")
                continue
            decision = result.model_dump(mode="json")
            store.save_score(job.source, job.source_job_id, resume_hash, decision)
        # Re-check against today's threshold in case it changed.
        decision["qualifies"] = (
            int(decision["match_score"]) >= int(policy["criteria"]["minimum_match_score"])
            and not decision.get("exclusion_reasons")
        )
        scored.append({"job": job.model_dump(mode="json"), "decision": decision})

    scored.sort(key=lambda item: -int(item["decision"]["match_score"]))
    queue = [item for item in scored if item["decision"]["qualifies"]]
    _json_write(QUEUE_PATH, queue)
    _json_write(PROJECT_ROOT / "data" / "last_run_scores.json", scored)

    lines = [f"# Job agent run {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             f"Checked {len(jobs)} jobs. {len(matched)} passed the basic filter. "
             f"{len(queue)} are good enough to apply (score >= {policy['criteria']['minimum_match_score']}).", "",
             "| Score | Apply? | Company | Role | Location | Link |", "|---:|---|---|---|---|---|"]
    for item in scored:
        job, d = item["job"], item["decision"]
        lines.append(f"| {d['match_score']} | {'YES' if d['qualifies'] else 'no'} | {job['company']} | {job['title']} | {job['location']} | {job['application_url']} |")
    (PROJECT_ROOT / "data" / "last_run.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"fetched": len(jobs), "prefiltered": len(matched), "ready_to_apply": len(queue)}


def run(args: argparse.Namespace) -> int:
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=2))
    print(f"Report: {PROJECT_ROOT / 'data' / 'last_run.md'}")
    if result["ready_to_apply"]:
        print("Next: python -m job_agent.cli apply")
    return 0


async def _ask(question: str) -> bool:
    answer = await asyncio.to_thread(input, question)
    return answer.strip().lower().startswith("y")


async def _apply(args: argparse.Namespace) -> list[dict[str, Any]]:
    from .agents import draft_form_answers
    from .assist import assist_application

    if not provider_key_present():
        raise RuntimeError("An AI API key is required to fill forms (see .env)")
    policy = load_policy()
    profile = load_profile()
    from .learning import load_learned

    learned_path = PROJECT_ROOT / "config" / "learned_answers.yaml"
    profile["learned_answers"] = load_learned(learned_path)
    store = ApplicationStore(PROJECT_ROOT / "data" / "job_agent.sqlite3")
    if not QUEUE_PATH.is_file():
        raise RuntimeError("No queue yet. Run: python -m job_agent.cli run")
    queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    if args.job_id:
        queue = [q for q in queue if q["job"]["source_job_id"] == args.job_id]
        if not queue and Path(PROJECT_ROOT / "data" / "last_run_scores.json").is_file():
            every = json.loads((PROJECT_ROOT / "data" / "last_run_scores.json").read_text(encoding="utf-8"))
            queue = [q for q in every if q["job"]["source_job_id"] == args.job_id]
    submitted = store.submitted_job_ids()
    results = []
    for item in queue[: args.limit]:
        job = JobPosting.model_validate(item["job"])
        if job.source_job_id in submitted:
            continue
        try:
            result = await assist_application(
                job,
                int(item["decision"]["match_score"]),
                policy,
                profile,
                store,
                PROJECT_ROOT / "artifacts",
                PROJECT_ROOT / "data" / "browser-profile",
                draft_form_answers,
                ask_human=_ask,
                learned_path=learned_path,
            )
            profile["learned_answers"] = load_learned(learned_path)
        except Exception as exc:
            print(f"    ! {job.company}: {exc}")
            results.append({"company": job.company, "title": job.title, "status": "ERROR", "error": str(exc)})
            continue
        print(f"    Result: {result.status}")
        results.append({"company": job.company, "title": job.title, "status": result.status, **result.detail})
        if result.status == "POLICY_BLOCKED" and "daily_limit_reached" in result.detail.get("reasons", []):
            print("    Daily limit reached. Stopping for today.")
            break
    return results


def apply(args: argparse.Namespace) -> int:
    results = asyncio.run(_apply(args))
    path = _json_write(PROJECT_ROOT / "artifacts" / "last_apply.json", results)
    print(json.dumps([{k: r[k] for k in ("company", "title", "status")} for r in results], indent=2))
    print(f"Details: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.set_defaults(handler=doctor)

    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--output", required=True)
    fetch_parser.set_defaults(handler=fetch)

    prefilter_parser = subparsers.add_parser("prefilter")
    prefilter_parser.add_argument("jobs")
    prefilter_parser.add_argument("--output", required=True)
    prefilter_parser.set_defaults(handler=prefilter)

    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("plan")
    evaluate_parser.set_defaults(handler=evaluate)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("job")
    plan_parser.add_argument("--job-id")
    plan_parser.add_argument("--output", required=True)
    plan_parser.set_defaults(handler=plan)

    run_parser = subparsers.add_parser("run", help="find and score jobs, build the apply queue")
    run_parser.set_defaults(handler=run)

    apply_parser = subparsers.add_parser("apply", help="open and fill each queued application")
    apply_parser.add_argument("--job-id")
    apply_parser.add_argument("--limit", type=int, default=5)
    apply_parser.set_defaults(handler=apply)
    return parser


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        # Deterministic commands still work before optional project install.
        pass
    args = build_parser().parse_args()
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
