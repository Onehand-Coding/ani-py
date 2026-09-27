@echo off
rem Windows counterpart of scripts/run-tests.sh. Same four stages, driven by
rem uv instead of a bare python3, and using the POSIX-launcher-safe invocation
rem (uv run python ani-py) because ani-py is a shebang script and cannot be
rem spawned directly on Windows.
setlocal EnableExtensions
pushd "%~dp0.."

where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found on PATH. Install it from https://docs.astral.sh/uv/ 1>&2
  popd
  exit /b 1
)

echo [1/4] Compiling Python files...
uv run python -m py_compile ani_py.py ani-py
if errorlevel 1 goto :fail

echo [2/4] Running unit tests...
uv run python -m unittest discover -s tests -v
if errorlevel 1 goto :fail

echo [3/4] Smoke-checking CLI help/version...
uv run python ani-py --help >nul
if errorlevel 1 goto :fail
uv run python ani-py --version
if errorlevel 1 goto :fail

echo [4/4] Building standalone artifact...
if not exist "dist" mkdir "dist"
copy /y "ani_py.py" "dist\ani-py" >nul
if errorlevel 1 goto :fail
uv run python -m py_compile "dist\ani-py"
if errorlevel 1 goto :fail
if exist "dist\__pycache__" rmdir /s /q "dist\__pycache__"
uv run python "dist\ani-py" --version
if errorlevel 1 goto :fail
echo Built %CD%\dist\ani-py

echo All checks passed.
popd
exit /b 0

:fail
echo.
echo Checks failed.
popd
exit /b 1
