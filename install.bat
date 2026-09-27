@echo off
rem Windows counterpart of install.sh. Downloads the standalone ani_py.py from
rem the source repository and installs it, plus the shared ani-py.bat launcher,
rem under <prefix>\bin. Defaults to %USERPROFILE%\.local\bin to mirror the Unix
rem ~/.local/bin target: no admin rights needed, no Program Files churn.
rem
rem The installed launcher prefers `uv run python`, so a uv-equipped machine
rem resolves the interpreter through pyproject.toml/uv.lock automatically.
setlocal EnableExtensions

rem Capture the script directory before any shift: cmd rewrites %~dp0 once
rem shift has run, so read it up front or the launcher lookup goes astray.
set "SCRIPT_DIR=%~dp0"

set "REPO=%ANI_PY_REPO%"
if "%REPO%"=="" set "REPO=Onehand-Coding/ani-py"
set "REF=%ANI_PY_REF%"
if "%REF%"=="" set "REF=main"
set "WITH_DEPS=0"
set "PREFIX="
set "TOUCH_PATH=1"

set "HOME_DIR=%USERPROFILE%"
if "%HOME_DIR%"=="" set "HOME_DIR=%HOMEDRIVE%%HOMEPATH%"
set "URL=https://raw.githubusercontent.com/%REPO%/%REF%/ani_py.py"
rem Never name this TMP: TMP/TMPDIR are standard env vars that child
rem processes (uv, curl) treat as a temp *directory*, so reusing the name
rem makes uv mkdir a directory here and the download then fails to write.
set "ANI_PY_TMP=%TEMP%\ani-py-install-%RANDOM%.tmp"

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--deps" (set "WITH_DEPS=1" & shift & goto :parse)
if /i "%~1"=="--no-path" (set "TOUCH_PATH=0" & shift & goto :parse)
if /i "%~1"=="--prefix" goto :needprefix
if /i "%~1"=="-h" goto :help
if /i "%~1"=="--help" goto :help
echo error: unknown option: %~1 1>&2
call :usage
exit /b 2

:needprefix
if "%~2"=="" (
  echo error: --prefix requires a directory 1>&2
  call :usage
  exit /b 2
)
set "PREFIX=%~2"
shift
shift
goto :parse

:parsed
if "%PREFIX%"=="" set "PREFIX=%HOME_DIR%\.local"
set "BINDIR=%PREFIX%\bin"
set "TARGET=%BINDIR%\ani-py"
set "STUB=%BINDIR%\ani-py.bat"
set "STUB_SRC=%SCRIPT_DIR%ani-py.bat"

if not exist "%STUB_SRC%" (
  echo error: %STUB_SRC% is missing; run install.bat from a checkout 1>&2
  exit /b 1
)

if "%WITH_DEPS%"=="1" call :install_deps

call :check_interpreter

call :download
if errorlevel 1 exit /b 1

rem install.sh validates the shebang before installing; mirror that check.
set "FIRSTLINE="
set /p FIRSTLINE=<"%ANI_PY_TMP%"
if not "%FIRSTLINE:~0,2%"=="#!" (
  echo error: downloaded file does not look like an executable script 1>&2
  del /q "%ANI_PY_TMP%" 2>nul
  exit /b 1
)

if not exist "%BINDIR%" mkdir "%BINDIR%"
if not exist "%BINDIR%" (
  echo error: could not create %BINDIR% 1>&2
  del /q "%ANI_PY_TMP%" 2>nul
  exit /b 1
)

copy /y "%ANI_PY_TMP%" "%TARGET%" >nul
if errorlevel 1 (
  echo error: could not write %TARGET% 1>&2
  del /q "%ANI_PY_TMP%" 2>nul
  exit /b 1
)
del /q "%ANI_PY_TMP%" 2>nul

rem Ship the checked-in launcher verbatim rather than generating one, so the
rem repo copy and the installed copy can never drift.
copy /y "%STUB_SRC%" "%STUB%" >nul
if errorlevel 1 (
  echo error: could not write %STUB% 1>&2
  exit /b 1
)

echo Installed ani-py to %TARGET%
echo Installed launcher to %STUB%

call "%STUB%" --version
if errorlevel 1 echo warning: the installed launcher did not run cleanly 1>&2

if "%TOUCH_PATH%"=="0" goto :no_path_note
call :ensure_path

where mpv >nul 2>nul
if not errorlevel 1 goto :player_note
where vlc >nul 2>nul
if not errorlevel 1 goto :player_note
echo Note: install mpv or VLC before playback.

:player_note
echo.
echo Try it:  ani-py "frieren"
exit /b 0

:no_path_note
echo Skipped PATH update. Add %BINDIR% to your PATH to run: ani-py
echo.
echo Try it:  "%STUB%" "frieren"
exit /b 0

:help
call :usage
exit /b 0

:check_interpreter
where uv >nul 2>nul
if not errorlevel 1 exit /b 0
where python >nul 2>nul
if not errorlevel 1 exit /b 0
where py >nul 2>nul
if not errorlevel 1 exit /b 0
echo warning: no uv, python, or py on PATH; ani-py requires Python 3.10+ 1>&2
echo          install.bat --deps will install an interpreter via uv. 1>&2
exit /b 0

:download
where curl >nul 2>nul
if not errorlevel 1 goto :download_curl
where powershell >nul 2>nul
if not errorlevel 1 goto :download_ps
echo error: curl or powershell is required to download ani-py 1>&2
exit /b 1

:download_curl
echo Downloading ani-py from %REPO%@%REF% ...
curl -fsSL "%URL%" -o "%ANI_PY_TMP%"
if errorlevel 1 goto :download_failed
exit /b 0

:download_ps
rem curl.exe ships with Windows 10+ since 1803, so prefer this over a winget
rem install of curl.
echo Downloading ani-py from %REPO%@%REF% ...
set "ANI_PY_URL=%URL%"

powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing -Uri $env:ANI_PY_URL -OutFile $env:ANI_PY_TMP"
if errorlevel 1 goto :download_failed
exit /b 0

:download_failed
echo error: download failed: %URL% 1>&2
del /q "%ANI_PY_TMP%" 2>nul
exit /b 1

rem Registers %BINDIR% in the per-user PATH. Reads and writes the registry
rem value directly because setx silently truncates PATH at 1024 characters.
rem Exit 2 means we added it, 0 means already present, 1 means failure.
rem Single quotes, not double: cmd strips doubled "" before PowerShell sees it.
:ensure_path
set "ANI_PY_BINDIR=%BINDIR%"
powershell -NoProfile -Command "$d=$env:ANI_PY_BINDIR; $p=[Environment]::GetEnvironmentVariable('Path','User'); if($null -eq $p){$p=''}; $parts=@(); foreach($x in ($p -split ';')){ if($x -ne ''){ $parts += $x } }; if($parts -contains $d){ exit 0 }; [Environment]::SetEnvironmentVariable('Path',(($parts + $d) -join ';'),'User'); exit 2"
if errorlevel 2 (
  echo Added %BINDIR% to your user PATH. Open a new terminal to pick it up.
) else if errorlevel 1 (
  echo warning: could not update your user PATH 1>&2
) else (
  echo %BINDIR% is already on your PATH.
)
exit /b 0

:install_deps
rem winget replaces the apt/dnf/pacman/zypper/apk fan-out in install.sh.
where winget >nul 2>nul
if not errorlevel 1 goto :winget_deps
echo warning: winget unavailable; skipping dependency install 1>&2
exit /b 0

:winget_deps
rem uv owns the interpreter; winget owns the external tools. curl.exe is built
rem into Windows 10+ since 1803, so it is never installed here.
call :winget_one astral-sh.uv uv
where uv >nul 2>nul
if errorlevel 1 goto :winget_tools
rem Probe before installing. `uv python install` aborts with "Executable
rem already exists ... but is not managed by uv" when an unmanaged shim sits
rem in the uv bin dir, which is a warning, not a broken install.
uv python find 3.12 >nul 2>nul
if not errorlevel 1 (
  echo   a uv-managed Python 3.12 is already available
  goto :winget_tools
)
echo   installing a uv-managed Python interpreter ...
uv python install 3.12
if errorlevel 1 echo   warning: uv python install failed 1>&2

:winget_tools
call :winget_one junegunn.fzf fzf
call :winget_one VideoLAN.VLC vlc
call :winget_one shinchiro.mpv mpv
call :winget_one yt-dlp.yt-dlp yt-dlp
exit /b 0

rem %1 = winget package id, %2 = command to probe on PATH (optional).
rem Probing first keeps --deps non-destructive: yt-dlp installed via pip or
rem mpv from another source must not get a second copy on PATH.
:winget_one
set "WINGET_ID=%~1"
if not "%~2"=="" (
  where "%~2" >nul 2>nul
  if not errorlevel 1 (
    echo   skipping %WINGET_ID% ^(%~2 already on PATH^)
    exit /b 0
  )
)
echo   installing %WINGET_ID% ...
winget install --id %WINGET_ID% -e --disable-interactivity --accept-package-agreements --accept-source-agreements
if errorlevel 1 echo   warning: winget could not install %WINGET_ID% 1>&2
exit /b 0

:usage
echo Usage: install.bat [--deps] [--prefix DIR] [--no-path] [-h^|--help]
echo.
echo Installs the standalone ani-py script for Windows.
echo   --deps        install VLC, fzf, mpv, yt-dlp via winget plus a uv-managed Python
echo   --prefix DIR  install under DIR\bin instead of the default
echo   --no-path     do not add the install directory to your user PATH
echo   -h, --help    show this help
echo.
echo Default: %HOME_DIR%\.local\bin
echo.
echo Environment:
echo   ANI_PY_REPO   source repository, default Onehand-Coding/ani-py
echo   ANI_PY_REF    source ref, default main
echo.
echo The installed launcher prefers `uv run python`, falling back to python
echo and then the py launcher. WSL and Termux users should use install.sh
echo inside that environment instead of this script.
exit /b 0
