[README.md](https://github.com/user-attachments/files/29549194/README.md)
# Duplicate Finder

Find duplicate and near-duplicate **photos, videos, and documents** on your disk — then review them in a clean visual screen before deleting anything.

Duplicate Finder is a cross-platform desktop app built around one principle: **precision over recall.** It would rather miss a borderline match than wrongly flag two different files as duplicates, so you can trust what it shows you.

## Features

- **Photos, videos, and documents** in a single scan
- **Exact duplicates** of any file type via SHA-256
- **Near-duplicate images** via perceptual hashing (pHash + dHash), including crop and mirror detection
- **Near-duplicate videos** via frame hashing with trim/segment detection — catches a shorter clip of a longer video, and copies saved at a different resolution
- Separates **burst photos** from true duplicates, and catches re-saved / messaging-app copies
- **Zero-install:** everything is bundled — no FFmpeg, no extra downloads, no terminal
- **Visual review screen** (dark theme) with thumbnails, video playback, infinite scroll, and one-click select / move-to-review-folder / delete
- **Fast rescans:** frame hashes are cached, so re-scanning a folder is quick

## How it works

Every file first goes through a SHA-256 pass to catch byte-for-byte identical copies. Images then go through perceptual hashing; videos go through frame hashing plus a segment search that only opens files for promising trim candidates. Documents (PDF/Word) are matched exactly. Detection is content-based, not name-based — it does not matter what the files are called.

The video comparison packs each frame hash into an integer and compares pairs with fast bit-count operations, and it ignores low-information (near-black / solid) frames so that unrelated clips are not matched just because both contain dark frames. Together these let large libraries with millions of candidate pairs scan in minutes rather than hours.

## Download

Prebuilt apps for Windows and macOS are on the [Releases](../../releases) page.

- **Windows:** unzip and run `DuplicateFinder.exe`.
- **macOS:** unzip and open `Duplicate Finder.app`. The first time only, right-click the app and choose **Open** (it is an unsigned app).

## Run from source

Requires Python 3.10 or newer.

    pip install -r dedup/requirements.txt
    python -m dedup

There is also a headless mode that scans without opening a window:

    python run_app.py --scan "/path/to/folder"

## Build a standalone app yourself

- **Windows:** double-click `build_windows.bat`
- **macOS:** double-click `build_mac.command`

Each script installs the dependencies and runs PyInstaller; the finished app lands in `dist/`. See [PACKAGING.md](PACKAGING.md) for full details, including the GitHub Actions workflow that builds both platforms automatically in the cloud.

## Tech stack

Python · OpenCV · imagehash · Pillow · NumPy · SciPy · Flask

## License

Released under the MIT License — see [LICENSE](LICENSE).
