@echo off
REM ArborFDM Windows build: venv + dependencies + PyInstaller (onedir) + zip.
REM Usage (from any folder):  scripts\build_windows.bat
REM Needs Python 3.10+ 64-bit on PATH (py launcher or python). NOT tested on Windows yet:
REM the same spec was only built and run on Linux (see docs\PACKAGING.md).
setlocal enableextensions
set "ROOT=%~dp0.."
pushd "%ROOT%" || exit /b 1

set "PY=py -3"
%PY% --version >nul 2>&1 || set "PY=python"
%PY% --version || (echo Python not found & goto :fail)

if not exist .venv-build (
  %PY% -m venv .venv-build || goto :fail
)
call .venv-build\Scripts\activate.bat || goto :fail
python -m pip install --upgrade pip || goto :fail
python -m pip install ".[gui,dev]" || goto :fail

REM run the tests that do not need a display before packaging
set QT_QPA_PLATFORM=offscreen
python -m pytest -q -p no:cacheprovider tests\test_cli.py tests\test_export.py tests\test_export_extra.py || goto :fail

python -m PyInstaller --noconfirm --clean packaging\arborfdm.spec || goto :fail

REM smoke test of the frozen CLI
dist\ArborFDM\arborfdm-cli.exe --version || goto :fail
dist\ArborFDM\arborfdm-cli.exe generate examples\shelf.stl -o build\smoke_shelf.3mf -q || goto :fail

for /f "usebackq delims=" %%v in (`python -c "import importlib.metadata as m; print(m.version('arborfdm'))"`) do set "VER=%%v"
if "%VER%"=="" set "VER=dev"
set "ZIP=dist\ArborFDM-%VER%-windows-x64.zip"
if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ArborFDM' -DestinationPath '%ZIP%'" || goto :fail
echo.
echo Built %ZIP%
popd
exit /b 0

:fail
echo BUILD FAILED
popd
exit /b 1
