"""
Shared ffmpeg and winget availability checks.
Used by both timelapse and RTSP features.
"""
import subprocess


def is_ffmpeg_available() -> bool:
    """Check if ffmpeg is installed and accessible in PATH."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def is_winget_available() -> bool:
    """Check if winget (Windows Package Manager) is available."""
    try:
        result = subprocess.run(
            ['winget', '--version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False
