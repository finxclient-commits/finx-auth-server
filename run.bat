@echo off
title FinxClient Auth Server & Discord Bot
cd /d "%~dp0"
echo ===================================================
echo Starting FinxClient Auth Server & Discord Bot
echo ===================================================
echo Installing/Verifying dependencies...
py -m pip install -r requirements.txt
echo.
echo Launching Server...
py bot.py
pause
