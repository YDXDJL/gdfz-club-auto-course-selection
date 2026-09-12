@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python course_app.py
) else (
    py -3 course_app.py
)
if errorlevel 1 pause
