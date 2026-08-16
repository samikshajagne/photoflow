@echo off
REM ===========================================================================
REM  PhotoFlow — one-command Windows build
REM  Samiksha Technologies
REM
REM  Usage (from the project root, in an activated virtualenv):
REM      packaging\build.bat
REM      packaging\build.bat SKIP_TESTS   (skips step 2/4 only; preflight always runs)
REM
REM  Produces:
REM      dist\PhotoFlow\PhotoFlow.exe                     (frozen app)
REM      packaging\output\PhotoFlow-Setup-<version>.exe   (installer)
REM
REM  Requires: Python with the project's dependencies, PyInstaller, and Inno
REM  Setup 6 (iscc.exe) on PATH. See BUILD.md.
REM ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

set SKIP_TESTS=
if /I "%~1"=="SKIP_TESTS" set SKIP_TESTS=1

echo.
echo === PhotoFlow build ===
echo Working directory: %CD%
echo.

REM --- Sanity check: are we in the right place? ------------------------------
if not exist "ui_qt\main.py" (
    echo ERROR: ui_qt\main.py not found. Run this from the project root:
    echo        packaging\build.bat
    exit /b 1
)

REM --- Read the version from the single source of truth ----------------------
for /f "delims=" %%v in ('python -c "import sys; sys.path.insert(0,'.'); from utils.version import __version__; print(__version__)"') do set APPVER=%%v
if "%APPVER%"=="" (
    echo ERROR: could not read the version from utils\version.py
    exit /b 1
)
echo Building version %APPVER%
echo.

REM --- Step 1: preflight + version resource ---------------------------------
REM Preflight catches missing data files, undeclared lazy imports and absent
REM dependencies in about a second, instead of after a multi-minute build that
REM only fails once a customer runs it. It also generates version_info.txt.
echo [1/4] Preflight checks...
python packaging\preflight.py || goto :preflight_failed

REM --- Step 2: run the tests ------------------------------------------------
REM Shipping a build that fails its own tests is not worth the time saved.
REM SKIP_TESTS skips only this step -- preflight (step 1) always still runs.
echo.
if defined SKIP_TESTS (
    echo [2/4] Skipping tests ^(SKIP_TESTS^)...
) else (
    echo [2/4] Running tests...
    python -m pytest tests -q || goto :tests_failed
)

REM --- Step 3: freeze with PyInstaller -------------------------------------
echo.
echo [3/4] Freezing with PyInstaller...
if exist "build" rmdir /s /q "build"
if exist "dist\PhotoFlow" rmdir /s /q "dist\PhotoFlow"
pyinstaller packaging\photoflow.spec --noconfirm || goto :failed

if not exist "dist\PhotoFlow\PhotoFlow.exe" (
    echo ERROR: PyInstaller finished but dist\PhotoFlow\PhotoFlow.exe is missing.
    goto :failed
)

REM --- Step 4: build the installer -----------------------------------------
echo.
echo [4/4] Building the installer...
where iscc >nul 2>nul
if errorlevel 1 (
    echo.
    echo WARNING: iscc.exe ^(Inno Setup^) was not found on PATH.
    echo The frozen app is ready in dist\PhotoFlow, but no installer was built.
    echo Install Inno Setup 6 from https://jrsoftware.org/isdl.php and re-run,
    echo or add its folder to PATH.
    goto :done_noinstaller
)
iscc /DMyAppVersion=%APPVER% packaging\installer.iss || goto :failed

echo.
echo === Build complete ===
echo   App:       dist\PhotoFlow\PhotoFlow.exe
echo   Installer: packaging\output\PhotoFlow-Setup-%APPVER%.exe
echo.
echo NEXT: sign the installer before publishing it, or Windows SmartScreen will
echo       warn your customers. See packaging\BUILD.md.
goto :eof

:done_noinstaller
echo.
echo === Partial build complete (app only) ===
goto :eof

:preflight_failed
echo.
echo BUILD ABORTED: preflight checks failed. See the errors above -- these are
echo problems that would otherwise surface only after a full build, or worse,
echo on a customer's machine.
exit /b 1

:tests_failed
echo.
echo BUILD ABORTED: the test suite failed. Fix that before shipping a build.
exit /b 1

:failed
echo.
echo BUILD FAILED. See the output above.
exit /b 1
