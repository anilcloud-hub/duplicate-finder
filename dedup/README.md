# Duplicate Finder

A complete duplicate detector for Windows that finds:

- **Exact duplicates** of *any* file type (photos, videos, PDFs, Word docs,
  audio, archives) — byte-for-byte identical copies, via SHA-256.
- **Near-duplicate images** — the same picture resized, re-encoded to another
  format, mirrored, rotated, lightly edited, or cropped.
- **Near-duplicate videos** — the same video trimmed, compressed, or saved at a
  different resolution (e.g. a 480p WhatsApp clip of a 1080p original).

Detection is **content-based**, not filename-based, so it works no matter how
files are named.

## First-time setup

You need Python 3.9+ installed (https://www.python.org/downloads/, tick
"Add Python to PATH" during install).

1. Double-click **`0_Install.bat`** once to install the required packages.

## Usage

1. Double-click **`1_Scan.bat`**. Enter or drag in the folder to scan.
   - The first scan hashes every file; later scans reuse a cache and are fast.
   - When the scan finishes, the review screen **opens automatically** in your
     browser. (If it doesn't, open http://127.0.0.1:5000 manually.)

2. In the review screen, each duplicate group shows previews.
   - **Click a preview** (or its checkbox) to mark that copy for removal.
   - Use **Auto-select smaller copies** to keep the largest in each group.
   - **Move to review folder**: moves selected files into `_REVIEW_DUPLICATES`
     (reversible — drag them back if you change your mind).
   - **Delete permanently**: deletes selected files for good (asks to confirm).

3. To re-open the review screen later without rescanning, double-click
   **`2_Review.bat`** and enter the same folder.

## How it works

```
All files ─► Layer 0: SHA-256 exact match (any type)
                 │
                 ├─► Images ─► perceptual hash + crop/mirror detection
                 └─► Videos ─► frame hashing (fast pass) +
                              dense per-frame segment search (trim pass)
```

The video trim pass only opens files for a small, capped set of promising
candidate pairs, and runs in parallel, so it stays fast even on large libraries.

## Notes

- RAW photos (.arw, .cr2, .nef, ...) are matched exactly but not perceptually
  (RAW needs developing before visual comparison is reliable).
- Nothing is ever deleted automatically. Files are only moved when you confirm.
