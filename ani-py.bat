@echo off
rem Windows counterpart of the ./ani-py launcher. ani-py is a POSIX shebang
rem script, so cmd.exe cannot execute it directly; hand the file to Python
rem and forward every argument untouched via %*.
rem
rem Interpreter order: uv run python, then python, then the Windows py
rem launcher. uv first so a checkout honours pyproject.toml and uv.lock; uv
rem outside a project is a plain pass-through to the resolved interpreter and
rem creates nothing. Copy this file next to any ani-py script to launch it.
setlocal EnableExtensions
set "ANI_PY=%~dp0ani-py"

where uv >nul 2>nul
if not errorlevel 1 goto :uv
where python >nul 2>nul
if not errorlevel 1 goto :python
where py >nul 2>nul
if not errorlevel 1 goto :pylauncher

echo error: Python 3.10+ was not found on PATH. 1>&2
echo Install uv (https://docs.astral.sh/uv/), which can provide Python 1>&2
echo itself, or Python (https://www.python.org/downloads/). Or run the 1>&2
echo launcher yourself: 1>&2
echo   py -3 "%ANI_PY%" %* 1>&2
exit /b 1

:uv
uv run python "%ANI_PY%" %*
exit /b %errorlevel%

:python
python "%ANI_PY%" %*
exit /b %errorlevel%

:pylauncher
py -3 "%ANI_PY%" %*
exit /b %errorlevel%
