# Finds and scores new jobs. Safe to run every day: it never opens a form.
# Result: data\last_run.md (all scores) and data\apply_queue.json (jobs to apply to).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONPATH = "src"
$env:JOB_AGENT_HOME = $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" -m job_agent.cli run *>> "$PSScriptRoot\data\run_daily.log"
