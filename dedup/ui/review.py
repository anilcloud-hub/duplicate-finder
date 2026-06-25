"""
Review UI — a local Flask app to review and act on detected duplicates.

Reads dedup_data.json (written by dedup.main) and serves a dark-themed web page
showing each duplicate group with:
  - images: inline thumbnails
  - videos: inline <video> players
  - other (PDF/Word/etc): a file card with name + size

The user ticks the files to remove. On confirm, selected files are MOVED into
the _REVIEW_DUPLICATES folder (not hard-deleted), preserving relative paths so
nothing is lost and the action is reversible by hand.

Run:
    python -m dedup.ui.review "D:/path/to/folder"
The folder must already contain a dedup_data.json from a scan.
"""

import json
import os
import shutil
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_file, abort

app = Flask(__name__)
STATE = {"root": None, "data": None}


@app.after_request
def _no_cache(resp):
    # The review page and its data change every scan. Never let the browser
    # serve a stale copy — otherwise a fixed/updated UI can keep showing the
    # old cached page (a frequent "still stuck on Loading" cause).
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Duplicate Finder — Review</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #14130f; color: #e8e6df;
         font-family: -apple-system, system-ui, sans-serif; }
  header { position: sticky; top: 0; z-index: 10; background: #1d1c17;
           border-bottom: 1px solid #34322b; padding: 14px 20px;
           display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 17px; margin: 0; font-weight: 500; }
  header .meta { color: #8f8d84; font-size: 13px; }
  header .spacer { flex: 1; }
  button { font: inherit; border: 0; border-radius: 8px; padding: 9px 16px;
           cursor: pointer; }
  .primary { background: #c2603a; color: #fff; }
  .primary:hover { background: #d06f48; }
  .danger { background: #8a2a2a; color: #fff; }
  .danger:hover { background: #a33; }
  .ghost { background: #2a2823; color: #d8d6cd; }
  .ghost:hover { background: #34322b; }
  main { padding: 20px; max-width: 1100px; margin: 0 auto; }
  .group { background: #1b1a15; border: 1px solid #34322b; border-radius: 12px;
           margin-bottom: 18px; overflow: hidden; }
  .group-head { padding: 10px 14px; border-bottom: 1px solid #2a2823;
                display: flex; align-items: center; gap: 10px; font-size: 13px; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 20px;
           text-transform: uppercase; letter-spacing: .04em; }
  .badge.exact { background: #163a2b; color: #6fdca8; }
  .badge.image { background: #14304f; color: #79b6e8; }
  .badge.video { background: #3a1f14; color: #e89a7b; }
  .files { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px,1fr));
           gap: 12px; padding: 14px; }
  .file { background: #14130f; border: 1px solid #2a2823; border-radius: 10px;
          padding: 10px; display: flex; flex-direction: column; gap: 8px; }
  .file.keep { outline: 2px solid #2f6f4f; }
  .file.remove { outline: 2px solid #a33; opacity: .7; }
  .thumb { width: 100%; height: 140px; object-fit: contain; background: #000;
           border-radius: 6px; }
  .clickable { cursor: pointer; }
  .fileinfo { font-size: 12px; color: #b6b4ab; word-break: break-all; }
  .fileinfo .nm { color: #e8e6df; }
  .fileinfo .sz { color: #8f8d84; }
  .row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .doc-icon { width: 100%; height: 140px; display: flex; align-items: center;
              justify-content: center; background: #000; border-radius: 6px;
              font-size: 40px; color: #5a5852; }
  .vidwrap { position: relative; padding: 0; height: 140px; }
  .playbtn { position: absolute; top: 8px; right: 8px; width: 34px; height: 34px;
             display: flex; align-items: center; justify-content: center;
             font-size: 16px; color: #fff; background: rgba(0,0,0,.55);
             border-radius: 50%; cursor: pointer; }
  .playbtn:hover { background: rgba(0,0,0,.8); }
  .empty { text-align: center; color: #8f8d84; padding: 60px; }
  .pager { display: flex; align-items: center; justify-content: center; gap: 16px;
           padding: 14px; color: #8f8d84; font-size: 13px; }
  .pager button[disabled] { opacity: .4; cursor: default; }
  #toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
           background: #2a2823; border: 1px solid #34322b; padding: 12px 20px;
           border-radius: 8px; display: none; }
</style></head>
<body>
<header>
  <h1>Duplicate Finder</h1>
  <span class="meta" id="meta"></span>
  <span class="spacer"></span>
  <span class="meta" id="count">0 selected</span>
  <button class="ghost" onclick="selectExtras()">Select / clear smaller copies</button>
  <button class="ghost" onclick="doAction('move')">Move to review folder</button>
  <button class="danger" onclick="doAction('delete')">Delete permanently</button>
</header>
<main id="main"><div class="empty">Loading…</div></main>
<div id="toast"></div>
<script>
let DATA = null;
const selected = new Set();   // keys "g:i" marked for removal

async function load() {
  const main = document.getElementById('main');
  try {
    const r = await fetch('/api/data');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    DATA = await r.json();
  } catch (e) {
    main.innerHTML = '<div class="empty">Could not load results: ' + e.message +
                     '<br><br>Try re-running the scan.</div>';
    return;
  }
  if (!DATA || !Array.isArray(DATA.groups)) {
    main.innerHTML = '<div class="empty">No results found. Try re-running the scan.</div>';
    return;
  }
  const c = DATA.counts || {images:0, videos:0, other:0};
  document.getElementById('meta').textContent =
    `${DATA.groups.length} duplicate groups · ${c.images||0} images, ${c.videos||0} videos, ${c.other||0} other`;
  try {
    render();
  } catch (e) {
    main.innerHTML = '<div class="empty">Error displaying results: ' + e.message + '</div>';
  }
}

function key(g,i){ return g+':'+i; }

// Infinite scroll: render groups in batches as the user scrolls down, instead
// of paginating. Previews are loading="lazy", so only thumbnails near the
// viewport ever hit the server. Appending in batches keeps the DOM from
// ballooning all at once on very large scans (thousands of groups).
const BATCH = 60;       // groups appended per scroll step
let rendered = 0;       // how many groups are currently in the DOM
let io = null;          // IntersectionObserver watching the bottom sentinel

function buildGroup(gi) {
  const grp = DATA.groups[gi];
  const div = document.createElement('div');
  div.className = 'group';
  const files = grp.files.map((f, fi) => fileCard(grp, gi, fi, f)).join('');
  div.innerHTML = `
      <div class="group-head">
        <span>${groupTitle(grp)}</span>
        <span class="spacer" style="flex:1"></span>
        <span style="color:#8f8d84">${grp.files.length} copies</span>
      </div>
      <div class="files">${files}</div>`;
  return div;
}

function appendBatch() {
  const main = document.getElementById('main');
  const sentinel = document.getElementById('sentinel');
  const start = rendered;
  const end = Math.min(start + BATCH, DATA.groups.length);
  const frag = document.createDocumentFragment();
  for (let gi = start; gi < end; gi++) frag.appendChild(buildGroup(gi));
  if (sentinel) main.insertBefore(frag, sentinel); else main.appendChild(frag);
  rendered = end;

  const total = DATA.groups.length;
  if (sentinel) {
    sentinel.textContent = rendered >= total
      ? `All ${total} groups loaded.`
      : `Showing ${rendered} of ${total} groups — scroll for more…`;
  }
  if (rendered >= total && io) { io.disconnect(); io = null; }
}

function render() {
  const main = document.getElementById('main');
  if (io) { io.disconnect(); io = null; }
  if (!DATA.groups.length) { main.innerHTML = '<div class="empty">No duplicates found 🎉</div>'; return; }
  main.innerHTML = '';
  rendered = 0;

  const sentinel = document.createElement('div');
  sentinel.id = 'sentinel';
  sentinel.className = 'pager';
  main.appendChild(sentinel);

  appendBatch();   // first batch

  // Load the next batch a bit before the sentinel actually enters the viewport,
  // so new groups are ready by the time the user reaches them.
  io = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) appendBatch();
  }, { rootMargin: '800px' });
  io.observe(sentinel);
}

function groupTitle(grp) {
  // Friendly, plain-language descriptions instead of technical jargon.
  if (grp.kind === 'exact') return 'Identical copies of the same file';
  if (grp.kind === 'image') return 'The same photo (different size or quality)';
  if (grp.kind === 'video') return 'The same video (trimmed or different quality)';
  return 'Duplicate files';
}

function detectType(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  const img = ['jpg','jpeg','png','gif','bmp','tiff','tif','webp','heic','heif','jfif',
               'arw','cr2','cr3','nef','dng','raf','orf','rw2'];
  const vid = ['mp4','mov','avi','mkv','wmv','flv','webm','m4v','mpg','mpeg','3gp','m2ts','mts','ts'];
  if (img.includes(ext)) return 'image';
  if (vid.includes(ext)) return 'video';
  return 'other';
}

function fileCard(grp, gi, fi, f) {
  const k = key(gi, fi);
  const isRemove = selected.has(k);
  const enc = encodeURIComponent(f.path);
  // Preview is based on the FILE's own type, not the group kind. Prefer the
  // ftype the scanner stored; if it's missing (older data) or the group kind
  // is "exact", fall back to detecting type from the file extension.
  let ftype = f.ftype;
  if (!ftype || ftype === 'exact') ftype = detectType(f.name);
  let preview;
  if (ftype === 'image') {
    preview = `<img class="thumb clickable" loading="lazy" src="/thumb?kind=image&path=${enc}"
                 onclick="toggle('${k}')"
                 onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'doc-icon',textContent:'⚠'}))">`;
  } else if (ftype === 'video') {
    preview = `<div class="thumb vidwrap">
                 <img class="thumb clickable" loading="lazy" src="/thumb?kind=video&path=${enc}"
                      onclick="toggle('${k}')"
                      onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'doc-icon',textContent:'▶'}))">
                 <div class="playbtn" onclick="event.stopPropagation();playVideo(this.parentNode,'${enc}')">▶</div>
               </div>`;
  } else {
    preview = `<div class="doc-icon clickable" onclick="toggle('${k}')">▤</div>`;
  }
  return `
    <div class="file ${isRemove ? 'remove' : 'keep'}" id="card-${k}">
      ${preview}
      <div class="fileinfo">
        <div class="nm">${f.name}</div>
        <div class="sz">${fileMeta(f, ftype)}</div>
      </div>
      <label class="row">
        <input type="checkbox" ${isRemove ? 'checked' : ''} onchange="toggle('${k}', this.checked)">
        Remove this copy
      </label>
    </div>`;
}

function fileMeta(f, ftype) {
  const parts = [f.size_mb + ' MB'];
  if (f.w && f.h) parts.push(f.w + '×' + f.h);
  if (ftype === 'video' && f.duration) parts.push(fmtDuration(f.duration));
  return parts.join(' · ');
}

function fmtDuration(sec) {
  sec = Math.round(sec);
  const m = Math.floor(sec / 60), s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function playVideo(wrap, enc) {
  const v = document.createElement('video');
  v.className = 'thumb';
  v.controls = true; v.autoplay = true; v.src = '/file?path=' + enc;
  wrap.replaceWith(v);
}

function toggle(k, forceState) {
  const on = (typeof forceState === 'boolean') ? forceState : !selected.has(k);
  if (on) selected.add(k); else selected.delete(k);
  const card = document.getElementById('card-' + k);
  card.classList.toggle('remove', on);
  card.classList.toggle('keep', !on);
  const cb = card.querySelector('input[type=checkbox]');
  if (cb) cb.checked = on;
  updateCount();
}

// Update the visual state (outline + checkbox) of the cards currently in the
// DOM to match the `selected` set, WITHOUT rebuilding the page. This preserves
// scroll position. Groups not yet scrolled into view are built later with the
// correct state because fileCard() reads `selected` directly.
function refreshVisible() {
  document.querySelectorAll('[id^="card-"]').forEach(card => {
    const k = card.id.slice(5);   // "card-<gi>:<fi>" -> "<gi>:<fi>"
    const on = selected.has(k);
    card.classList.toggle('remove', on);
    card.classList.toggle('keep', !on);
    const cb = card.querySelector('input[type=checkbox]');
    if (cb) cb.checked = on;
  });
}

// Toggle: if nothing is auto-selected yet, select every copy except the
// largest in each group (keeping the best quality). If they're already
// selected, clear the selection. One button does both.
function selectExtras() {
  // count how many "extras" exist (all-but-largest across groups)
  let extras = 0;
  DATA.groups.forEach(grp => { extras += Math.max(0, grp.files.length - 1); });
  const allExtrasSelected = (selected.size >= extras && extras > 0);

  if (allExtrasSelected) {
    selected.clear();
    showToast('Selection cleared');
  } else {
    selected.clear();
    DATA.groups.forEach((grp, gi) => {
      grp.files.forEach((f, fi) => { if (fi > 0) selected.add(key(gi, fi)); });
    });
    showToast('Selected all but the largest copy in each group');
  }
  refreshVisible();   // in place — keeps your scroll position
  updateCount();
}

function updateCount() {
  const el = document.getElementById('count');
  if (el) el.textContent = selected.size + ' selected';
}

async function doAction(mode) {
  if (!selected.size) { showToast('Nothing selected'); return; }
  let msg, endpoint;
  if (mode === 'delete') {
    msg = `Permanently DELETE ${selected.size} file(s)? This cannot be undone.`;
    endpoint = '/api/delete';
  } else {
    msg = `Move ${selected.size} file(s) into the review folder? This is reversible — files are moved, not deleted.`;
    endpoint = '/api/remove';
  }
  if (!confirm(msg)) return;
  const paths = [];
  selected.forEach(k => {
    const [gi, fi] = k.split(':').map(Number);
    paths.push(DATA.groups[gi].files[fi].path);
  });
  const r = await fetch(endpoint, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ paths })
  });
  const res = await r.json();
  const verb = mode === 'delete' ? 'Deleted' : 'Moved';
  showToast(`${verb} ${res.done} file(s). ${res.failed ? res.failed + ' failed.' : ''}`);
  selected.clear();
  await load();
  updateCount();
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}
load();
</script>
</body></html>"""


@app.route("/")
def index():
    return PAGE


@app.route("/api/data")
def api_data():
    return jsonify(STATE["data"])


def _is_within_root(p: Path, root: Path) -> bool:
    """
    True if p is inside root. Works on Windows where case and slash direction
    can differ between the stored path and the scanned root. We compare
    normalized, case-folded absolute paths via commonpath rather than
    relative_to, which is brittle across drive-letter casing and separators.
    """
    try:
        pa = os.path.normcase(os.path.abspath(str(p)))
        ra = os.path.normcase(os.path.abspath(str(root)))
        return os.path.commonpath([pa, ra]) == ra
    except (ValueError, OSError):
        return False


@app.route("/file")
def serve_file():
    """Serve the full original file (used by the video player)."""
    path = request.args.get("path", "")
    if not path:
        abort(400)
    p = Path(path)
    root = Path(STATE["root"])
    if not _is_within_root(p, root):
        print(f"[serve_file] refused (outside root): {path}")
        abort(403)
    if not p.is_file():
        print(f"[serve_file] not found on disk: {path}")
        abort(404)
    return send_file(os.path.abspath(str(p)))


@app.route("/thumb")
def serve_thumb():
    """
    Return a small JPEG preview for any image or video, generated server-side.
    This makes previews fast (no multi-MB downloads), and works for formats the
    browser can't display directly (HEIC, RAW-as-image, exotic video codecs).
    """
    path = request.args.get("path", "")
    kind = request.args.get("kind", "image")
    if not path:
        abort(400)
    p = Path(path)
    root = Path(STATE["root"])
    if not _is_within_root(p, root) or not p.is_file():
        abort(404)

    import io
    buf = io.BytesIO()
    try:
        if kind == "video":
            import cv2
            cap = cv2.VideoCapture(os.path.abspath(str(p)))
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 10))
            ok, frame = cap.read()
            cap.release()
            if not ok:
                abort(415)
            from PIL import Image
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        else:
            from PIL import Image, ImageOps
            img = Image.open(os.path.abspath(str(p)))
            img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((400, 400))
        img.save(buf, format="JPEG", quality=80)
        buf.seek(0)
    except Exception as e:
        print(f"[thumb] failed for {path}: {e}")
        abort(415)
    return send_file(buf, mimetype="image/jpeg")


@app.route("/api/remove", methods=["POST"])
def api_remove():
    """Move selected files into the review folder, preserving relative paths."""
    root = Path(STATE["root"])
    review = root / "_REVIEW_DUPLICATES"
    review.mkdir(exist_ok=True)
    paths = request.get_json(force=True).get("paths", [])
    moved, failed = 0, 0
    for path in paths:
        src = Path(path)
        if not _is_within_root(src, root):
            failed += 1
            continue
        if not src.is_file():
            failed += 1
            continue
        try:
            rel = Path(os.path.relpath(os.path.abspath(str(src)),
                                       os.path.abspath(str(root))))
            dest = review / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # avoid clobbering: if dest exists, add a numeric suffix
            if dest.exists():
                stem, suf = dest.stem, dest.suffix
                k = 1
                while (dest.parent / f"{stem}_{k}{suf}").exists():
                    k += 1
                dest = dest.parent / f"{stem}_{k}{suf}"
            shutil.move(str(src), str(dest))
            moved += 1
        except OSError:
            failed += 1
    _refresh_after_remove()
    return jsonify({"done": moved, "failed": failed})


@app.route("/api/delete", methods=["POST"])
def api_delete():
    """Permanently delete selected files (with the same in-root safety check)."""
    root = Path(STATE["root"])
    paths = request.get_json(force=True).get("paths", [])
    deleted, failed = 0, 0
    for path in paths:
        src = Path(path)
        if not _is_within_root(src, root) or not src.is_file():
            failed += 1
            continue
        try:
            src.unlink()
            deleted += 1
        except OSError:
            failed += 1
    _refresh_after_remove()
    return jsonify({"done": deleted, "failed": failed})


def _refresh_after_remove():
    data = STATE["data"]
    new_groups = []
    for grp in data["groups"]:
        remaining = [f for f in grp["files"] if Path(f["path"]).is_file()]
        if len(remaining) > 1:
            grp = dict(grp)
            grp["files"] = remaining
            new_groups.append(grp)
    data["groups"] = new_groups


def main():
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        folder = input("Enter the scanned folder path:\n> ").strip().strip('"')
    root = Path(folder).expanduser().resolve()
    data_file = root / "dedup_data.json"
    if not data_file.exists():
        print(f"No dedup_data.json in {root}. Run a scan first.")
        sys.exit(1)
    with open(data_file, "r", encoding="utf-8") as f:
        STATE["data"] = json.load(f)
    STATE["root"] = str(root)
    print("=" * 62)
    print("  Duplicate Finder — Review UI")
    print("=" * 62)
    print(f"Folder: {root}")
    print(f"Groups: {len(STATE['data']['groups'])}")

    url = "http://127.0.0.1:5000"
    # Open the browser automatically a moment after the server starts.
    import threading
    import webbrowser

    def _open():
        import time as _t
        _t.sleep(1.0)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open, daemon=True).start()
    print(f"\nOpening {url} in your browser...")
    print("If it doesn't open, paste that address into your browser.")
    print("Press Ctrl+C here to stop.\n")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    main()
