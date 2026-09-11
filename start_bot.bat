@echo off
title FinxClient Auth Bot
cd /d "c:\Users\User\Downloads\67ClientSourceByDexter\finx-auth-server"
:restart
echo [%date% %time%] Starting FinxClient bot...
py bot.py
echo [%date% %time%] Bot exited, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto restart