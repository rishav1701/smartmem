"""Safely discover and optionally start a local Ollama server."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import pathlib
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

import httpx

from config import ENABLE_OLLAMA, OLLAMA_AUTOSTART, OLLAMA_BASE

logger = logging.getLogger(__name__)
_START_LOCK = asyncio.Lock()
_START_ATTEMPTED = False
_SERVER_PROCESS: subprocess.Popen | None = None


def _local_ollama_host() -> tuple[str, int] | None:
    """Return a loopback bind address only for a plain local Ollama URL."""
    try:
        parsed = urlsplit(OLLAMA_BASE)
        if parsed.scheme != "http" or parsed.username or parsed.password:
            return None
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
        if host != "localhost":
            try:
                if not ipaddress.ip_address(host).is_loopback:
                    return None
            except ValueError:
                return None
        return "127.0.0.1", parsed.port or 11434
    except ValueError:
        return None


def _find_ollama() -> str | None:
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = pathlib.Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
            if candidate.is_file():
                return str(candidate)
    return shutil.which("ollama")


async def _is_running() -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.0, trust_env=False) as client:
            response = await client.get(f"{OLLAMA_BASE.rstrip('/')}/api/tags")
            return response.status_code == 200
    except (httpx.HTTPError, ValueError):
        return False


async def ensure_ollama_started() -> str:
    """Start Ollama once when enabled and configured for a local loopback URL.

    This never executes a shell command or attempts to launch a remote endpoint.
    It leaves process supervision to the operating system, matching Ollama's
    normal background-server model.
    """
    global _START_ATTEMPTED, _SERVER_PROCESS
    if not ENABLE_OLLAMA or not OLLAMA_AUTOSTART:
        return "disabled"
    if await _is_running():
        return "already_running"
    bind = _local_ollama_host()
    if bind is None:
        logger.info("Ollama auto-start skipped for a non-loopback or unsupported endpoint")
        return "non_local_endpoint"

    async with _START_LOCK:
        if await _is_running():
            return "already_running"
        if _START_ATTEMPTED:
            return "not_running"
        _START_ATTEMPTED = True
        executable = _find_ollama()
        if not executable:
            logger.warning("Ollama is not running and its executable was not found")
            return "executable_not_found"
        env = os.environ.copy()
        env["OLLAMA_HOST"] = f"{bind[0]}:{bind[1]}"
        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": env,
            "close_fds": True,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            _SERVER_PROCESS = subprocess.Popen([executable, "serve"], **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Could not start Ollama: %s", exc)
            return "start_failed"

        # Startup can take a moment, particularly when Ollama initializes GPU support.
        for _ in range(20):
            await asyncio.sleep(0.5)
            if await _is_running():
                logger.info("Ollama server started by Memora")
                return "started"
            if _SERVER_PROCESS.poll() is not None:
                logger.warning("Ollama server process exited during startup")
                return "start_failed"
        logger.warning("Ollama process started but its API did not become ready in 10 seconds")
        return "start_timeout"
