"""
Layer 0 — exact duplicate detection via SHA-256.

Works for ANY file type (photos, videos, PDFs, Word docs, audio, archives).
Two files with the same SHA-256 are byte-for-byte identical: a true duplicate
with zero false positives. This is the foundation of the whole tool — it is
fast, certain, and language/format independent.

To stay fast on large media libraries we hash in chunks and short-circuit:
files of different sizes cannot be identical, so we only fully hash files that
share a size with at least one other file.
"""

import hashlib
import os
from collections import defaultdict
from pathlib import Path

CHUNK = 1024 * 1024  # 1 MiB read blocks


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def quick_signature(path: Path) -> str:
    """
    Cheap pre-hash: size + first/last 64 KiB. Files that differ here cannot be
    identical, so we avoid full-hashing unique files. Collisions here are fine —
    they only mean we fall through to a full SHA-256.
    """
    st = path.stat()
    size = st.st_size
    h = hashlib.sha1()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        head = f.read(65536)
        h.update(head)
        if size > 131072:
            f.seek(-65536, os.SEEK_END)
            h.update(f.read(65536))
    return h.hexdigest()


def find_exact_duplicates(paths, cache=None, key_fn=None, root=None,
                          progress=None):
    """
    Group byte-identical files.

    Returns (groups, sha_by_path):
      groups      : list of lists; each inner list is 2+ identical file Paths
      sha_by_path : dict {Path: sha256}  (only for files that were fully hashed)

    Optimization passes:
      1. bucket by file size            (different size => not identical)
      2. within a size bucket, bucket by quick_signature
      3. within a quick bucket, full SHA-256
    """
    paths = list(paths)

    # Pass 1: size buckets
    by_size = defaultdict(list)
    for p in paths:
        try:
            by_size[p.stat().st_size].append(p)
        except OSError:
            continue

    # Only sizes shared by 2+ files can contain duplicates
    candidates = [grp for grp in by_size.values() if len(grp) > 1]

    sha_by_path = {}
    groups = []
    total = sum(len(g) for g in candidates)
    done = 0

    for size_group in candidates:
        # Pass 2: quick signature buckets
        by_quick = defaultdict(list)
        for p in size_group:
            try:
                by_quick[quick_signature(p)].append(p)
            except OSError:
                pass
            done += 1
            if progress and done % 200 == 0:
                progress(done, total, "exact pre-hash")

        for quick_group in by_quick.values():
            if len(quick_group) < 2:
                continue
            # Pass 3: full SHA-256
            by_sha = defaultdict(list)
            for p in quick_group:
                # reuse cached sha if available
                sha = None
                if cache is not None and key_fn is not None and root is not None:
                    entry = cache.get(key_fn(p, root))
                    if entry and entry.get("sha256"):
                        sha = entry["sha256"]
                if sha is None:
                    try:
                        sha = sha256_file(p)
                    except OSError:
                        continue
                sha_by_path[p] = sha
                by_sha[sha].append(p)

            for sha, sha_group in by_sha.items():
                if len(sha_group) > 1:
                    groups.append(sha_group)

    if progress:
        progress(total, total, "exact pre-hash")
    return groups, sha_by_path
