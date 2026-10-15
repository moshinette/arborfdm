@echo off
REM ArborFDM Windows build: venv + dependencies + tests + PyInstaller (onedir) + zip.
REM Usage (from any folder):  scripts\build_windows.bat
REM The console only shows the steps; the full output goes to build\build_windows.log
REM Needs Python 3.10.1+ 64-bit (3.12 recommended). Python 3.10.0 has a bug in its "dis"
REM module that crashes PyInstaller ("IndexError: tuple index out of range").
setlocal enableextensions
set "ROOT=%~dp0.."
pushd "%ROOT%" || exit /b 1
if not exist build mkdir build
set "LOG=%CD%\build\build_windows.log"

if /i "%~1"=="__inner" goto :inner

echo Build ArborFDM - full log: %LOG%
call "%~f0" __inner > "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo BUILD OK
  findstr /b /c:"Built " "%LOG%"
) else (
  echo BUILD FAILED - last lines of the log:
  echo ------------------------------------------------------------
  powershell -NoProfile -Command "Get-Content -Tail 25 '%LOG%'"
  echo ------------------------------------------------------------
  echo Full log: %LOG%
)
popd
exit /b %RC%

:inner
REM everything below goes to the log; "> con" lines are also shown in the console
set "PY=py -3"
%PY% --version >nul 2>&1 || set "PY=python"
%PY% --version || (echo Python not found> con & echo Python not found & goto :fail)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10, 1) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
  echo [ERROR] Python 3.10.1 or newer, 64-bit, is required. Install Python 3.12 from python.org,> con
  echo         then delete the .venv-build folder and run this script again.> con
  echo [ERROR] Python 3.10.1+ 64-bit required
  goto :fail
)

echo [1/5] Python environment (.venv-build)> con
if exist .venv-build\Scripts\python.exe (
  .venv-build\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 10, 1) else 1)"
  if errorlevel 1 (
    echo       old Python in .venv-build: recreating it> con
    rmdir /s /q .venv-build
  )
)
if not exist .venv-build (
  %PY% -m venv .venv-build || goto :fail
)
call .venv-build\Scripts\activate.bat || goto :fail
python --version

echo [2/5] Installing dependencies (a few minutes the first time)> con
python -m pip install --upgrade pip || goto :fail
python -m pip install ".[gui,dev]" || goto :fail

echo [3/5] Tests> con
set QT_QPA_PLATFORM=offscreen
python -m pytest -q -p no:cacheprovider tests\test_cli.py tests\test_export.py tests\test_export_extra.py || goto :fail

echo [4/5] PyInstaller (several minutes)> con
python -m PyInstaller --noconfirm --clean packaging\arborfdm.spec || goto :fail

echo [5/5] Smoke test and zip> con
dist\ArborFDM\arborfdm-cli.exe --version || goto :fail
dist\ArborFDM\arborfdm-cli.exe generate examples\shelf.stl -o build\smoke_shelf.3mf -q || goto :fail

for /f "usebackq delims=" %%v in (`python -c "import importlib.metadata as m; print(m.version('arborfdm'))"`) do set "VER=%%v"
if "%VER%"=="" set "VER=dev"
set "ZIP=dist\ArborFDM-%VER%-windows-x64.zip"
if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ArborFDM' -DestinationPath '%ZIP%'" || goto :fail
echo Built %ZIP%
exit /b 0

:fail
echo BUILD FAILED
exit /b 1
