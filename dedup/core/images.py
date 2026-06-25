"""
Image near-duplicate detection.

Catches the same picture saved at a different size, re-encoded to another
format (JPEG vs PNG vs WebP/HEIC), lightly edited, mirrored, rotated, or
cropped. Exact byte-identical copies are already handled by the exact layer;
this layer finds visually-equivalent images that differ in their bytes.

Technique
---------
1. Perceptual hashing: phash + dhash at hash_size 16. Robust to resolution,
   compression, and small edits. Two images match if the minimum of their
   phash/dhash Hamming distances is under PHOTO_THR.

2. Orientation invariance: each image is also hashed in its mirrored form; we
   compare against both so a flipped copy still matches.

3. Crop/edit tolerance: when two images are close but just over threshold and
   their aspect ratios differ, we compare a center-crop of the larger against
   the smaller. A cropped version of a photo lines up after the crop.

4. Transitive grouping: A~B and B~C puts A, B, C in one group, which matches
   how image quality/edits degrade gradually across versions.

RAW files (.arw, .cr2, ...) are not perceptually hashed here (they need
development first); they rely on the exact layer. They're still listed so the
caller can skip them cleanly.
"""

from pathlib import Path

import imagehash
from PIL import Image, ImageOps

# Match thresholds. Measured separation between "same photo, recompressed"
# (distance ~1-3) and "burst / very similar but different moment" (distance
# ~8-11). We sit comfortably below the burst range so consecutive shots are NOT
# grouped, while still catching WhatsApp-compressed copies of the same image.
PHOTO_THR = 6         # averaged phash/dhash distance for a match
CROP_THR = 10         # looser only when testing a crop hypothesis
HASH_SIZE = 16

# Extensions we will actually open with Pillow. RAW excluded on purpose.
PILLOW_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
               ".webp", ".jfif"}


def _open_rgb(path: Path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)   # honor camera rotation metadata
    return img.convert("RGB")


def hash_image(path: Path):
    """
    Return a dict of hashes for an image, or None if it can't be opened.
    {
      "phash": <ImageHash>, "dhash": <ImageHash>,
      "phash_m": <mirrored>, "dhash_m": <mirrored>,
      "w": int, "h": int
    }
    """
    if path.suffix.lower() not in PILLOW_EXTS:
        return None
    try:
        img = _open_rgb(path)
    except Exception:
        return None

    w, h = img.size
    mirror = ImageOps.mirror(img)
    return {
        "phash": imagehash.phash(img, hash_size=HASH_SIZE),
        "dhash": imagehash.dhash(img, hash_size=HASH_SIZE),
        "phash_m": imagehash.phash(mirror, hash_size=HASH_SIZE),
        "dhash_m": imagehash.dhash(mirror, hash_size=HASH_SIZE),
        "w": w, "h": h,
    }


def _hash_pair_distance(a, b):
    """
    Distance over phash+dhash, considering b's mirror. We AVERAGE phash and
    dhash rather than taking the min: a single hash can coincidentally collide
    on two different burst shots, but both rarely do at once, so the average is
    a much more reliable "same image" signal. The mirror is tried separately so
    a flipped copy still matches.
    """
    d_normal = ((a["phash"] - b["phash"]) + (a["dhash"] - b["dhash"])) / 2
    d_mirror = ((a["phash"] - b["phash_m"]) + (a["dhash"] - b["dhash_m"])) / 2
    return min(d_normal, d_mirror)


def _center_crop_hash(path: Path, target_aspect: float):
    """
    Hash a center-crop of `path` matching target_aspect (w/h). Used to test
    whether a wider/taller image is a cropped version of another.
    """
    try:
        img = _open_rgb(path)
    except Exception:
        return None
    w, h = img.size
    cur = w / h
    if cur > target_aspect:
        # too wide -> crop width
        new_w = int(h * target_aspect)
        x0 = (w - new_w) // 2
        box = (x0, 0, x0 + new_w, h)
    else:
        # too tall -> crop height
        new_h = int(w / target_aspect)
        y0 = (h - new_h) // 2
        box = (0, y0, w, y0 + new_h)
    cropped = img.crop(box)
    return {
        "phash": imagehash.phash(cropped, hash_size=HASH_SIZE),
        "dhash": imagehash.dhash(cropped, hash_size=HASH_SIZE),
        "phash_m": imagehash.phash(ImageOps.mirror(cropped), hash_size=HASH_SIZE),
        "dhash_m": imagehash.dhash(ImageOps.mirror(cropped), hash_size=HASH_SIZE),
    }


def find_image_duplicates(paths, hashed=None, progress=None,
                          enable_crop=True):
    """
    Group near-duplicate images.

    paths  : iterable of image Paths (RAW will be skipped, returns no group)
    hashed : optional dict {Path: hash_dict} to reuse precomputed hashes
    Returns list of groups (each a list of 2+ Paths).
    """
    paths = list(paths)
    if hashed is None:
        hashed = {}

    # Ensure every path has a hash (or is marked unhashable as None)
    items = []
    for i, p in enumerate(paths):
        h = hashed.get(p)
        if h is None and p not in hashed:
            h = hash_image(p)
            hashed[p] = h
        if h is not None:
            items.append((p, h))
        if progress and i % 50 == 0:
            progress(i, len(paths), "hashing images")
    if progress:
        progress(len(paths), len(paths), "hashing images")

    n = len(items)
    adj = [[] for _ in range(n)]

    for i in range(n):
        pi, hi = items[i]
        for j in range(i + 1, n):
            pj, hj = items[j]
            d = _hash_pair_distance(hi, hj)
            match = d <= PHOTO_THR

            # Crop hypothesis: close-ish but not matched, different aspect ratio
            if not match and enable_crop:
                ai = hi["w"] / hi["h"] if hi["h"] else 1
                aj = hj["w"] / hj["h"] if hj["h"] else 1
                aspect_diff = abs(ai - aj) / max(ai, aj)
                if d <= PHOTO_THR + 8 and aspect_diff > 0.12:
                    # crop the larger-area image to the smaller's aspect
                    area_i = hi["w"] * hi["h"]
                    area_j = hj["w"] * hj["h"]
                    if area_i >= area_j:
                        ch = _center_crop_hash(pi, aj)
                        other = hj
                    else:
                        ch = _center_crop_hash(pj, ai)
                        other = hi
                    if ch is not None:
                        dc_normal = ((ch["phash"] - other["phash"]) +
                                     (ch["dhash"] - other["dhash"])) / 2
                        dc_mirror = ((ch["phash_m"] - other["phash"]) +
                                     (ch["dhash_m"] - other["dhash"])) / 2
                        if min(dc_normal, dc_mirror) <= CROP_THR:
                            match = True

            if match:
                adj[i].append(j)
                adj[j].append(i)

        if progress and i % 20 == 0:
            progress(i, n, "comparing images")
    if progress:
        progress(n, n, "comparing images")

    # Transitive grouping via BFS
    visited = [False] * n
    groups = []
    for start in range(n):
        if visited[start] or not adj[start]:
            visited[start] = True
            continue
        comp = []
        queue = [start]
        while queue:
            node = queue.pop()
            if visited[node]:
                continue
            visited[node] = True
            comp.append(node)
            queue.extend(adj[node])
        if len(comp) > 1:
            groups.append([items[k][0] for k in comp])

    return groups
