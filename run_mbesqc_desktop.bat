@echo off
chcp 65001 >nul
title MBES QC - Desktop
echo ============================================
echo   MBES QC - Desktop Application (CTk)
echo ============================================
echo.

set PYTHONPATH=E:\Software\_shared;E:\Software\MBESQC
cd /d E:\Software\MBESQC

python gui\app.py
pause
