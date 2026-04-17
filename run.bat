@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

python -m desktop --self-check
if errorlevel 1 exit /b %errorlevel%

python -m desktop
