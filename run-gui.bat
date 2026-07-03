@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=src

rem Ensure the tree-sitter backend and gitignore matcher are available (falls back gracefully if install fails).
py -3 -c "import tree_sitter, tree_sitter_language_pack, pathspec" 2>NUL
if errorlevel 1 (
    echo Installing tree-sitter backend and pathspec...
    py -3 -m pip install tree-sitter tree-sitter-language-pack pathspec
)

py -3 src\codemapy\gui.py %*
set "exit_code=%ERRORLEVEL%"
if not "%exit_code%"=="0" (
    echo.
    echo codemapy GUI exited with error code %exit_code%.
    pause
)
exit /b %exit_code%
