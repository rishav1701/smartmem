"""Explicit user-triggered actions for opening indexed local files."""
import os
import pathlib
import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import aiosqlite

from database.db import get_db
from security.guard import assert_under_allowed, resolve_safe

router = APIRouter()


class FileActionIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


def _open_in_desktop(path: pathlib.Path) -> None:
    """Ask the desktop shell to open a path without invoking a command shell."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, shell=False, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


@router.post("/open-file")
async def open_file(body: FileActionIn, db: aiosqlite.Connection = Depends(get_db)):
    path = resolve_safe(body.path)
    await assert_under_allowed(path, db)
    if not path.is_file():
        raise HTTPException(404, "File not found")
    try:
        _open_in_desktop(path)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(503, "Could not open a file with the system desktop") from exc
    return {"status": "opened"}


@router.post("/open-folder")
async def open_folder(body: FileActionIn, db: aiosqlite.Connection = Depends(get_db)):
    path = resolve_safe(body.path)
    await assert_under_allowed(path, db)
    folder = path if path.is_dir() else path.parent
    if not folder.is_dir():
        raise HTTPException(404, "Containing folder not found")
    try:
        _open_in_desktop(folder)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(503, "Could not open a folder with the system desktop") from exc
    return {"status": "opened"}
