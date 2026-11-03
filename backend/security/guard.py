"""
security/guard.py – Path validation, safe-delete, and access control
FIXES:
  - Reject symlinks to prevent bypass
  - Use Path.is_relative_to() for safer path checks
  - Improved error logging
"""

import os
import shutil
import pathlib
import logging
import aiosqlite
from datetime import datetime
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Import from dedicated config
from config import RECYCLE_BIN

RECYCLE_BIN.mkdir(parents=True, exist_ok=True)


# ── Path helpers ──────────────────────────────────────────────────────────────

def resolve_safe(raw: str) -> pathlib.Path:
    """
    Resolve a raw path string to an absolute, real path.
    **SECURITY**: Rejects symlinks to prevent access control bypass.
    Raises HTTPException 400 if the path is suspicious.
    """
    try:
        p = pathlib.Path(raw).expanduser()
        if not p.is_absolute():
            p = pathlib.Path.cwd() / p
        # Check each existing component before resolve(). Checking only the
        # leaf misses paths such as trusted/link-to-outside/file.txt.
        _reject_linked_components(p)
        p = p.resolve(strict=False)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Invalid path: {exc}")

    # Block obvious traversal tricks
    str_p = str(p)
    if ".." in pathlib.Path(raw).parts:
        raise HTTPException(400, "Path traversal detected")
    if any(c in str_p for c in ["\x00", "\n", "\r"]):
        raise HTTPException(400, "Null/control bytes in path")

    return p


def _reject_linked_components(path: pathlib.Path) -> None:
    """Reject symlink/junction components before canonicalizing a user path."""
    current = pathlib.Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise HTTPException(400, "Symlinks and junctions are not allowed")
        except OSError as exc:
            raise HTTPException(400, "Cannot safely inspect path") from exc


async def assert_under_allowed(
    target: pathlib.Path,
    db: aiosqlite.Connection,
) -> None:
    """
    Ensure `target` lives inside one of the user-selected directories.
    **SECURITY**: Uses Path.is_relative_to() for safe path comparison.
    Raises HTTPException 403 otherwise.
    """
    async with db.execute("SELECT path FROM directories") as cur:
        rows = await cur.fetchall()

    allowed = [pathlib.Path(r["path"]).resolve() for r in rows]
    
    # FIX: Use is_relative_to() for safer path containment check
    # This properly handles edge cases like /allowed_evil under /allowed
    target_resolved = target.resolve()
    if not any(
        _is_under_path(target_resolved, a) for a in allowed
    ):
        raise HTTPException(403, "Access denied: path outside allowed directories")


def _is_under_path(target: pathlib.Path, allowed_root: pathlib.Path) -> bool:
    """
    Safely check if target is under allowed_root using is_relative_to().
    Returns True only if target is truly under the allowed root.
    """
    try:
        target.relative_to(allowed_root)
        return True
    except ValueError:
        return False


def safe_delete(file_path: pathlib.Path) -> pathlib.Path:
    """
    Move a file to the recycle bin instead of hard-deleting it.
    Returns the recycle-bin path.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"{file_path} does not exist")

    if file_path.is_symlink():
        logger.warning("Refusing to delete symlink: %s", file_path)
        raise HTTPException(400, "Cannot delete symlinks")

    file_path = pathlib.Path(resolve_safe(str(file_path)))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = RECYCLE_BIN / f"{timestamp}__{file_path.name}"
    suffix = 1
    while dest.exists():
        dest = RECYCLE_BIN / f"{timestamp}_{suffix}__{file_path.name}"
        suffix += 1

    shutil.move(str(file_path), str(dest))
    logger.info("🗑  Moved %s → %s", file_path, dest)
    return dest


def validate_directory(path_str: str) -> pathlib.Path:
    """
    Validate that the given string is a readable directory.
    Returns resolved Path or raises HTTPException.
    """
    p = resolve_safe(path_str)
    
    # FIX: Check symlink after resolve_safe already rejected it
    # This is defensive; resolve_safe should have caught it
    if p.is_symlink():
        raise HTTPException(400, "Path cannot be a symlink")
    
    if not p.exists():
        raise HTTPException(400, f"Path does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(400, f"Not a directory: {p}")
    if not os.access(p, os.R_OK):
        raise HTTPException(400, f"No read permission: {p}")
    return p
