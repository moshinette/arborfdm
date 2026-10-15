@echo off
REM Run ArborFDM from the source tree (Windows).
REM   scripts\run_dev.bat                 GUI
REM   scripts\run_dev.bat cli ARGS...     command line (python -m arbor ARGS...)
REM   scripts\run_dev.bat test            pytest
setlocal enableextensions
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
REM no pushd: relative file arguments keep working
set "PY=py -3"
%PY% --version >nul 2>&1 || set "PY=python"
if not exist "%ROOT%\.venv" (
  %PY% -m venv "%ROOT%\.venv" || goto :fail
  call "%ROOT%\.venv\Scripts\activate.bat" || goto :fail
  python -m pip install -e "%ROOT%[gui,dev]" || goto :fail
) else (
  call "%ROOT%\.venv\Scripts\activate.bat" || goto :fail
)
set "PYTHONPATH=%ROOT%"
if /i "%~1"=="cli" (
  shift
  goto :cli
)
if /i "%~1"=="test" goto :test
python -m arbor.app.main %*
exit /b %ERRORLEVEL%

:cli
set "ARGS="
:collect
if "%~1"=="" goto :runcli
set ARGS=%ARGS% %1
shift
goto :collect
:runcli
python -m arbor %ARGS%
exit /b %ERRORLEVEL%

:test
REM %%ERRORLEVEL%% is read outside any ( ) block, so it is the pytest result
pushd "%ROOT%"
python -m pytest -q -p no:cacheprovider
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:fail
exit /b 1
