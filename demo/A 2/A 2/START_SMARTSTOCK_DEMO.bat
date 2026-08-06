@echo off
setlocal
title SmartStock Demo Launcher

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start_SmartStock_Demo.ps1"

