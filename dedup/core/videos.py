"""
Video near-duplicate detection.

This is the hardest and most distinctive part of the tool. It catches the same
video when it has been:
  - re-encoded at a different resolution (1080p original vs 480p WhatsApp copy)
  - trimmed (a 17s clip cut from a 54s original, from any offset)
  - compressed heavily (messaging-app artifacts)
  - rotated or letterboxed

Design principles (learned the hard way)
----------------------------------------
1. CONTENT, NOT FILENAMES. Routing decisions never depend on how files are
   named — a commercial tool can't assume WhatsApp-style names. Filenames may
   only nudge a threshold, never gate detection.

2. ZERO FILE I/O IN THE HOT LOOP. The O(n^2) pairwise comparison uses only the
   small set of frame hashes already in memory/cache. Opening video files in
   that loop is what made earlier versions freeze on millions of pairs.

3. TWO PASSES.
     Pass 1 (hot loop): compare cached frame hashes for every candidate pair.
       Cheap, no file access. Clear matches are grouped immediately.
     Pass 2 (segment search): only near-miss pairs with a plausible
       trim/compression relationship are re-opened and verified with a dense
       per-frame best-match. This is capped and parallelized so it stays in
       the low-minutes range even on large libraries.

4. SEGMENT SEARCH IS SAFE. Dense per-frame best-match scores unrelated videos
   ~90+ while true trims score in the single digits — so a broad routing net
   does not create false positives; the only cost is time, which the cap and
   parallelism bound.
"""

import os
import time
from pathlib import Path

# Silence the noisy FFmpeg audio-decoder warnings (e.g. "Input buffer exhausted
# before END element found"). We only read video frames, never audio, so these
# are harmless — but they must be muted BEFORE cv2/FFmpeg initialize.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")   # -8 = AV_LOG_QUIET
os.environ.setdefault("OPENCV_LOG_LEVEL", "FATAL")

import cv2
import imagehash
import numpy as np
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
except Exception:
    pass


import contextlib

@contextlib.contextmanager
def _suppress_native_stderr():
    """
    Silence C-level stderr (where FFmpeg prints "Input buffer exhausted",
    "moov atom not found", etc.). The env vars don't reliably mute FFmpeg on
    Windows, so we redirect file descriptor 2 to the null device for the
    duration of video reads, then restore it.
    """
    try:
        stderr_fd = 2
        saved = os.dup(stderr_fd)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, stderr_fd)
        os.close(devnull)
    except Exception:
        saved = None
    try:
        yield
    finally:
        if saved is not None:
            try:
                os.dup2(saved, 2)
                os.close(saved)
            except Exception:
                pass

# ── tunables ──────────────────────────────────────────────────────────────
VIDEO_THR = 30          # base frame-distance match threshold
HASH_SIZE = 16
VIDEO_FRAMES = 16       # frames hashed per video (more = better trim prefiltering)
SEG_SHORT_FRAMES = 8    # short-video samples in segment search
SEG_LONG_FRAMES = 50    # dense long-video samples (50 is the reliability floor)
SEGMENT_SEARCH_CAP = 4000    # max candidate pairs checked in the file-opening pass
SEG_CHECK_TIMEOUT = 20       # seconds; skip a pair whose video reads hang
SEG_READ_BUDGET = 8          # seconds max spent reading frames from one video
# A pair only enters the file-opening trim pass if its best cached cross-frame
# match is below this — a real trim shares frames (low value), unrelated videos
# don't (high value). Measured separation: trims ~50-65, unrelated ~95+.
TRIM_PREFILTER = 80
HASH_WORKERS = os.cpu_count() or 4
# Parallelism for the (now multi-threaded) frame-hashing pass. Video decode is
# CPU-heavy and releases the GIL, so threads give real speedup. Capped because
# too many simultaneous decoders thrash a spinning disk; lower this if scanning
# a slow HDD feels worse, raise it on a fast SSD.
VIDEO_HASH_WORKERS = min(8, os.cpu_count() or 4)

# A frame whose grayscale standard deviation is below this is "low-entropy":
# near-black, near-white, or a flat solid color. Such frames carry almost no
# information and their perceptual hashes collide with each other, which used to
# make unrelated videos (both containing dark/letterboxed frames) match. We tag
# them and never let them drive a match.
LOW_ENTROPY_STD = 12.0


def _frame_is_informative(frame_bgr) -> bool:
    """True if a frame has enough visual detail to be trusted in matching."""
    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        return float(gray.std()) >= LOW_ENTROPY_STD
    except Exception:
        return True   # if we can't tell, don't discard it


# Popcount used by the hot O(n^2) comparison loop. int.bit_count() (Py 3.10+)
# is a single C call and is ~50-100x faster than imagehash's numpy-array
# subtraction, which is what made "comparing videos" take hours. We therefore
# represent every frame hash as a packed integer, not an imagehash object.
try:
    (0).bit_count
    def _popcount(x: int) -> int:
        return x.bit_count()
except AttributeError:                       # Python < 3.10 fallback
    def _popcount(x: int) -> int:
        return bin(x).count("1")


def _pack_hash(h) -> int:
    """Pack an imagehash (HxW bool array) into one big integer."""
    return int.from_bytes(np.packbits(h.hash.flatten()).tobytes(), "big")


# ── low-level frame hashing ─────────────────────────────────────────────────
def _open_video(path: Path):
    cap = cv2.VideoCapture(str(path))
    return cap


def hash_frame(frame_bgr):
    """(phash_int, dhash_int, informative) for a single BGR frame. Hashes are
    packed into integers so the comparison loop can use fast bit_count popcounts
    instead of slow per-call numpy operations. Small frames are upscaled first
    so the hash has enough detail to be meaningful."""
    info = _frame_is_informative(frame_bgr)
    h, w = frame_bgr.shape[:2]
    if w < 320 or h < 320:
        scale = max(320 / w, 320 / h)
        frame_bgr = cv2.resize(frame_bgr, (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_CUBIC)
    img = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    ph = imagehash.phash(img, hash_size=HASH_SIZE)
    dh = imagehash.dhash(img, hash_size=HASH_SIZE)
    return (_pack_hash(ph), _pack_hash(dh), info)


def probe_video(path: Path, n: int = VIDEO_FRAMES):
    """
    Open a video ONCE and return (frames, duration_sec, pixels).

    Previously the scan opened each file three times (frames, duration,
    resolution). Combining them into a single open roughly thirds the per-file
    seek cost — which dominates on spinning disks. Safe to run in a worker
    thread (no global-fd manipulation here; the caller wraps the whole pool in
    one stderr-suppression block).
    """
    try:
        cap = _open_video(path)
        if not cap.isOpened():
            cap.release()
            return ([], 0.0, 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        dur = (total / fps) if (fps > 0 and total > 0) else 0.0
        pixels = w * h
        frames = []
        if total >= 1:
            positions = [int(total * i / (n + 1)) for i in range(1, n + 1)]
            for pos in positions:
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ret, frame = cap.read()
                if ret:
                    frames.append(hash_frame(frame))
        cap.release()
        return (frames, dur, pixels)
    except Exception:
        return ([], 0.0, 0)


def sample_video_frames(path: Path, n: int):
    """Sample n evenly-spaced frames, return list of (phash, dhash) tuples."""
    try:
        cap = _open_video(path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total < 1:
            cap.release()
            return []
        frames = []
        positions = [int(total * i / (n + 1)) for i in range(1, n + 1)]
        for pos in positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ret, frame = cap.read()
            if ret:
                frames.append(hash_frame(frame))
        cap.release()
        return frames
    except Exception:
        return []


def sample_frames_at_timestamps(path: Path, timestamps_sec):
    """Sample frames at specific absolute timestamps (seconds). Bails out fast
    if the file opens slowly or reads stall, so one bad video can't hang a
    worker thread for long."""
    start = time.time()
    try:
        cap = _open_video(path)
        if not cap.isOpened():
            cap.release()
            return []
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frames = []
        for t in timestamps_sec:
            if time.time() - start > SEG_READ_BUDGET:
                break   # this video is too slow; use whatever we have
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
            ret, frame = cap.read()
            if ret:
                frames.append(hash_frame(frame))
        cap.release()
        return frames
    except Exception:
        return []


def get_duration(path: Path) -> float:
    try:
        cap = _open_video(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        return (total / fps) if fps > 0 else 0.0
    except Exception:
        return 0.0


def get_resolution(path: Path):
    """Return (pixels, w, h)."""
    try:
        cap = _open_video(path)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return (w * h, w, h)
    except Exception:
        return (0, 0, 0)


# ── distance functions ───────────────────────────────────────────────────────
def frame_dist(a, b):
    """Min of phash/dhash Hamming distance on packed-int hashes — if either
    hash says 'similar', trust it. Uses bit_count popcount (fast)."""
    return min(_popcount(a[0] ^ b[0]), _popcount(a[1] ^ b[1]))


def best_cross_frame_match(frames1, frames2, early_exit=None):
    """
    For each frame in the SHORTER list, find its closest frame in the other,
    and average those minimums. Cheap (cached frames only) content signal:
    a trim shares frames with its source (low score) even when the videos have
    different lengths/resolutions; unrelated videos score high. Used to prefilter
    pairs before the expensive file-opening segment search.

    Low-entropy frames (near-black/solid) are excluded on BOTH sides so a pair
    can't look similar just because each happens to contain a dark frame.

    early_exit: if the running average is already guaranteed to exceed this,
    bail out with 9999. Since the caller only checks `< TRIM_PREFILTER`, bailing
    at that bound is exact — it just skips finishing pairs that can't qualify.
    """
    f1 = [f for f in frames1 if f[2]]
    f2 = [f for f in frames2 if f[2]]
    if not f1 or not f2:
        return 9999
    short, long_ = (f1, f2) if len(f1) <= len(f2) else (f2, f1)
    limit = (early_exit * len(short)) if early_exit is not None else None
    total = 0
    for s in short:
        total += min(frame_dist(s, l) for l in long_)
        if limit is not None and total > limit:
            return 9999
    return total / len(short)


def vid_dist_global(frames1, frames2, early_exit=9999):
    """Average aligned-frame distance with early exit once over threshold.
    Aligned pairs where either side is a low-entropy frame are skipped so a
    black intro/outro can't inflate or deflate the score."""
    if not frames1 or not frames2:
        return 9999
    n = min(len(frames1), len(frames2))
    total = 0
    cnt = 0
    for i in range(n):
        if not (frames1[i][2] and frames2[i][2]):
            continue
        total += frame_dist(frames1[i], frames2[i])
        cnt += 1
        if total / cnt > early_exit:
            return 9999
    if cnt == 0:
        return 9999   # nothing informative to compare
    return total / cnt


def vid_dist_trim_aware(frames_long, frames_short):
    """Sliding window of the shorter frame list across the longer one.
    Low-entropy frames are skipped inside each window."""
    n_long, n_short = len(frames_long), len(frames_short)
    if n_long == 0 or n_short == 0:
        return 9999
    if n_short > n_long:
        return vid_dist_trim_aware(frames_short, frames_long)
    best = 9999
    for start in range(n_long - n_short + 1):
        total = 0
        cnt = 0
        for i in range(n_short):
            a, b = frames_long[start + i], frames_short[i]
            if not (a[2] and b[2]):
                continue
            total += frame_dist(a, b)
            cnt += 1
        if cnt == 0:
            continue
        best = min(best, total / cnt)
    return best


def vid_dist_combined(frames1, frames2, threshold):
    d = vid_dist_global(frames1, frames2, early_exit=threshold)
    if d <= threshold:
        return d
    return min(d, vid_dist_trim_aware(frames1, frames2))


def vid_dist_segment_search(p_short, p_long, dur_short, dur_long,
                            n_short=SEG_SHORT_FRAMES, n_long=SEG_LONG_FRAMES):
    """
    Detect whether p_short is a trimmed segment of p_long, at ANY offset and
    across resolutions. Samples the short video sparsely and the long video
    densely, then for each short frame takes its closest long frame and
    averages those minimum distances.

    Opens both files — caller must restrict this to a small candidate set.
    Returns a distance; unrelated videos score ~90+, true trims score <10.
    """
    if dur_short <= 0 or dur_long <= 0:
        return 9999
    if dur_short > dur_long:
        p_short, p_long = p_long, p_short
        dur_short, dur_long = dur_long, dur_short

    short_ts = [dur_short * (i + 1) / (n_short + 1) for i in range(n_short)]
    short_h = [f for f in sample_frames_at_timestamps(p_short, short_ts) if f[2]]
    if not short_h:
        return 9999
    long_ts = [dur_long * (i + 1) / (n_long + 1) for i in range(n_long)]
    long_h = [f for f in sample_frames_at_timestamps(p_long, long_ts) if f[2]]
    if not long_h:
        return 9999

    total = 0
    for sh in short_h:
        total += min(frame_dist(sh, lh) for lh in long_h)
    return total / len(short_h)


# ── main grouping ─────────────────────────────────────────────────────────────
def find_video_duplicates(paths, hashed=None, res_cache=None, progress=None,
                          status=print):
    """
    Group near-duplicate videos.

    paths    : list of video Paths
    hashed   : optional {Path: (frames, dur)} precomputed hashes
    res_cache: optional {Path: pixels} precomputed resolutions
    Returns list of groups (each a list of 2+ Paths).
    """
    paths = list(paths)
    if hashed is None:
        hashed = {}
    if res_cache is None:
        res_cache = {}

    # Ensure frames + duration + resolution for each video. This used to be a
    # serial loop opening each file three times — the dominant cost on large
    # libraries. Now each file is opened ONCE (probe_video) and the whole pass
    # runs across a thread pool. The pool is wrapped in a single stderr
    # suppression block (fd redirection is process-global, so it must NOT be
    # done per-thread).
    to_hash = [p for p in paths if p not in hashed]
    if to_hash:
        done = 0
        with _suppress_native_stderr():
            with ThreadPoolExecutor(max_workers=VIDEO_HASH_WORKERS) as ex:
                futs = {ex.submit(probe_video, p): p for p in to_hash}
                for fut in as_completed(futs):
                    p = futs[fut]
                    try:
                        frames, dur, pixels = fut.result()
                    except Exception:
                        frames, dur, pixels = [], 0.0, 0
                    hashed[p] = (frames, dur)
                    res_cache[p] = pixels
                    done += 1
                    if progress and (done % 10 == 0 or done == len(to_hash)):
                        progress(done, len(to_hash), "hashing videos")
    if progress:
        progress(len(paths), len(paths), "hashing videos")

    items = []   # (path, frames, dur)
    for p in paths:
        frames, dur = hashed.get(p, ([], 0.0))
        if frames:
            items.append((p, frames, dur))

    n = len(items)
    if n < 2:
        return []

    # resolutions
    px = []
    for p, _, _ in items:
        if p in res_cache:
            px.append(res_cache[p])
        else:
            pixels = get_resolution(p)[0]
            res_cache[p] = pixels
            px.append(pixels)

    # Duration pre-filter: sort by duration, skip only the most hopeless pairs.
    dur_indexed = sorted([(items[i][2], i) for i in range(n)], key=lambda x: x[0])
    candidate_pairs = []
    for a in range(n):
        dur_a, idx_a = dur_indexed[a]
        for b in range(a + 1, n):
            dur_b, idx_b = dur_indexed[b]
            if dur_a > 0 and dur_b > 0:
                ratio = dur_a / dur_b if dur_b > 0 else 0
                if ratio < 0.03:        # one clip under 3% the other's length
                    break
            candidate_pairs.append((idx_a, idx_b))

    matches = [set() for _ in range(n)]
    trim_candidates = []
    total_pairs = len(candidate_pairs)
    t0 = time.time()

    for k, (i, j) in enumerate(candidate_pairs):
        p_a, frames_i, dur_i = items[i]
        p_b, frames_j, dur_j = items[j]

        dur_ratio = (min(dur_i, dur_j) / max(dur_i, dur_j)
                     if dur_i > 0 and dur_j > 0 else 1.0)
        px_ratio = (min(px[i], px[j]) / max(px[i], px[j])
                    if px[i] > 0 and px[j] > 0 else 1.0)
        is_cross_res = px_ratio < 0.35

        # Effective threshold — content signals only (resolution + duration).
        # Inflation is kept modest: combined with low-entropy frame filtering,
        # the previous very-lenient bumps (up to +45) were what let unrelated
        # cross-resolution clips match. These tighter values keep real
        # cross-res copies catchable without re-opening the door to those.
        eff = VIDEO_THR
        if px_ratio < 0.12:
            eff += 18
        elif px_ratio < 0.2:
            eff += 13
        elif px_ratio < 0.35:
            eff += 9
        elif px_ratio < 0.6:
            eff += 5
        diff = abs(dur_i - dur_j)
        if diff <= 2:
            eff += 8
        elif diff <= 5:
            eff += 5
        elif diff <= 15 and is_cross_res:
            eff += 3

        # Hot loop: cached frames only, no file I/O
        d = vid_dist_combined(frames_i, frames_j, eff)
        if d <= eff:
            matches[i].add(j)
            matches[j].add(i)
        else:
            # Route near-miss pairs into the segment-search pass, but ONLY if a
            # cheap cached-frame content check suggests they actually share
            # footage. With 16 frames per video, best_cross_frame_match cleanly
            # separates real trims (~50-65) from unrelated pairs (~95+), so this
            # cuts millions of structural candidates down to a few thousand real
            # ones — without opening a single file here.
            structural = False
            if is_cross_res and 0.05 <= dur_ratio < 0.97:
                structural = True
            elif px_ratio < 0.6 and 0.80 <= dur_ratio <= 1.0:
                structural = True
            elif (not is_cross_res) and 0.10 <= dur_ratio < 0.85:
                structural = True

            if structural:
                bfm = best_cross_frame_match(frames_i, frames_j,
                                             early_exit=TRIM_PREFILTER)
                if bfm < TRIM_PREFILTER:
                    trim_candidates.append((i, j, eff, bfm))

        if progress and (k % 5000 == 0 or k == total_pairs - 1):
            progress(k + 1, total_pairs, "comparing videos")

    # Second pass: segment search on near-miss candidates (parallel, capped).
    # Candidates already passed the cached content prefilter, so the list is
    # small. Sort by best-frame-match (most trim-like first) and check up to the
    # cap. Each check has a per-video read budget so bad files can't hang it.
    if trim_candidates:
        trim_candidates.sort(key=lambda t: t[3])   # lowest best-frame-match first
        if len(trim_candidates) > SEGMENT_SEARCH_CAP:
            status(f"  Trim check: {len(trim_candidates):,} candidates, "
                   f"checking the {SEGMENT_SEARCH_CAP:,} most similar...")
            trim_candidates = trim_candidates[:SEGMENT_SEARCH_CAP]
        else:
            status(f"  Trim check: segment-searching "
                   f"{len(trim_candidates):,} candidates...")

        def _check(c):
            i, j, eff, _d = c
            p_a, _, dur_a = items[i]
            p_b, _, dur_b = items[j]
            dd = vid_dist_segment_search(p_a, p_b, dur_a, dur_b)
            return (i, j) if dd <= eff else None

        found = 0
        done = 0
        total_c = len(trim_candidates)
        with _suppress_native_stderr():
            with ThreadPoolExecutor(max_workers=HASH_WORKERS) as ex:
                futures = {ex.submit(_check, c): c for c in trim_candidates}
                for fut in as_completed(futures):
                    done += 1
                    try:
                        res = fut.result(timeout=SEG_CHECK_TIMEOUT)
                    except Exception:
                        res = None   # timeout or decode error — skip this pair
                    if res is not None:
                        matches[res[0]].add(res[1])
                        matches[res[1]].add(res[0])
                        found += 1
                    if progress and (done % 50 == 0 or done == total_c):
                        progress(done, total_c, "trim check")
        status(f"  Trim check complete: {found} additional duplicate(s).")

    # Build groups: best-quality file as anchor, attach its direct matches
    def quality(idx):
        return (px[idx], items[idx][2])

    order = sorted(range(n), key=quality, reverse=True)
    used = set()
    groups = []
    for anchor in order:
        if anchor in used or not matches[anchor]:
            used.add(anchor)
            continue
        grp = [anchor] + [j for j in matches[anchor] if j not in used]
        if len(grp) > 1:
            groups.append([items[k][0] for k in grp])
            used.update(grp)
        else:
            used.add(anchor)

    return groups
