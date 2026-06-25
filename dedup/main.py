"""
Duplicate Finder — main orchestrator.

Pipeline:
  1. Scan folder, classify files (image / video / other)
  2. Layer 0: exact SHA-256 duplicates across ALL files
  3. Images: perceptual + crop near-duplicates (excluding files already in an
     exact group, to avoid redundant work)
  4. Videos: frame-hash + segment-search near-duplicates
  5. Merge results, write dedup_data.json for the review UI

Run:
    python -m dedup.main "D:/path/to/folder"
or import and call run_scan(folder).
"""

import os
# Mute FFmpeg/OpenCV decoder warnings before any cv2 import happens downstream.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("OPENCV_LOG_LEVEL", "FATAL")

import json
import sys
import time
import builtins
from pathlib import Path

from .core import scanner, exact, images, videos
from .core.cache import load_cache, save_cache, cache_key


def _bar(done, total, label):
    if total <= 0:
        return
    pct = int(done / total * 100)
    filled = pct // 5
    sys.stdout.write(f"\r  [{'#' * filled}{' ' * (20 - filled)}] "
                     f"{pct:3d}% ({done:,}/{total:,}) {label}    ")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")


def run_scan(folder, write_json=True, progress=None, log=None):
    """Scan a folder for duplicates.

    progress(done, total, label): called for each progress tick (defaults to a
        terminal progress bar). A GUI can pass its own to drive a progress widget.
    log(message): called for status lines (defaults to print). A GUI can pass its
        own to append to a log panel.
    """
    log = log or builtins.print
    progress = progress or _bar
    print = log   # route this function's status prints to the caller's logger

    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a folder: {root}")

    t_start = time.time()
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")

    review_dir = root / scanner.REVIEW_DIR_NAME
    review_dir.mkdir(exist_ok=True)

    print("=" * 62)
    print("  Duplicate Finder  —  scanning")
    print("=" * 62)
    print(f"[OK] Folder : {root}")
    print(f"[OK] Review : {review_dir}")

    groups_by_kind, all_paths = scanner.scan_grouped(root)
    n_img = len(groups_by_kind["image"])
    n_vid = len(groups_by_kind["video"])
    n_oth = len(groups_by_kind["other"])
    print(f"Found {n_img} images, {n_vid} videos, {n_oth} other files "
          f"({len(all_paths)} total)")

    cache = load_cache(root)

    results = []   # each: {"kind","reason","files":[{path,size,...}]}

    # ── Layer 0: exact duplicates (all files) ────────────────────────────────
    print("\n[1/3] Exact duplicate scan (SHA-256, all file types)...")
    exact_groups, sha_by_path = exact.find_exact_duplicates(
        all_paths, cache=cache, key_fn=cache_key, root=root, progress=progress)

    # For near-dup scans we exclude exact-duplicate copies but KEEP one
    # representative per exact group — that representative can still be a
    # near-duplicate of some other file (e.g. a resized or trimmed version).
    exact_redundant = set()   # all-but-one member of each exact group
    for grp in exact_groups:
        # keep the largest file as the representative for near-dup scans
        grp_sorted = sorted(grp, key=lambda p: p.stat().st_size if p.exists() else 0,
                            reverse=True)
        for p in grp_sorted[1:]:
            exact_redundant.add(p)
        for p in grp:
            k = cache_key(p, root)
            entry = cache.get(k, {})
            entry["sha256"] = sha_by_path.get(p, entry.get("sha256"))
            cache[k] = entry
    print(f"  Found {len(exact_groups)} exact-duplicate group(s).")

    for grp in exact_groups:
        results.append(_make_group("exact", "Byte-identical files", grp))

    # ── Images: near-duplicates (skip those already exact-matched) ───────────
    img_paths = [p for p in groups_by_kind["image"] if p not in exact_redundant]
    if img_paths:
        print(f"\n[2/3] Image near-duplicate scan ({len(img_paths)} images)...")
        img_groups = images.find_image_duplicates(img_paths, progress=progress)
        print(f"  Found {len(img_groups)} image near-duplicate group(s).")
        for grp in img_groups:
            results.append(_make_group("image", "Visually similar images", grp))
    else:
        print("\n[2/3] No images to compare for near-duplicates.")

    # ── Videos: near-duplicates ──────────────────────────────────────────────
    vid_paths = [p for p in groups_by_kind["video"] if p not in exact_redundant]
    if vid_paths:
        print(f"\n[3/3] Video near-duplicate scan ({len(vid_paths)} videos)...")
        # reuse cached frames/durations/resolutions
        hashed, res_cache = _load_video_cache(vid_paths, cache, root)
        vid_groups = videos.find_video_duplicates(
            vid_paths, hashed=hashed, res_cache=res_cache,
            progress=progress, status=log)
        _store_video_cache(hashed, res_cache, cache, root)
        print(f"  Found {len(vid_groups)} video near-duplicate group(s).")
        for grp in vid_groups:
            results.append(_make_group("video", "Visually similar / trimmed videos", grp))
    else:
        print("\n[3/3] No videos to compare for near-duplicates.")

    save_cache(root, cache)

    # Merge groups that share any file. The same image can be found by both the
    # exact layer (byte-identical twin) and the image layer (compressed copy);
    # those belong in ONE group of all 5, not a 2-group + a 3-group. We also
    # fold each exact group's members together (so the redundant twins we held
    # out of the near-dup scan rejoin their group).
    results = _merge_overlapping_groups(results, exact_groups)

    elapsed = time.time() - t_start
    finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

    # Tally duplicates by type and how much space the extra copies use
    dup_files = 0
    reclaimable = 0
    by_type = {"image": 0, "video": 0, "other": 0}
    for g in results:
        # every copy beyond the first in a group is a removable duplicate
        extras = g["files"][1:]
        dup_files += len(extras)
        for f in extras:
            reclaimable += f.get("size", 0)
        for f in g["files"]:
            ft = f.get("ftype", "other")
            by_type[ft] = by_type.get(ft, 0) + 1

    def _fmt_secs(s):
        s = int(s)
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        if h:
            return f"{h}h {m}m {sec}s"
        if m:
            return f"{m}m {sec}s"
        return f"{sec}s"

    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_sec": round(elapsed, 1),
        "elapsed_human": _fmt_secs(elapsed),
        "scanned": {"images": n_img, "videos": n_vid, "other": n_oth,
                    "total": n_img + n_vid + n_oth},
        "duplicate_groups": len(results),
        "duplicate_files": dup_files,
        "reclaimable_mb": round(reclaimable / (1024 * 1024), 1),
    }

    payload = {
        "root": str(root),
        "review_dir": str(review_dir),
        "scanned_at": finished_at,
        "counts": {"images": n_img, "videos": n_vid, "other": n_oth},
        "summary": summary,
        "groups": results,
    }

    if write_json:
        out = root / "dedup_data.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    # ── printed summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("  SCAN SUMMARY")
    print("=" * 62)
    print(f"  Started      : {started_at}")
    print(f"  Finished     : {finished_at}")
    print(f"  Total time   : {summary['elapsed_human']}")
    print(f"  Scanned      : {summary['scanned']['total']} files "
          f"({n_img} images, {n_vid} videos, {n_oth} other)")
    print(f"  Duplicates   : {dup_files} extra copies in "
          f"{len(results)} group(s)")
    print(f"  Reclaimable  : {summary['reclaimable_mb']} MB if you remove them")
    print("=" * 62)

    return payload


def _merge_overlapping_groups(groups, exact_groups):
    """
    Combine groups that share at least one file into single unified groups,
    using union-find over file paths.

    Two sources of connection:
      1. files within the same detected group (image/video/exact group)
      2. members of the same exact group — so a byte-identical twin that was
         held out of the near-dup scan rejoins the file that represents it.

    The merged group's kind/reason reflects the strongest relationship:
    if any contributing group was a near-dup (image/video) we label it that,
    otherwise it stays 'exact'.
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # collect file metadata and connections
    meta = {}          # path -> file dict
    kind_of = {}       # path -> set of kinds it appeared under

    for g in groups:
        paths = [f["path"] for f in g["files"]]
        for f in g["files"]:
            meta[f["path"]] = f
            kind_of.setdefault(f["path"], set()).add(g["kind"])
        for p in paths[1:]:
            union(paths[0], p)

    # connect exact-group siblings (Path objects -> str)
    for grp in exact_groups:
        sp = [str(p) for p in grp]
        for p in sp:
            if p not in meta:
                # ensure even held-out twins get a meta entry
                from pathlib import Path as _P
                pp = _P(p)
                try:
                    size = pp.stat().st_size
                except OSError:
                    size = 0
                ftype = scanner.classify(pp)
                w, h, dur = _file_dimensions(pp, ftype)
                meta[p] = {
                    "path": p, "name": pp.name, "size": size,
                    "size_mb": round(size / (1024 * 1024), 2),
                    "ftype": ftype, "w": w, "h": h, "duration": round(dur, 1),
                }
                kind_of.setdefault(p, set()).add("exact")
        for p in sp[1:]:
            union(sp[0], p)

    # gather components
    comps = {}
    for p in meta:
        comps.setdefault(find(p), []).append(p)

    merged = []
    for root_key, paths in comps.items():
        if len(paths) < 2:
            continue
        kinds = set()
        for p in paths:
            kinds |= kind_of.get(p, set())
        if "video" in kinds:
            kind, reason = "video", "Visually similar / trimmed videos"
        elif "image" in kinds:
            kind, reason = "image", "Visually similar images"
        else:
            kind, reason = "exact", "Byte-identical files"
        files = [meta[p] for p in paths]
        files.sort(key=lambda f: f["size"], reverse=True)
        merged.append({"kind": kind, "reason": reason, "files": files})

    # stable order: largest groups first, then by reason
    merged.sort(key=lambda g: (-len(g["files"]), g["kind"]))
    return merged


def _file_dimensions(p, ftype):
    """Return (width, height, duration_sec) for display. Cheap, best-effort."""
    w = h = 0
    dur = 0.0
    try:
        if ftype == "image":
            from PIL import Image
            with Image.open(p) as im:
                w, h = im.size
        elif ftype == "video":
            _, w, h = videos.get_resolution(p)
            dur = videos.get_duration(p)
    except Exception:
        pass
    return w, h, dur


def _make_group(kind, reason, paths):
    files = []
    for p in paths:
        try:
            st = p.stat()
            size = st.st_size
        except OSError:
            size = 0
        ftype = scanner.classify(p)
        w, h, dur = _file_dimensions(p, ftype)
        files.append({
            "path": str(p),
            "name": p.name,
            "size": size,
            "size_mb": round(size / (1024 * 1024), 2),
            "ftype": ftype,
            "w": w,
            "h": h,
            "duration": round(dur, 1),
        })
    # largest file first (usually the best to keep)
    files.sort(key=lambda f: f["size"], reverse=True)
    return {"kind": kind, "reason": reason, "files": files}


def _load_video_cache(vid_paths, cache, root):
    hashed, res_cache = {}, {}
    for p in vid_paths:
        entry = cache.get(cache_key(p, root))
        if entry and entry.get("frames") is not None and "dur" in entry:
            try:
                frames = []
                for fr in entry["frames"]:
                    # v5 frames are [phash_hex, dhash_hex, info]; packed ints
                    ph = int(fr[0], 16)
                    dh = int(fr[1], 16)
                    info = bool(fr[2]) if len(fr) > 2 else True
                    frames.append((ph, dh, info))
                hashed[p] = (frames, entry["dur"])
                if "px" in entry:
                    res_cache[p] = entry["px"]
            except Exception:
                pass
    return hashed, res_cache


def _store_video_cache(hashed, res_cache, cache, root):
    for p, (frames, dur) in hashed.items():
        k = cache_key(p, root)
        entry = cache.get(k, {})
        # phash/dhash are packed 256-bit ints -> 64-char hex
        entry["frames"] = [(format(ph, "064x"), format(dh, "064x"),
                            int(bool(info))) for ph, dh, info in frames]
        entry["dur"] = dur
        if p in res_cache:
            entry["px"] = res_cache[p]
        cache[k] = entry


# imagehash is needed for cache (de)serialization
import imagehash  # noqa: E402


if __name__ == "__main__":
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        folder = input("Enter path to your media folder:\n> ").strip().strip('"')
    payload = run_scan(folder)

    # Automatically launch the review UI (which opens the browser itself)
    if payload and payload.get("groups"):
        print("\nLaunching the review screen...")
        from .ui import review as _review
        _review.STATE["data"] = payload
        _review.STATE["root"] = payload["root"]
        import threading
        import webbrowser

        def _open():
            import time as _t
            _t.sleep(1.2)
            url = "http://127.0.0.1:5000"
            opened = False
            try:
                opened = webbrowser.open(url)
            except Exception:
                opened = False
            if not opened:
                # Windows fallback
                try:
                    os.startfile(url)  # type: ignore[attr-defined]
                except Exception:
                    pass

        threading.Thread(target=_open, daemon=True).start()
        print("Opening http://127.0.0.1:5000 in your browser...")
        print("Press Ctrl+C here when you're done reviewing.\n")
        _review.app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
    else:
        print("\nNo duplicates found — nothing to review.")
