"""
File discovery and type classification.

Walks a root folder, skips the review/output folder and hidden system files,
and classifies each file into one of: 'image', 'video', 'other'.

Classification drives which detectors run:
  - every file goes through the exact-hash (SHA-256) layer
  - images additionally go through perceptual + crop detection
  - videos additionally go through frame-hash + segment search
  - 'other' (PDF, Word, audio, archives, ...) is exact-only
"""

from pathlib import Path

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".heic", ".heif", ".jfif",
    # RAW formats — treated as images for exact matching; perceptual hashing
    # of RAW is unreliable without development, so they mainly match exactly.
    ".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf", ".rw2",
}

VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp", ".m2ts", ".mts", ".ts",
}

REVIEW_DIR_NAME = "_REVIEW_DUPLICATES"


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "other"


def scan_folder(root: Path):
    """
    Yield (path, kind) for every regular file under root, recursively.
    Skips the review folder, the cache file, and hidden/system files.
    """
    root = Path(root)
    for p in root.rglob("*"):
        # Skip anything inside the review/output folder
        if REVIEW_DIR_NAME in p.parts:
            continue
        if not p.is_file():
            continue
        name = p.name
        if name.startswith(".") or name.startswith("~$"):
            continue
        if name == ".dedup_cache.json" or name == "dedup_data.json" or name.endswith(".tmp"):
            continue
        yield p, classify(p)


def scan_grouped(root: Path):
    """
    Convenience: return a dict {kind: [paths]} plus a flat list of all paths.
    """
    groups = {"image": [], "video": [], "other": []}
    all_paths = []
    for p, kind in scan_folder(root):
        groups[kind].append(p)
        all_paths.append(p)
    return groups, all_paths
