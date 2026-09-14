"""Platform detection and runner dispatch."""

import os
import sys

from .base import Runner


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if hasattr(sys, "getandroidapilevel") or "ANDROID_ROOT" in os.environ:
        return "android"
    return "posix"


def get_runner(platform: str | None = None) -> Runner:
    platform = platform or current_platform()
    if platform == "posix":
        from .posix import PosixRunner

        return PosixRunner()
    if platform == "windows":
        from .windows import WindowsRunner

        return WindowsRunner()
    if platform == "android":
        from .android import AndroidRunner

        return AndroidRunner()
    raise ValueError(f"unknown platform {platform!r}")
