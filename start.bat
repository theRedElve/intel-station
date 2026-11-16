@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 个人情报站
echo 正在启动个人情报站...
python app.py
if errorlevel 1 (
  echo.
  echo 启动失败，请确认已安装 Python 3.9 及以上版本。
  pause
)
