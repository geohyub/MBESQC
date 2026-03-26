@echo off
chcp 65001 >nul
title MBESQC Desktop
cd /d E:\Software\QC\MBESQC
set PYTHONPATH=E:\Software\_shared;E:\Software\QC\MBESQC
python -m desktop
pause
