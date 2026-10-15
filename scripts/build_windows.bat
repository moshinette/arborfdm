@echo off
REM ArborFDM Windows build: venv + dependencies + tests + PyInstaller (onedir) + zip.
REM Usage (from any folder):  scripts\build_windows.bat
REM The console only shows the steps; the full output goes to build\build_windows.log
REM Needs Python 3.10.1 to 3.14, 64-bit (3.12 recommended). Python 3.10.0 crashes PyInstaller
REM (bug in its "dis" module); Python 3.15+ has no manifold3d wheel (source build needs TBB).
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
REM Pick a supported Python: 3.10.1 to 3.14, 64-bit (manifold3d has no Windows wheel for
REM newer versions, and 3.10.0 crashes PyInstaller). Prefer 3.12.
set "PY="
for %%V in (3.12 3.13 3.11 3.14 3.10) do (
  if not defined PY (
    py -%%V -c "import sys; sys.exit(0 if (3, 10, 1) <= sys.version_info[:3] and sys.version_info[:2] <= (3, 14) and sys.maxsize > 2**32 else 1)" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3, 10, 1) <= sys.version_info[:3] and sys.version_info[:2] <= (3, 14) and sys.maxsize > 2**32 else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] No supported Python found: need Python 3.10.1 to 3.14, 64-bit ^(3.12 recommended^).> con
  echo         Install Python 3.12 64-bit from python.org, then run this script again.> con
  echo [ERROR] no supported Python
  goto :fail
)
echo       using: %PY%> con
%PY% --version

echo [1/5] Python environment (.venv-build)> con
if exist .venv-build\Scripts\python.exe (
  .venv-build\Scripts\python.exe -c "import sys; sys.exit(0 if (3, 10, 1) <= sys.version_info[:3] and sys.version_info[:2] <= (3, 14) else 1)"
  if errorlevel 1 (
    echo       unsupported Python in .venv-build: recreating it> con
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
