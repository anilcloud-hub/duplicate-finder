# Packaging Duplicate Finder for Windows & macOS

The app now has a desktop window (folder picker, Scan button, live progress,
then the review screen opens in the browser). Users never see Python or a
terminal.

## The one rule that decides everything

**PyInstaller does not cross-compile.** You build the Windows app *on* Windows
and the Mac app *on* a Mac. There is no way to produce a `.exe` from a Mac or a
`.app` from Windows. You have three ways to deal with this:

### Option 1 — Build on each machine yourself (simplest)
On a **Windows** PC: double-click `build_windows.bat`.
On a **Mac**: double-click `build_mac.command`.
Each script installs the dependencies and runs PyInstaller. Output appears in
`dist/`. You need access to both kinds of machine (or a friend with the other).

### Option 2 — Let GitHub build both for you, in the cloud (recommended)
You don't need to own a Mac. Push this project to a GitHub repo, then either
push a tag (`git tag v1.0.0 && git push --tags`) or open the **Actions** tab and
run **"Build apps"** manually. GitHub spins up a real Windows runner *and* a
real macOS runner, builds both, and gives you `DuplicateFinder-windows.zip` and
`DuplicateFinder-macos.zip` to download from the run's **Artifacts** section.
The workflow is already included at `.github/workflows/build.yml`.

### Option 3 — Ship Windows now, add Mac later
Most of your customers are likely on Windows. Build the `.exe` today (Option 1)
and add the Mac build when convenient.

## What you get

- **Windows:** `dist\DuplicateFinder\` — a folder containing `DuplicateFinder.exe`
  and its libraries. Zip the whole folder; the user unzips and runs the `.exe`.
- **macOS:** `dist/Duplicate Finder.app` — a normal clickable app bundle. Zip it
  (or wrap it in a `.dmg`) to distribute.

## macOS Gatekeeper (important for your customers)

An unsigned app downloaded from the internet is blocked by macOS on first run
("cannot be opened because the developer cannot be verified"). Two paths:

- **Free / no signing:** tell users to **right-click the app → Open → Open** the
  first time. After that it launches normally. Put this one line in your Gumroad
  description and they'll be fine.
- **Signed & notarized (polished):** requires an Apple Developer account
  ($99/year). You'd sign with your Developer ID and notarize the app so it opens
  with no warning. Worth it later; not required to start selling.

Windows may show a SmartScreen "unknown publisher" prompt — users click
**More info → Run anyway**. A code-signing certificate removes it but isn't
required.

## Notes

- **Size.** The bundle is ~450–500 MB, mostly SciPy and OpenCV. That's normal
  for a PyInstaller app with these libraries. If you want a much smaller
  download later, the biggest win is dropping SciPy: the perceptual hash only
  needs a DCT, which OpenCV (already bundled) can provide via `cv2.dct`. That's a
  small, self-contained change to the image hashing — ask and it can be done.
- **First launch** of a one-folder build is fast. (A one-*file* build is a single
  `.exe`/binary but unpacks to a temp dir on every launch, which is slow with
  libraries this size — that's why the spec uses one-folder.)
- **Python version:** build with 3.10+ (the comparison loop uses
  `int.bit_count()`, which is 3.10+; there's a fallback for older Pythons, but
  build on 3.10+ anyway).
- The headless mode `DuplicateFinder --scan "C:\path\to\folder"` runs a scan with
  no window — handy for testing a fresh build.
