@echo off
setlocal
set PYTHONPATH=%~dp0src
python -m codemapy.gui %*
