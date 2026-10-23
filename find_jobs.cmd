@echo off
rem Double-click: finds and scores new jobs now.
cd /d "%~dp0"
set PYTHONPATH=src
set JOB_AGENT_HOME=%~dp0
".venv\Scripts\python.exe" -m job_agent.cli run
start "" "data\last_run.md"
pause
