@echo off
setlocal
set GH="C:\Program Files\GitHub CLI\gh.exe"
set GIT="C:\Program Files\Git\cmd\git.exe"
set DIR=c:\Users\User\Downloads\67ClientSourceByDexter\finx-auth-server
cd /d "%DIR%"

title FinxClient - Deploying to GitHub + Railway
color 0B
echo.
echo  ================================================
echo   FinxClient Auth Server - One-Click Deploy
echo  ================================================
echo.

echo [1/4] Logging into GitHub (browser will open)...
%GH% auth login --web --git-protocol https
if %errorlevel% neq 0 (
    echo ERROR: GitHub login failed. Please try again.
    pause & exit /b 1
)

echo.
echo [2/4] Creating private GitHub repository...
%GH% repo create finx-auth-server --private --source=. --remote=origin --push 2>nul
if %errorlevel% neq 0 (
    echo Repo may already exist. Trying to push...
    %GIT% push -u origin main
)
echo  Repository created and code pushed!

echo.
echo [3/4] Opening Railway.app in your browser...
echo  Steps to complete on Railway:
echo    1. Sign in with GitHub
echo    2. Click "New Project" > "Deploy from GitHub repo"
echo    3. Select "finx-auth-server"
echo    4. Click "+ New" > "Database" > "PostgreSQL"  
echo    5. Go to your service > "Variables" and add:
echo.
echo    BOT_TOKEN    = MTU0Nzk3MjQ4MDYxMjgzNTQzOQ.GHhPaD.6LEYesyxfRG0dzz4Uzn8ABe-cOkWwHbimr8YD8
echo    CLIENT_ID    = 1547972480612835439
echo    ADMIN_ROLE_ID = 1482723434931687677
echo    DASHBOARD_URL = https://YOUR-RAILWAY-URL.up.railway.app/dashboard
echo.
start https://railway.app/new/github

echo.
echo [4/4] Done! Your GitHub repo is ready at:
%GH% repo view --web
echo.
echo  After Railway deploys, update DASHBOARD_URL in Railway Variables
echo  then run UPDATE_JAVA_CLIENT.bat to rebuild the Minecraft client.
echo.
pause