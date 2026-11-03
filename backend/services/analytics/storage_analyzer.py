"""
services/analytics/storage_analyzer.py
----------------------------------------
Accurate storage breakdown using psutil + shutil.

psutil is used (as in the uploaded main.py reference) for:
  - Per-disk partition stats
  - Disk I/O counters (read/write bytes since boot)
  - Real used/free/total that matches OS reports
"""

import logging
import pathlib
import collections
import aiosqlite
from database.db import DB_PATH

logger = logging.getLogger(__name__)


# ── Real OS disk usage via psutil ─────────────────────────────────────────────

async def get_disk_usage() -> list[dict]:
    """
    Return real disk usage for every partition that holds a scanned directory.
    Falls back to shutil.disk_usage if psutil is missing.
    """
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT path FROM directories") as cur:
            dirs = [r["path"] for r in await cur.fetchall()]

    results = []
    seen_devices: set[str] = set()

    try:
        import psutil

        # Build a map of mountpoint → partition info
        partitions = {p.mountpoint: p for p in psutil.disk_partitions(all=False)}

        for d in dirs:
            try:
                p   = pathlib.Path(d).resolve()
                mnt = _best_mount(str(p), list(partitions.keys()))
                part = partitions.get(mnt)
                device = part.device if part else mnt
                if device in seen_devices:
                    continue
                seen_devices.add(device)

                usage = psutil.disk_usage(str(p))

                # Try to get I/O stats
                io_stats = {}
                try:
                    io_all  = psutil.disk_io_counters(perdisk=True)
                    disk_name = pathlib.Path(device).name if device else ""
                    io = io_all.get(disk_name)
                    if io:
                        io_stats = {
                            "read_bytes":  io.read_bytes,
                            "write_bytes": io.write_bytes,
                            "read_count":  io.read_count,
                            "write_count": io.write_count,
                        }
                except Exception:
                    pass

                results.append({
                    "mount":       mnt,
                    "device":      device,
                    "fstype":      part.fstype if part else "",
                    "total_bytes": usage.total,
                    "used_bytes":  usage.used,
                    "free_bytes":  usage.free,
                    "used_pct":    round(usage.percent, 1),
                    **io_stats,
                })
            except Exception as exc:
                logger.warning("disk_usage failed for %s: %s", d, exc)

    except ImportError:
        # Fallback to shutil
        import shutil
        logger.info("psutil not available, using shutil.disk_usage")
        for d in dirs:
            try:
                p   = pathlib.Path(d).resolve()
                mnt = str(p.anchor)
                if mnt in seen_devices:
                    continue
                seen_devices.add(mnt)
                usage = shutil.disk_usage(str(p))
                results.append({
                    "mount":       mnt,
                    "device":      mnt,
                    "fstype":      "",
                    "total_bytes": usage.total,
                    "used_bytes":  usage.used,
                    "free_bytes":  usage.free,
                    "used_pct":    round(usage.used / usage.total * 100, 1),
                })
            except Exception as exc:
                logger.warning("shutil.disk_usage failed for %s: %s", d, exc)

    # If no directories configured, show root/C: info
    if not results:
        try:
            import shutil
            usage = shutil.disk_usage(pathlib.Path.home())
            results.append({
                "mount":       str(pathlib.Path.home().anchor),
                "device":      "",
                "fstype":      "",
                "total_bytes": usage.total,
                "used_bytes":  usage.used,
                "free_bytes":  usage.free,
                "used_pct":    round(usage.used / usage.total * 100, 1),
            })
        except Exception:
            pass

    return results


def _best_mount(path: str, mounts: list[str]) -> str:
    """Return the most specific (longest matching) mountpoint for path."""
    mounts_sorted = sorted(mounts, key=len, reverse=True)
    for m in mounts_sorted:
        if path.startswith(m):
            return m
    return mounts_sorted[-1] if mounts_sorted else path


# ── Scanned-file stats ────────────────────────────────────────────────────────

async def get_top_level_stats() -> dict:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            """SELECT
                 COUNT(*)           AS total_files,
                 SUM(size_bytes)    AS total_bytes,
                 AVG(size_bytes)    AS avg_bytes,
                 MAX(size_bytes)    AS max_bytes,
                 MIN(size_bytes)    AS min_bytes,
                 COUNT(DISTINCT extension) AS unique_extensions
               FROM files_metadata WHERE is_deleted=0"""
        ) as cur:
            row = dict(await cur.fetchone())

        async with db.execute(
            """SELECT
                 SUM(CASE WHEN sha256       IS NOT NULL THEN 1 ELSE 0 END) AS hashed,
                 SUM(CASE WHEN ai_category  IS NOT NULL THEN 1 ELSE 0 END) AS categorised,
                 SUM(CASE WHEN embedding_id IS NOT NULL THEN 1 ELSE 0 END) AS embedded,
                 SUM(CASE WHEN doc_text IS NOT NULL AND doc_text != '' THEN 1 ELSE 0 END) AS with_text
               FROM files_metadata WHERE is_deleted=0"""
        ) as cur:
            extra = dict(await cur.fetchone())

        row.update(extra)

        async with db.execute(
            "SELECT path, filename, size_bytes FROM files_metadata "
            "WHERE is_deleted=0 ORDER BY size_bytes DESC LIMIT 1"
        ) as cur:
            biggest = await cur.fetchone()
            row["largest_file"] = dict(biggest) if biggest else None

        async with db.execute(
            "SELECT MIN(modified_at) AS oldest, MAX(modified_at) AS newest "
            "FROM files_metadata WHERE is_deleted=0"
        ) as cur:
            dates = await cur.fetchone()
            row["oldest_file"] = dates["oldest"]
            row["newest_file"] = dates["newest"]

    return row


# ── Extension breakdown ───────────────────────────────────────────────────────

async def get_extension_breakdown() -> list[dict]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT extension,
                      COUNT(*)        AS file_count,
                      SUM(size_bytes) AS total_bytes,
                      AVG(size_bytes) AS avg_bytes,
                      MAX(size_bytes) AS max_bytes
               FROM files_metadata WHERE is_deleted=0
               GROUP BY extension ORDER BY total_bytes DESC LIMIT 30"""
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_category_size_breakdown() -> list[dict]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT COALESCE(ai_category,'Uncategorised') AS category,
                      COUNT(*)        AS file_count,
                      SUM(size_bytes) AS total_bytes
               FROM files_metadata WHERE is_deleted=0
               GROUP BY ai_category ORDER BY total_bytes DESC"""
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Storage tree (O(n) build) ─────────────────────────────────────────────────

async def get_storage_tree() -> dict:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT path, size_bytes FROM files_metadata WHERE is_deleted=0"
        ) as cur:
            file_rows = await cur.fetchall()
        async with db.execute("SELECT path FROM directories") as cur:
            roots = [r["path"] for r in await cur.fetchall()]

    dir_sizes: dict[str, int] = collections.defaultdict(int)
    for row in file_rows:
        for parent in pathlib.Path(row["path"]).parents:
            dir_sizes[str(parent)] += row["size_bytes"]

    children_map: dict[str, list[str]] = collections.defaultdict(list)
    all_dirs = set(dir_sizes.keys())
    for d in all_dirs:
        parent = str(pathlib.Path(d).parent)
        if parent != d and parent in all_dirs:
            children_map[parent].append(d)

    def build_node(path: str, depth: int = 0) -> dict:
        name = pathlib.Path(path).name or path
        size = dir_sizes.get(path, 0)
        if depth >= 4:
            return {"name": name, "value": size}
        kids = sorted(
            children_map.get(path, []),
            key=lambda x: dir_sizes.get(x, 0), reverse=True
        )[:8]
        child_nodes = [build_node(c, depth + 1) for c in kids]
        direct = max(0, size - sum(dir_sizes.get(c, 0)
                                   for c in children_map.get(path, [])))
        if direct > 0:
            child_nodes.append({"name": "(files)", "value": direct})
        return {"name": name, "value": size, "children": child_nodes}

    nodes = [build_node(r) for r in roots]
    total = sum(dir_sizes.get(r, 0) for r in roots)
    return {"name": "root", "value": total, "children": nodes}
