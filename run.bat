@echo off
setlocal

python -m desktop --self-check
if errorlevel 1 exit /b %errorlevel%

python -m desktop
