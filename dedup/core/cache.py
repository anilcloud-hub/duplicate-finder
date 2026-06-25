"""
Shared cache for all detectors (exact, image, video).

The cache maps a stable file key -> stored hash data, so repeat scans of the
same folder are fast. The key is (relative_path, size, mtime_ns); if any of
those change, the entry is treated as stale and recomputed.

Cache layout on disk (one JSON file per scanned root):
{
  "version": 2,
  "entries": {
     "<key>": { "kind": "video"|"image"|"exact",
                "sha256": "...",            # exact layer (all files)
                "phash": "...",             # image perceptual hash (images)
                "frames": [[ph, dh], ...],  # video frame hashes (videos)
                "dur": 12.3,                # video duration seconds
                "w": 1920, "h": 1080 }      # pixel dimensions
  }
}
"""

import json
import os
from pathlib import Path

CACHE_VERSION = 5   # v5: video frame hashes stored as packed-int hex (fast popcount)
CACHE_NAME = ".dedup_cache.json"


def cache_key(path: Path, root: Path) -> str:
    """Stable key: relative path + size + mtime. Survives folder moves poorly
    on purpose — if the file content/size/time matches we reuse the hash."""
    try:
        st = path.stat()
        rel = os.path.relpath(str(path), str(root))
        return f"{rel}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        return f"{path}|0|0"


def load_cache(root: Path) -> dict:
    """Load the cache file for a scan root. Returns the 'entries' dict."""
    cache_file = root / CACHE_NAME
    if not cache_file.exists():
        return {}
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != CACHE_VERSION:
            return {}  # schema changed — start fresh
        return data.get("entries", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(root: Path, entries: dict) -> None:
    """Write the cache atomically (temp file + rename) so a crash mid-write
    can't corrupt an existing good cache."""
    cache_file = root / CACHE_NAME
    tmp = cache_file.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": CACHE_VERSION, "entries": entries}, f)
        os.replace(tmp, cache_file)
    except OSError:
        # Cache is an optimization; never fail the scan because of it.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
