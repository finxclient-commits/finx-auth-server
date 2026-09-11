@echo off
setlocal
title FinxClient - Update Java Client Endpoint
color 0B

echo.
echo  ================================================
echo   FinxClient - Update Server URL + Rebuild JAR
echo  ================================================
echo.
set /p RAILWAY_URL="Enter your Railway URL (e.g. finxclient-auth.up.railway.app): "

echo.
echo Updating LicenseConfig.java...
set JAVAFILE=c:\Users\User\Downloads\67ClientSourceByDexter\67Client SourceByDexter\src\main\java\dev\sixseven\license\LicenseConfig.java
powershell -Command "(Get-Content '%JAVAFILE%') -replace 'http://localhost:3000/api/verify', 'https://%RAILWAY_URL%/api/verify' | Set-Content '%JAVAFILE%'"

echo Rebuilding JAR...
cd /d "c:\Users\User\Downloads\67ClientSourceByDexter\67Client SourceByDexter"
call gradlew.bat build

echo.
echo Copying new JAR to auth server...
copy /y "build\libs\FinxClient-1.6.2.jar" "c:\Users\User\Downloads\67ClientSourceByDexter\finx-auth-server\client\FinxClient-1.6.2.jar"

echo.
echo  JAR rebuilt and copied!
echo  Now upload it to your GitHub repo as a Release asset,
echo  then set JAR_DOWNLOAD_URL in Railway to the download link.
echo.
pause