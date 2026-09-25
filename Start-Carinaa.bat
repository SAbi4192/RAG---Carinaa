@echo off
setlocal EnableExtensions
rem ============================================================================
rem  Carinaa - one-click launcher
rem
rem    Start-Carinaa.bat            production: build the frontend if needed,
rem                                 start the backend, open http://127.0.0.1:8000
rem                                 (one origin: the backend serves frontend/dist)
rem    Start-Carinaa.bat dev        live reload: Vite on :5173 (proxies /api)
rem                                 plus uvicorn --reload on :8000
rem    Start-Carinaa.bat test       run the backend test suite
rem    Start-Carinaa.bat build      typecheck + build the frontend, then exit
rem
rem  Production keeps the server in a minimized window titled "Carinaa server";
rem  close that window to stop. Everything is quoted so the "&" and the spaces
rem  in this folder's name stay safe.
rem
rem  Two batch pitfalls this file deliberately avoids, because both fail
rem  silently rather than loudly:
rem    1. %errorlevel% inside a parenthesised block is expanded when the block
rem       is PARSED, before the command in it has run - so block-style error
rem       checks always see the PREVIOUS command's status. Control flow here
rem       uses goto/labels instead.
rem    2. `goto` inside a routine invoked with `call` does not return from that
rem       call; it leaves the routine and jumps on. Shared work therefore lives
rem       in routines that end in `exit /b` and are never jumped out of.
rem ============================================================================

set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "RC=0"
cd /d "%ROOT%"

if exist "%PY%" goto :dispatch
echo [!] No virtualenv found at .venv\Scripts\python.exe
echo     Create it once from the project root:
echo         python -m venv .venv
echo         ".venv\Scripts\python" -m pip install -r backend\requirements.txt -r backend\requirements-dev.txt
echo.
pause
exit /b 1

:dispatch
if /i "%~1"=="test"  goto :mode_test
if /i "%~1"=="build" goto :mode_build
if /i "%~1"=="dev"   goto :mode_dev
goto :mode_serve

rem ==================================================================== test
:mode_test
echo Running the backend test suite...
rem pytest.ini sets pythonpath to "." and the suite imports app.*, so it has to
rem run from backend/; from the repo root the imports cannot resolve.
pushd "%ROOT%backend"
"%PY%" -m pytest -m "not slow" -q
set "RC=%errorlevel%"
popd
if not "%RC%"=="0" goto :fail_tests
echo.
echo All tests passed.
goto :end

:fail_tests
echo.
echo [!] Tests failed.
goto :end_rc

rem =================================================================== build
:mode_build
call :require_node   || goto :end_rc
call :require_tools  || goto :end_rc
call :do_build
goto :end_rc

rem ==================================================================== dev
:mode_dev
call :require_node   || goto :end_rc
call :require_tools  || goto :end_rc
echo Starting Vite (http://localhost:5173, proxies /api) in a minimized window...
start "Carinaa vite dev" /min /D "%ROOT%frontend" node node_modules\vite\bin\vite.js
echo Starting the API with live reload. Press Ctrl+C in this window to stop it.
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend --reload
goto :end_rc

rem ================================================== production (default)
rem `start` inherits this script's working directory (set by `cd /d` above), so
rem no /D is needed - and a /D path ending in backslash-quote is a quoting trap
rem not worth taking.
:mode_serve
if exist "%ROOT%frontend\dist\index.html" goto :serve_launch
call :require_node   || goto :end_rc
call :require_tools  || goto :end_rc
echo First run: building the frontend...
call :do_build || goto :end_rc
if not exist "%ROOT%frontend\dist\index.html" (
    echo [!] The build produced no frontend\dist\index.html.
    set "RC=1"
    goto :end_rc
)

:serve_launch
echo Starting Carinaa...
start "Carinaa server" /min "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
rem ping, not timeout: `timeout` refuses to run when stdin is redirected (which
rem happens whenever this file is run from another script or a scheduled task).
ping -n 5 127.0.0.1 >nul
start "" http://127.0.0.1:8000/app
echo.
echo   Carinaa is starting:  http://127.0.0.1:8000
echo   Server window with the logs, and the way to stop it: the minimized
echo   "Carinaa server" window.
echo.
pause
goto :end

rem =========================================================== subroutines
rem Typecheck + production build. Ends via `exit /b`, so `call :do_build || ...`
rem and `call :do_build` followed by `goto :end_rc` both behave.
:do_build
echo Typechecking...
pushd "%ROOT%frontend"
rem npm's .ps1/.cmd shim resolves against the wrong Node on this machine, so the
rem tools are run through node directly.
node node_modules\typescript\bin\tsc --noEmit -p tsconfig.json
set "RC=%errorlevel%"
if not "%RC%"=="0" goto :do_build_fail
echo Building...
node node_modules\vite\bin\vite.js build
set "RC=%errorlevel%"
:do_build_fail
popd
if not "%RC%"=="0" (
    echo.
    echo [!] Frontend build failed - see the output above.
    exit /b %RC%
)
echo Build finished: frontend\dist is up to date.
exit /b 0

rem Exits the CALLER via || when Node is absent.
:require_node
where node >nul 2>nul
if errorlevel 1 (
    echo [!] This mode needs Node.js on the PATH.
    echo     Plain production mode still works if frontend\dist is already built.
    set "RC=1"
    exit /b 1
)
exit /b 0

:require_tools
if exist "%ROOT%frontend\node_modules\" exit /b 0
echo [!] frontend\node_modules is missing. Install dependencies once:
echo         cd frontend ^&^& npm install
set "RC=1"
exit /b 1

rem =================================================================== exit
:end
set "RC=0"
:end_rc
endlocal & exit /b %RC%
