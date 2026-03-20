"""
Shared ffmpeg and winget availability checks.
Used by the timelapse feature.
"""
import glob
import os
import shutil
import subprocess
import sys

# Hide console windows for subprocess calls on Windows
_POPEN_KWARGS = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}


def get_ffmpeg_path() -> str:
    """
    Return the full path to the ffmpeg executable.

    Search order:
    1. System/user PATH (shutil.which)
    2. winget packages folder — Gyan.FFmpeg installs as a zip extract
       to %LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\ and may not
       add itself to PATH on all winget versions.

    Falls back to the bare string 'ffmpeg' so callers can still attempt
    to run it and get a natural FileNotFoundError if truly absent.
    """
    # 1. PATH check
    path = shutil.which('ffmpeg')
    if path:
        return path

    # 2. winget packages folder
    winget_base = os.path.join(
        os.getenv('LOCALAPPDATA', ''),
        'Microsoft', 'WinGet', 'Packages'
    )
    for candidate in glob.glob(
        os.path.join(winget_base, 'Gyan.FFmpeg*', '**', 'ffmpeg.exe'),
        recursive=True,
    ):
        if os.path.isfile(candidate):
            return candidate

    return 'ffmpeg'


def is_ffmpeg_available() -> bool:
    """Check if ffmpeg is installed and runnable (PATH or winget packages folder)."""
    path = get_ffmpeg_path()
    try:
        result = subprocess.run([path, '-version'], capture_output=True, timeout=5, **_POPEN_KWARGS)
        return result.returncode == 0
    except Exception:
        return False


def is_winget_available() -> bool:
    """Check if winget (Windows Package Manager) is available."""
    try:
        result = subprocess.run(['winget', '--version'], capture_output=True, timeout=5, **_POPEN_KWARGS)
        return result.returncode == 0
    except Exception:
        return False
