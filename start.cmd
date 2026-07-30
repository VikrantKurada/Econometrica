@echo off
rem Double-clickable wrapper so the stack starts from Explorer as well as a
rem shell. -ExecutionPolicy Bypass applies to this invocation only; it changes
rem nothing about the machine's policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
