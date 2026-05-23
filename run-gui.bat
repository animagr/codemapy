@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=src
py -3 src\codemapy\gui.py %*
set "exit_code=%ERRORLEVEL%"
if not "%exit_code%"=="0" (
    echo.
    echo codemapy GUI exited with error code %exit_code%.
    pause
)
exit /b %exit_code%
