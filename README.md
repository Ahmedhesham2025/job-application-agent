# Job Application Agent

An AI assistant for job hunting, built in Python with the Claude API and Playwright.

- **Finds** jobs on public company job boards (Greenhouse, Lever, Ashby).
- **Scores** each job against your CV with a fixed rubric, and lists the gaps.
- **Fills** the application form in a real browser: CV upload, cover letter, every question it can answer from your verified facts.
- **You press Submit.** The agent never clicks Submit and never solves CAPTCHAs.
- **Learns** the answers you type on one form and reuses them on the next.
- A rule-based safety gate checks every answer: allowed sites only, verified facts only, personal questions always "Prefer not to say", daily limit, no duplicates. 29 automated tests.

Built by [Ahmed Hesham](https://linkedin.com/in/ahmed-hesham-data-scientist).

### Use it with your own CV

```powershell
copy config\candidate_profile.example.yaml config\candidate_profile.yaml   # then put your own facts in it
mkdir config\resume                                                         # put your CV PDF here
```

Set `source_document.path` and `sha256` in your profile, and the same hash in
`config/policy.yaml` -> `submission.approved_resume_sha256`. Your profile, CV, `.env`,
`data/` and `artifacts/` are in `.gitignore`, so they are never committed.

## How to use it (assist mode)

The agent finds jobs, scores them against your CV, opens each good job in Chrome
and fills the form. **You** solve the CAPTCHA and press **Submit**.
Every job site it uses (Greenhouse, Ashby, Lever) has a CAPTCHA, so the last click is always yours.

### One-time setup (PowerShell, in this folder)

```powershell
python -m venv .venv                 # skip if .venv already exists
.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium   # only used if Google Chrome is not installed
$env:PYTHONPATH = "src"
python -m unittest discover -s tests    # should end with OK
python -m job_agent.cli doctor          # resume_hash_matches must be true
```

### Every time

1. Double-click **find_jobs.cmd**. It checks all job feeds and scores new jobs.
   The report opens: `data\last_run.md`. Jobs marked YES are in the queue.
2. Double-click **apply.cmd**. For each queued job, Chrome opens with the form filled.
   - A yellow note at the top says what still needs you. Those questions have a red box.
   - Check every answer. Fix anything wrong.
   - Solve the CAPTCHA if shown, press **Submit**, then close the window.
   - If the "thank you" page was not detected, the terminal asks "Did you submit?". Type y or n.
3. The agent records each submitted job so it never opens it again. Max 5 per day.

Evidence for each application (filled-form screenshot, cover letter, answers) is saved in
`artifacts\assist\<date>-<company>\`.

### Run the search every day (optional)

```powershell
schtasks /Create /SC DAILY /ST 08:40 /TN "Job agent - find jobs" /TR "powershell -ExecutionPolicy Bypass -File \"%CD%\run_daily.ps1\""
```

This only finds and scores jobs. It never opens a form. Open `data\last_run.md` and run `apply.cmd` when you are ready.

### What the agent will and will not answer

- Uses your CV facts and the answers in `config/candidate_profile.yaml`.
- Facts marked `assumed: true` were guessed (salary, pronouns, "how did you hear", EU citizen...). Check them once and edit the file if wrong.
- Personal questions (gender, race, disability, veteran) are always answered "Prefer not to say".
- Never answers: criminal record, security clearance, tests, video interviews. These are left to you.
- Open questions ("why us?") are written only from your real experience. Read them before you submit.

### Change the pass mark

`config/policy.yaml` -> `criteria.minimum_match_score` (now 82). Lower it to get more jobs in the queue.

---

## Technical design

This starter implements two AI specialists with a deterministic safety boundary:

1. **Finder agent** scores discovered jobs against explicit criteria.
2. **Application agent** prepares a typed application plan using only verified candidate facts.
3. **Policy controller** independently validates the domain, score, resume hash, daily limit, duplicate status, answer categories, and fact provenance.
4. **Adapter** performs a dry run. Live submission is intentionally absent until a site-specific adapter is implemented and tested.

The AI agents never receive a live submit capability. A normal Python controller owns that side effect.

## Current safety state

- Submission mode is `assist`: the agent fills the form, the human submits. The agent never clicks Submit.
- `submission.live_submission_enabled` is `false`.
- Only a dry-run adapter is included.
- Unknown candidate facts block submission.
- Verified relocation, availability, sponsorship, salary, and work-authorization answers may be used. Values must match the verified profile exactly.
- CAPTCHAs, redirects, assessments, sensitive questions, and unsupported fields must go to review.
- LinkedIn automation is intentionally unsupported.

## Setup

```powershell
cd job-application-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

The default provider is Anthropic Claude. Add a newly created
`ANTHROPIC_API_KEY` to `.env`, or export it in your shell. Never paste a key
into chat or commit it. Set `AI_PROVIDER=openai` to use the OpenAI path instead.
Run commands from the project directory. If a scheduler uses another working
directory, set `JOB_AGENT_HOME` to this project directory.

## Before running an agent

1. Review `config/candidate_profile.yaml` against the original CV.
2. Fill the facts marked `verified: false` only with answers the candidate confirmed.
3. Edit `config/policy.yaml` with exact target roles, locations, salary rules, ATS domains, and daily limits.
4. Add enabled company board tokens under `discovery.sources`.
5. Keep `submission.mode: dry_run` and `live_submission_enabled: false` until dry-run evidence is correct.

## Commands

Run deterministic checks without an API key:

```powershell
$env:PYTHONPATH = "src"
python -m job_agent.cli doctor
python -m unittest discover -s tests -v
python -m job_agent.cli evaluate examples/application_plan.json
```

Fetch configured public Lever, Greenhouse, and Ashby job feeds:

```powershell
python -m job_agent.cli fetch --output data/fetched_jobs.json
python -m job_agent.cli prefilter data/fetched_jobs.json --output data/shortlist.json
```

Run the two-agent workflow for one normalized job:

```powershell
python -m job_agent.cli plan data/shortlist.json --job-id JOB_ID --output artifacts/run.json
```

A successful plan ends with either `POLICY_BLOCKED` or `DRY_RUN_READY`; it never sends an application.

## Enabling real submission

Do not change the dry-run adapter into a generic browser agent. Add one explicit adapter per approved ATS or company domain. Each adapter must:

- re-check the final hostname after every redirect;
- inspect all fields before filling anything;
- reject unknown fields and unsupported widgets;
- stop on CAPTCHA, assessment, recorded interview, login challenge, or legal attestation;
- compare every filled value with the approved application plan;
- verify the approved resume SHA-256 hash;
- reserve an idempotency key before clicking submit;
- capture a pre-submit screenshot and a confirmation receipt;
- fail closed when selectors or page structure change.

Only after adapter tests and repeated dry runs should a separately implemented live adapter be selected and `live_submission_enabled` be changed to `true`.

## Project map

```text
config/                    Candidate facts and policy
examples/                  Non-submitting example inputs
src/job_agent/agents.py    Finder and application agents
src/job_agent/sources.py   Public job-feed readers
src/job_agent/policy.py    Deterministic allowlist gate
src/job_agent/database.py  Idempotency and audit records
src/job_agent/adapters/    Dry-run submission boundary
src/job_agent/workflow.py  Orchestration
tests/                     Deterministic policy tests
```
