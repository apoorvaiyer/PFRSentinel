@echo off
setlocal enabledelayedexpansion
REM Build script for PFR Sentinel executable
REM Creates a Windows executable using PyInstaller
REM
REM IMPORTANT: For production releases, set DEV_MODE_AVAILABLE = False in services\dev_mode_config.py
REM
REM Usage:
REM   build_sentinel.bat

echo ========================================
echo   PFR Sentinel - Build Executable
echo ========================================
echo.

set SPEC_FILE=PFRSentinel.spec

echo Building: PySide6 Fluent UI
echo Spec file: %SPEC_FILE%
echo.

REM Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo WARNING: Virtual environment not found
    echo Continuing with system Python...
)

echo.
echo Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist\PFRSentinel rmdir /s /q dist\PFRSentinel

echo.
echo Building executable with PyInstaller...
venv\Scripts\python.exe -m PyInstaller %SPEC_FILE%

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Build failed!
    pause
    exit /b 1
)

REM Step 2: Sign executable (if certificate available)
echo.
echo Signing executable...

REM Auto-connect SimplySign if OTP URI is configured
if defined CERTUM_OTP_URI (
    echo Connecting to SimplySign...
    powershell -ExecutionPolicy Bypass -File "%~dp0scripts\Connect-SimplySign.ps1" -SkipIfConnected
)

REM Set signtool path
set SIGNTOOL="C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"

REM Check for certificate thumbprint in environment or use default
if not defined CODE_SIGNING_THUMBPRINT (
    set CODE_SIGNING_THUMBPRINT=B5E267FE814CD41B883876712CA326C288FB3492
)

if exist %SIGNTOOL% (
    echo Using certificate: %CODE_SIGNING_THUMBPRINT%
    echo NOTE: Approve signing request in SimplySign mobile app...
    %SIGNTOOL% sign /sha1 %CODE_SIGNING_THUMBPRINT% /tr http://time.certum.pl /td SHA256 /fd SHA256 /d "PFR Sentinel" "dist\PFRSentinel\PFRSentinel.exe"
    if !ERRORLEVEL! EQU 0 (
        echo Executable signed successfully!
    ) else (
        echo WARNING: Signing failed, continuing with unsigned executable
    )
) else (
    echo WARNING: signtool.exe not found, skipping code signing
    echo Install Windows SDK to enable signing
)

echo.
echo ========================================
echo   Build completed successfully!
echo ========================================
echo.
echo Executable location:
echo   dist\PFRSentinel\PFRSentinel.exe
echo.
echo ========================================
echo   REMINDER: Production Build Checklist
echo ========================================
echo.
echo Before releasing, verify:
echo   1. services\dev_mode_config.py has DEV_MODE_AVAILABLE = False
echo   2. Test executable doesn't create raw_debug files
echo   3. Test executable doesn't show Developer Mode section in UI
echo.
echo You can now run:
echo   dist\PFRSentinel\PFRSentinel.exe
echo.
echo Or build the installer with:
echo   build_sentinel_installer.bat
echo.

REM Only pause if run directly (not from installer script)
if not defined BUILD_FROM_INSTALLER pause
exit /b 0
