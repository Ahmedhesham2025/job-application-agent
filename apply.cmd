@echo off
rem Double-click: opens each queued job in Chrome, fills it, and waits for you to press Submit.
cd /d "%~dp0"
set PYTHONPATH=src
set JOB_AGENT_HOME=%~dp0
".venv\Scripts\python.exe" -m job_agent.cli apply
pause
