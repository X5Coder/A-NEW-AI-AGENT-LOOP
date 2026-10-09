@echo off
REM Thin wrapper: ONE command on Windows -> install.py (detects OS, checks env, runs flow via Git-Bash).
python "%~dp0install.py" %*
