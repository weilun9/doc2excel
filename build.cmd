@echo off
rem Double-click: run the tests, then build dist\Doc2Excel.exe (and the
rem installer if NSIS is installed). Everything is logged to build.log.
cd /d "%~dp0"
echo === %date% %time% tests > build.log
.venv\Scripts\python tests\table_test.py >> build.log 2>&1
.venv\Scripts\python tests\dynamod_test.py >> build.log 2>&1
echo === build >> build.log
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 >> build.log 2>&1
echo === finished exit=%errorlevel% >> build.log
