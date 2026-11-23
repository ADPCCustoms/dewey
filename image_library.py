"""Image Library - index, tag and search your images. Files are never moved or changed.
Requires: pip install pillow
"""
import gzip, hashlib, os, queue, re, shutil, sqlite3, subprocess, sys, threading, time
import tkinter as tk
from collections import Counter
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

try:  # optional: enables dragging folders / images onto the window
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = TkinterDnD = None

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
DB_PATH = BASE / "library.db"


def resource_path(name):
    """Files bundled into the exe (such as the icon) live in a temporary folder when frozen."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


THUMB, PAGE = 140, 60
SIZES = [80, 110, 140, 180, 220, 280]  # thumbnail sizes offered by the slider
WATCH_MS = 60_000  # how often watched folders are checked

SCHEMA = """
CREATE TABLE IF NOT EXISTS images(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, hash TEXT,
  taken TEXT, kind TEXT, prompt TEXT);
CREATE TABLE IF NOT EXISTS tags(
  image_id INTEGER, tag TEXT, UNIQUE(image_id, tag));
CREATE TABLE IF NOT EXISTS watch(folder TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS albums(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE, pos INTEGER);
CREATE TABLE IF NOT EXISTS album_images(
  album_id INTEGER, image_id INTEGER, UNIQUE(album_id, image_id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


SORT_MODES = [
    ("ym", "Year / Month   (e.g. 2026/11)"),
    ("y", "Year only   (e.g. 2026)"),
    ("kind", "Type   (Photos, AI Images, Other)"),
    ("kind_ym", "Type, then Year / Month"),
    ("tag", "Tag   (first tag; untagged go in 'Untagged')"),
    ("album", "Album   (a folder per album; images in none go in 'No album')"),
]
KIND_NAMES = {"photo": "Photos", "ai": "AI Images", "other": "Other"}


def safe_name(s):
    s = "".join("_" if c in '<>:"/\\|?*' else c for c in s).strip(" .")
    return s or "Untagged"


def unique_path(target):
    if not target.exists():
        return target
    n = 1
    while True:
        cand = target.with_name(f"{target.stem}_{n}{target.suffix}")
        if not cand.exists():
            return cand
        n += 1


THUMB_DIR = BASE / "thumbs"


def make_thumb(path, placeholder=True, size=THUMB):
    """PIL thumbnail for a file, read from / saved to the on-disk cache when possible."""
    try:
        st = os.stat(path)
    except OSError:
        return Image.new("RGB", (size, size), "#888888") if placeholder else None
    key = hashlib.md5(f"{path}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest()
    cache_file = THUMB_DIR / f"{key}.jpg"
    try:
        if cache_file.exists():
            return Image.open(cache_file).convert("RGB")
    except Exception:
        pass
    try:
        img = Image.open(path)
        img.draft("RGB", (size * 2, size * 2))  # much faster decoding for big JPEGs
        img = img.convert("RGB")
        img.thumbnail((size, size))
    except Exception:
        return Image.new("RGB", (size, size), "#888888") if placeholder else None
    try:
        THUMB_DIR.mkdir(exist_ok=True)
        img.save(cache_file, "JPEG", quality=85)
    except Exception:
        pass
    return img


def thumb_worker(items, token, state, q, size=THUMB):
    for iid, path in items:
        if state["token"] != token:  # a newer page was drawn, stop wasting effort
            return
        q.put((token, iid, path, make_thumb(path, size=size)))


popcount = int.bit_count if hasattr(int, "bit_count") else (lambda x: bin(x).count("1"))


def dhash(img):
    """64-bit 'difference hash': survives resizing, re-saving and small colour changes."""
    px = img.convert("L").resize((9, 8), Image.LANCZOS).tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def find_similar(items, t):
    """items: [(id, 64-bit hash)]. Groups of ids linked by Hamming distance <= t.
    Splits the hash into t+1 bands: hashes within t bits must share at least one identical band,
    so only images sharing a band are compared."""
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    bands = t + 1
    bounds = [round(i * 64 / bands) for i in range(bands + 1)]
    for b in range(bands):
        lo, width = bounds[b], bounds[b + 1] - bounds[b]
        mask = (1 << width) - 1
        buckets = {}
        for idx, (_, h) in enumerate(items):
            buckets.setdefault((h >> lo) & mask, []).append(idx)
        for members in buckets.values():
            for i in range(len(members) - 1):
                a = members[i]
                ha = items[a][1]
                for j in range(i + 1, len(members)):
                    c = members[j]
                    if popcount(ha ^ items[c][1]) <= t:
                        ra, rc = find(a), find(c)
                        if ra != rc:
                            parent[ra] = rc
    groups = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(items[idx][0])
    return sorted((g for g in groups.values() if len(g) > 1), key=len, reverse=True)


def similar_worker(db_path, threshold, q, cancel):
    """Background job: hash any images not yet analysed, then group look-alikes."""
    try:
        db = sqlite3.connect(db_path, timeout=30)
        todo = db.execute("SELECT id, path FROM images WHERE phash IS NULL").fetchall()
        q.put(("total", len(todo)))
        for n, (iid, path) in enumerate(todo, 1):
            if cancel.is_set():
                break
            h = ""
            if os.path.exists(path):
                img = make_thumb(path, placeholder=False)  # also warms the thumbnail cache
                if img is not None:
                    h = format(dhash(img), "016x")
            db.execute("UPDATE images SET phash=? WHERE id=?", (h, iid))
            if n % 25 == 0:
                db.commit()
                q.put(("progress", n))
        db.commit()
        if cancel.is_set():
            db.close()
            q.put(("done", True))
            return
        q.put(("searching",))
        items = []
        for iid, h, path in db.execute(
                "SELECT id, phash, path FROM images WHERE phash IS NOT NULL AND phash != ''"):
            v = int(h, 16)
            if 6 <= popcount(v) <= 58 and os.path.exists(path):  # skip blank / flat images
                items.append((iid, v))
        db.close()
        q.put(("groups", find_similar(items, int(threshold))))
        q.put(("done", False))
    except Exception as e:
        q.put(("error", str(e)))


RESTORE_DIR = BASE / "restore"
MAX_POINTS = 20  # restore points kept; older ones are pruned
BULK_MIN = 10    # bulk tag / rating / favourite changes on at least this many images make a point


def ensure_schema(db):
    db.executescript(SCHEMA)
    for col in ("fav INTEGER DEFAULT 0", "rating INTEGER DEFAULT 0", "phash TEXT"):
        try:
            db.execute(f"ALTER TABLE images ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # column already exists
    db.commit()


class RestoreStore:
    """Restore points: a snapshot of the library taken before each risky change, plus a log of
    every file the app moved, renamed or deleted. Kept in its own database so that restoring a
    snapshot never rewinds the restore-point list itself."""

    def __init__(self, lib, base=None):
        self.lib = lib
        self.base = Path(base) if base else RESTORE_DIR
        self.trash = self.base / "trash"
        self.base.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.base / "restore.db", timeout=30)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS points(
              id INTEGER PRIMARY KEY, created TEXT, label TEXT, kind TEXT, snapshot TEXT);
            CREATE TABLE IF NOT EXISTS moves(
              id INTEGER PRIMARY KEY, point_id INTEGER, op TEXT, old_path TEXT, new_path TEXT);
        """)

    @staticmethod
    def norm(p):
        return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))

    def in_trash(self, p):
        return self.norm(p).startswith(self.norm(self.trash) + os.sep)

    # ---- points
    def begin(self, label, kind="auto"):
        """Snapshot the library now. Returns the point id, or None if a snapshot could not be made."""
        self.lib.commit()
        cur = self.db.execute("INSERT INTO points(created,label,kind,snapshot) VALUES(?,?,?,?)",
                              (datetime.now().isoformat(timespec="seconds"), label, kind, ""))
        pid = cur.lastrowid
        snap, tmp = self.base / f"snap_{pid}.db.gz", self.base / f"snap_{pid}.tmp"
        try:
            dst = sqlite3.connect(tmp)
            self.lib.backup(dst)
            dst.close()
            with open(tmp, "rb") as f, gzip.open(snap, "wb", compresslevel=3) as g:
                shutil.copyfileobj(f, g)
            self.db.execute("UPDATE points SET snapshot=? WHERE id=?", (snap.name, pid))
            self.db.commit()
            return pid
        except Exception:
            self.db.execute("DELETE FROM points WHERE id=?", (pid,))
            self.db.commit()
            snap.unlink(missing_ok=True)
            return None
        finally:
            tmp.unlink(missing_ok=True)

    def log_move(self, pid, op, old, new):
        self.db.execute("INSERT INTO moves(point_id,op,old_path,new_path) VALUES(?,?,?,?)",
                        (pid, op, str(old), str(new)))
        self.db.commit()

    def end(self, pid, changed=True):
        if changed:
            self.prune()
        else:
            self.discard(pid)

    def discard(self, pid):
        row = self.db.execute("SELECT snapshot FROM points WHERE id=?", (pid,)).fetchone()
        if row and row[0]:
            (self.base / row[0]).unlink(missing_ok=True)
        self.db.execute("DELETE FROM moves WHERE point_id=?", (pid,))
        self.db.execute("DELETE FROM points WHERE id=?", (pid,))
        self.db.commit()

    def delete_points(self, ids, free_space=False):
        """Delete restore points by hand. Each deleted point's file moves are merged into the nearest
        older point, so the remaining points can still put those files back. Files waiting in the
        app trash go to the Windows Recycle Bin when free_space is set, or when no older point
        exists to bring them back. Returns (points deleted, files sent to the Recycle Bin)."""
        deleted = freed = 0
        for pid in sorted(set(ids)):
            row = self.db.execute("SELECT snapshot FROM points WHERE id=?", (pid,)).fetchone()
            if not row:
                continue
            prev = self.db.execute("SELECT id FROM points WHERE id<? ORDER BY id DESC LIMIT 1",
                                   (pid,)).fetchone()
            if free_space or prev is None:
                for mid, new in self.db.execute("SELECT id, new_path FROM moves WHERE point_id=?",
                                                (pid,)).fetchall():
                    if self.in_trash(new) and os.path.exists(new):
                        try:
                            to_recycle_bin(new)
                            self.db.execute("UPDATE moves SET op='recycled' WHERE id=?", (mid,))
                            freed += 1
                        except OSError:
                            pass
            if prev:
                self.db.execute("UPDATE moves SET point_id=? WHERE point_id=?", (prev[0], pid))
            else:
                self.db.execute("DELETE FROM moves WHERE point_id=?", (pid,))
            self.db.execute("DELETE FROM points WHERE id=?", (pid,))
            if row[0]:
                (self.base / row[0]).unlink(missing_ok=True)
            try:
                (self.trash / str(pid)).rmdir()
            except OSError:
                pass
            deleted += 1
        self.db.commit()
        return deleted, freed

    def points(self):
        return self.db.execute(
            "SELECT id, created, label, kind FROM points ORDER BY id DESC").fetchall()

    def summary(self, pid):
        rows = self.db.execute("SELECT op, COUNT(*) FROM moves WHERE point_id=? GROUP BY op", (pid,))
        return dict(rows.fetchall())

    def prune(self, keep=None):
        keep = MAX_POINTS if keep is None else keep
        old = self.db.execute("SELECT id, snapshot FROM points ORDER BY id DESC LIMIT -1 OFFSET ?",
                              (keep,)).fetchall()
        for pid, snap in old:
            # files still sitting in the app trash go to the Windows Recycle Bin, never silently destroyed
            for (new,) in self.db.execute("SELECT new_path FROM moves WHERE point_id=?",
                                          (pid,)).fetchall():
                if self.in_trash(new) and os.path.exists(new):
                    try:
                        to_recycle_bin(new)
                    except OSError:
                        pass
            self.db.execute("DELETE FROM moves WHERE point_id=?", (pid,))
            self.db.execute("DELETE FROM points WHERE id=?", (pid,))
            if snap:
                (self.base / snap).unlink(missing_ok=True)
            try:
                (self.trash / str(pid)).rmdir()
            except OSError:
                pass
        self.db.commit()

    # ---- trash
    def trash_file(self, pid, path):
        d = self.trash / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        dest = unique_path(d / Path(path).name)
        shutil.move(str(path), str(dest))  # instant on the same drive, copy + delete across drives
        self.log_move(pid, "trash", path, dest)
        return dest

    def trash_size(self):
        total = 0
        for dp, _, names in os.walk(self.trash):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(dp, n))
                except OSError:
                    pass
        return total

    def empty_trash(self):
        """Send everything in the app trash to the Windows Recycle Bin (no longer undoable here)."""
        n = 0
        for mid, new in self.db.execute("SELECT id, new_path FROM moves").fetchall():
            if self.in_trash(new) and os.path.exists(new):
                try:
                    to_recycle_bin(new)
                    self.db.execute("UPDATE moves SET op='recycled' WHERE id=?", (mid,))
                    n += 1
                except OSError:
                    pass
        self.db.commit()
        return n

    # ---- restoring
    def _ids_from(self, point_id):
        return [r[0] for r in self.db.execute(
            "SELECT id FROM points WHERE id>=? ORDER BY id DESC", (point_id,))]

    def revert_plan(self, point_id):
        """Dry run of undoing every point from point_id to the newest.
        Returns (point ids, [(from, to)] file moves, [(path, reason)] problems)."""
        ids = self._ids_from(point_id)
        added, removed = set(), set()

        def exists(p):
            n = self.norm(p)
            return n in added or (n not in removed and os.path.exists(p))

        steps, problems = [], []
        for pid in ids:
            for op, old, new in self.db.execute(
                    "SELECT op, old_path, new_path FROM moves WHERE point_id=? ORDER BY id DESC",
                    (pid,)).fetchall():
                if op == "recycled":
                    problems.append((old, "is in the Windows Recycle Bin, restore it from there"))
                elif not exists(new):
                    problems.append((old, f"the file is no longer at {new}"))
                elif exists(old):
                    problems.append((old, "something already exists at the original location"))
                else:
                    steps.append((new, old))
                    nn, no = self.norm(new), self.norm(old)
                    removed.add(nn)
                    added.discard(nn)
                    added.add(no)
                    removed.discard(no)
        return ids, steps, problems

    def revert(self, point_id, log_pid):
        """Really move the files back (newest change first), logging each move under log_pid."""
        done, problems = 0, []
        for pid in self._ids_from(point_id):
            for op, old, new in self.db.execute(
                    "SELECT op, old_path, new_path FROM moves WHERE point_id=? ORDER BY id DESC",
                    (pid,)).fetchall():
                if op == "recycled":
                    problems.append((old, "is in the Windows Recycle Bin, restore it from there"))
                elif not os.path.exists(new):
                    problems.append((old, f"the file is no longer at {new}"))
                elif os.path.exists(old):
                    problems.append((old, "something already exists at the original location"))
                else:
                    try:
                        os.makedirs(os.path.dirname(old), exist_ok=True)
                        shutil.move(new, old)
                        if log_pid is not None:
                            self.log_move(log_pid, "move", new, old)
                        done += 1
                    except OSError as e:
                        problems.append((old, str(e)))
        return done, problems

    def restore_library(self, point_id):
        """Put the library database back to the snapshot (app settings such as theme are kept)."""
        snap = self.db.execute("SELECT snapshot FROM points WHERE id=?", (point_id,)).fetchone()[0]
        self.lib.commit()
        settings = self.lib.execute("SELECT key, value FROM settings").fetchall()
        tmp = self.base / "restore_tmp.db"
        try:
            with gzip.open(self.base / snap, "rb") as g, open(tmp, "wb") as f:
                shutil.copyfileobj(g, f)
            src = sqlite3.connect(tmp)
            src.backup(self.lib)
            src.close()
        finally:
            tmp.unlink(missing_ok=True)
        ensure_schema(self.lib)  # the snapshot may predate newer columns
        self.lib.executemany("INSERT OR REPLACE INTO settings VALUES(?,?)", settings)
        self.lib.commit()


def file_hash(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def index_worker(sources, db_path, q, cancel, settle=0):
    """Runs in a background thread with its own database connection.
    sources: folders and/or image files. settle: skip files modified in the last N seconds
    (they may still be downloading); they are picked up on the next scan."""
    try:
        db = sqlite3.connect(db_path, timeout=30)
        known = {r[0] for r in db.execute("SELECT path FROM images")}
        files = []
        for src in sources:
            if cancel.is_set():
                break
            if os.path.isfile(src):
                if os.path.splitext(src)[1].lower() in EXTS:
                    files.append(str(Path(src)))
                continue
            for dirpath, _, names in os.walk(src):
                if cancel.is_set():
                    break
                for n in names:
                    if os.path.splitext(n)[1].lower() in EXTS:
                        files.append(str(Path(dirpath) / n))
        files = list(dict.fromkeys(files))
        q.put(("total", len(files)))
        added = skipped = failed = 0
        for n, f in enumerate(files, 1):
            if cancel.is_set():
                break
            if f in known:
                skipped += 1
            else:
                try:
                    if settle and time.time() - os.path.getmtime(f) < settle:
                        continue
                    p = Path(f)
                    taken, kind, prompt = file_info(p)
                    db.execute("INSERT OR IGNORE INTO images(path,hash,taken,kind,prompt) "
                               "VALUES(?,?,?,?,?)", (f, file_hash(p), taken, kind, prompt))
                    added += 1
                except Exception:
                    failed += 1
            if n % 25 == 0:
                db.commit()
                q.put(("progress", n, added))
        db.commit()
        db.close()
        q.put(("done", added, skipped, failed, cancel.is_set()))
    except Exception as e:
        q.put(("error", str(e)))


_P = "replace(path, '/', '\\')"
NAME_SQL = f"replace({_P}, rtrim({_P}, replace({_P}, '\\', '')), '')"
SORTS = {
    "Newest first": " ORDER BY taken DESC, id",
    "Oldest first": " ORDER BY taken ASC, id",
    "Name A–Z": f" ORDER BY {NAME_SQL} COLLATE NOCASE ASC, id",
    "Name Z–A": f" ORDER BY {NAME_SQL} COLLATE NOCASE DESC, id",
    "Highest rated": " ORDER BY rating DESC, fav DESC, taken DESC, id",
    "Favourites first": " ORDER BY fav DESC, rating DESC, taken DESC, id",
}


def to_recycle_bin(path):
    """Send a file to the Windows Recycle Bin (recoverable). Raises OSError on failure."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        op = SHFILEOPSTRUCTW()
        op.wFunc = 3  # FO_DELETE
        op.pFrom = str(Path(path).resolve()) + "\0"  # list must end with a double null
        op.fFlags = 0x40 | 0x10 | 0x4 | 0x400  # undo, no confirm, silent, no error UI
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0 or op.fAnyOperationsAborted:
            raise OSError("Could not move the file to the Recycle Bin")
    else:
        try:
            from send2trash import send2trash
        except ImportError:
            raise OSError("Install send2trash to use the Recycle Bin on this system")
        send2trash(str(path))


THEMES = {
    "light": dict(bg="#f0f0f0", fg="#000000", field="#ffffff", sel="#3b82f6", selfg="#ffffff",
                  btn="#e1e1e1", btn_active="#d0d0d0", border="#adadad", dim="#6d6d6d"),
    "dark": dict(bg="#1e1f22", fg="#e6e6e6", field="#2b2d31", sel="#3b82f6", selfg="#ffffff",
                 btn="#3a3d42", btn_active="#4a4d54", border="#3a3d42", dim="#8a8f98"),
}

COL_SEL, COL_FAV = "#3b82f6", "#f5c518"


def build_name(pattern, n, taken, kind, tag, stem):
    """Expand a rename pattern: {date} {type} {tag} {name} {n} {n:03}"""
    s = re.sub(r"\{n(?::(\d+))?\}", lambda m: str(n).zfill(int(m.group(1) or 0)), pattern)
    s = (s.replace("{date}", taken[:10]).replace("{type}", kind)
          .replace("{tag}", tag or "untagged").replace("{name}", stem))
    return safe_name(s)


# Markers some AI tools leave in a file's embedded metadata (IPTC/XMP "digital source type")
AI_MARKERS = (b"trainedAlgorithmicMedia", b"Made with Google AI")


def file_info(p):
    taken, kind, prompt = None, "other", None
    try:
        img = Image.open(p)
        raw = img.getexif().get(306)
        if raw:
            taken = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").isoformat()
            kind = "photo"
        meta = getattr(img, "text", {}) or {}
        prompt = meta.get("parameters") or meta.get("prompt")
        if prompt:
            kind = "ai"
    except Exception:
        pass
    if kind != "ai":
        try:
            with open(p, "rb") as f:
                head = f.read(512 * 1024)
            name = p.name.lower()
            if any(m in head for m in AI_MARKERS) or "gemini" in name or "generated_image" in name:
                kind = "ai"
        except OSError:
            pass
    taken = taken or datetime.fromtimestamp(p.stat().st_mtime).isoformat()
    return taken, kind, prompt


class App:
    def __init__(self, root):
        self.root = root
        root.title("Image Library")
        root.geometry("1280x740")
        self.db = sqlite3.connect(DB_PATH, timeout=30)
        ensure_schema(self.db)
        self.backup_library()
        try:
            self.restore = RestoreStore(self.db)
        except Exception:  # e.g. folder not writable: the app still works, just without restore points
            self.restore = None
        self.page, self.rows, self.total = 0, [], 0
        self.cache, self.cols = {}, 5
        self.normal_bg = root.cget("bg")
        self.selected, self.primary, self.anchor = set(), None, None
        self.sel_path, self.widgets = None, {}
        self.side_filter, self.side_items = ("all", None), []
        self.debounce = None
        self.placeholder = None
        self.thumb_state, self.thumb_q, self.thumb_left = {"token": 0}, queue.Queue(), set()
        self.index_job, self.index_thread = None, None
        self.default_theme = ttk.Style().theme_use()
        row = self.db.execute("SELECT value FROM settings WHERE key='theme'").fetchone()
        self.theme = row[0] if row and row[0] in THEMES else "light"
        row = self.db.execute("SELECT value FROM settings WHERE key='thumb_size'").fetchone()
        self.thumb_size = int(row[0]) if row and row[0].isdigit() and int(row[0]) in SIZES else THUMB
        self.apply_theme(self.theme, walk=False)
        self.build_ui()
        self.refresh()
        self.apply_theme(self.theme)
        self.setup_dnd()
        self.root.after(2000, self.watch_tick)

    # ---------- UI ----------
    def build_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="Add folder…", command=self.add_folder).pack(side="left")
        ttk.Label(top, text="  Search:").pack(side="left")
        self.search_var = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.search_var, width=24)
        e.pack(side="left")
        e.bind("<KeyRelease>", self.on_search_key)
        self.kind_var = tk.StringVar(value="all")
        cb = ttk.Combobox(top, textvariable=self.kind_var, width=8, state="readonly",
                          values=["all", "photo", "ai", "other"])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.reset_page())
        ttk.Button(top, text="Find duplicates", command=self.show_duplicates).pack(side="left")
        ttk.Button(top, text="Sort into folders…", command=self.sort_dialog).pack(side="left", padx=6)
        ttk.Label(top, text="Mark showing as:").pack(side="left")
        self.mark_var = tk.StringVar(value="ai")
        ttk.Combobox(top, textvariable=self.mark_var, width=6, state="readonly",
                     values=["photo", "ai", "other"]).pack(side="left", padx=4)
        ttk.Button(top, text="Apply", command=self.mark_showing).pack(side="left")
        self.status = ttk.Label(top, text="")
        self.status.pack(side="right")
        self.theme_btn = ttk.Button(top, text="Dark mode", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=8)

        top2 = ttk.Frame(self.root)
        top2.pack(fill="x", padx=8)
        ttk.Label(top2, text="Sort:").pack(side="left")
        self.sort_var = tk.StringVar(value="Newest first")
        sc = ttk.Combobox(top2, textvariable=self.sort_var, width=16, state="readonly",
                          values=list(SORTS))
        sc.pack(side="left", padx=4)
        sc.bind("<<ComboboxSelected>>", lambda e: self.reset_page())
        ttk.Button(top2, text="Clean up missing files…", command=self.cleanup_missing).pack(
            side="left", padx=(12, 4))
        ttk.Button(top2, text="Delete duplicates…", command=self.delete_duplicates_dialog).pack(
            side="left")
        ttk.Button(top2, text="Watch folders…", command=self.watch_dialog).pack(side="left", padx=4)
        ttk.Button(top2, text="Find similar…", command=self.similar_dialog).pack(side="left")
        ttk.Button(top2, text="Restore points…", command=self.restore_dialog).pack(side="left", padx=4)
        self.prog = ttk.Frame(top2)  # shown only while a folder is being indexed
        self.prog_label = ttk.Label(self.prog, text="")
        self.prog_label.pack(side="left", padx=6)
        self.prog_bar = ttk.Progressbar(self.prog, length=220, mode="determinate")
        self.prog_bar.pack(side="left")
        ttk.Button(self.prog, text="Cancel", command=self.cancel_index).pack(side="left", padx=6)

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=8)

        # browse sidebar (left)
        side = ttk.Frame(main, width=190)
        side.pack(side="left", fill="y", padx=(0, 8))
        side.pack_propagate(False)
        ttk.Label(side, text="Browse", font=("", 10, "bold")).pack(anchor="w")
        self.side_list = tk.Listbox(side, exportselection=False, activestyle="none")
        self.side_list.pack(fill="both", expand=True)
        self.side_list.bind("<<ListboxSelect>>", self.on_side_select)
        self.side_list.bind("<Button-3>", self.on_side_menu)

        # detail panel (right)
        d = ttk.Frame(main, width=310)
        d.pack(side="right", fill="y", padx=(8, 0))
        d.pack_propagate(False)
        self.preview = ttk.Label(d, text="Select an image", anchor="center")
        self.preview.pack(fill="x", pady=4)
        self.info = ttk.Label(d, text="", wraplength=290, justify="left")
        self.info.pack(fill="x")
        ttk.Label(d, text="Tags (comma separated):").pack(anchor="w", pady=(8, 0))
        self.tags_var = tk.StringVar()
        te = ttk.Entry(d, textvariable=self.tags_var)
        te.pack(fill="x")
        te.bind("<Return>", lambda e: self.save_tags())
        trow = ttk.Frame(d)
        trow.pack(fill="x", pady=4)
        ttk.Button(trow, text="Save", command=self.save_tags).pack(side="left")
        ttk.Button(trow, text="Add to all selected", command=self.add_tags_selected).pack(side="left", padx=4)
        ttk.Button(trow, text="Remove", command=self.remove_tags_selected).pack(side="left")
        frow = ttk.Frame(d)
        frow.pack(fill="x", pady=(0, 4))
        self.fav_var = tk.BooleanVar()
        self.rating_var = tk.StringVar(value="0")
        ttk.Checkbutton(frow, text="♥ Favourite", variable=self.fav_var,
                        command=self.apply_fav).pack(side="left")
        ttk.Label(frow, text="   Rating:").pack(side="left")
        rc = ttk.Combobox(frow, textvariable=self.rating_var, width=3, state="readonly",
                          values=["0", "1", "2", "3", "4", "5"])
        rc.pack(side="left")
        rc.bind("<<ComboboxSelected>>", lambda e: self.apply_rating())
        arow = ttk.Frame(d)
        arow.pack(fill="x", pady=(0, 4))
        self.album_btn = ttk.Menubutton(arow, text="Add to album ▾")
        self.album_menu = tk.Menu(self.album_btn, tearoff=0, postcommand=self.fill_album_menu)
        self.album_btn.config(menu=self.album_menu)
        self.album_btn.pack(side="left")
        ttk.Button(arow, text="Remove from album", command=self.remove_from_album).pack(side="left", padx=4)
        row = ttk.Frame(d)
        row.pack(fill="x")
        ttk.Button(row, text="View", command=lambda: self.open_viewer(self.primary)).pack(side="left")
        ttk.Button(row, text="Open", command=self.open_file).pack(side="left", padx=4)
        ttk.Button(row, text="Show in folder", command=self.show_in_folder).pack(side="left")
        prow = ttk.Frame(d)
        prow.pack(fill="x", pady=(8, 0))
        ttk.Label(prow, text="Prompt / metadata:").pack(side="left")
        ttk.Button(prow, text="Copy", command=self.copy_prompt).pack(side="right")
        self.prompt = tk.Text(d, height=8, wrap="word", state="disabled")
        self.prompt.pack(fill="both", expand=True)

        # grid (left)
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(left, highlightthickness=0)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>",
                             lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        pager = ttk.Frame(self.root)
        pager.pack(fill="x", pady=6)
        ttk.Button(pager, text="◀ Prev", command=lambda: self.change_page(-1)).pack(side="left", padx=8)
        self.page_label = ttk.Label(pager, text="")
        self.page_label.pack(side="left")
        ttk.Button(pager, text="Next ▶", command=lambda: self.change_page(1)).pack(side="left", padx=8)
        self.sel_label = ttk.Label(pager, text="")
        self.sel_label.pack(side="left", padx=12)
        ttk.Button(pager, text="Select page", command=self.select_page).pack(side="left")
        ttk.Button(pager, text="Clear selection", command=self.clear_selection).pack(side="left", padx=4)
        ttk.Button(pager, text="Rename…", command=self.rename_dialog).pack(side="left")
        ttk.Label(pager, text="Ctrl-click / Shift-click to select several").pack(side="right", padx=8)
        self.size_label = ttk.Label(pager, text=f"{self.thumb_size}px", width=6)
        self.size_label.pack(side="right")
        self.size_scale = ttk.Scale(pager, from_=0, to=len(SIZES) - 1, length=140,
                                    command=self.on_size_drag)
        self.size_scale.set(SIZES.index(self.thumb_size))
        self.size_scale.pack(side="right")
        self.size_scale.bind("<ButtonRelease-1>", self.on_size_release)
        ttk.Label(pager, text="Thumbnail size").pack(side="right", padx=6)
        self.canvas.bind_all("<Control-MouseWheel>", self.on_zoom_wheel)

    def on_resize(self, event):
        cols = max(1, event.width // (self.thumb_size + 16))
        if cols != self.cols:
            self.cols = cols
            self.render()

    def on_search_key(self, _):
        if self.debounce:
            self.root.after_cancel(self.debounce)
        self.debounce = self.root.after(300, self.reset_page)

    # ---------- data ----------
    def where(self):
        sql, params = " WHERE 1=1", []
        mode, val = self.side_filter
        if mode == "fav":
            sql += " AND fav=1"
        elif mode == "rated":
            sql += " AND rating>=4"
        elif mode == "untagged":
            sql += " AND id NOT IN (SELECT image_id FROM tags)"
        elif mode == "tag":
            sql += " AND id IN (SELECT image_id FROM tags WHERE tag=?)"
            params.append(val)
        elif mode == "album":
            sql += " AND id IN (SELECT image_id FROM album_images WHERE album_id=?)"
            params.append(val)
        if self.kind_var.get() != "all":
            sql += " AND kind=?"
            params.append(self.kind_var.get())
        for w in self.search_var.get().split():
            like = f"%{w}%"
            sql += (" AND (path LIKE ? OR IFNULL(prompt,'') LIKE ?"
                    " OR id IN (SELECT image_id FROM tags WHERE tag LIKE ?))")
            params += [like, like, like]
        return sql, params

    def reset_page(self):
        self.page = 0
        self.refresh()

    def change_page(self, delta):
        last = max(0, (self.total - 1) // PAGE)
        self.page = min(max(0, self.page + delta), last)
        self.refresh()

    def refresh(self):
        w, p = self.where()
        self.total = self.db.execute("SELECT COUNT(*) FROM images" + w, p).fetchone()[0]
        self.rows = self.db.execute(
            "SELECT id, path, fav FROM images" + w + SORTS[self.sort_var.get()] + " LIMIT ? OFFSET ?",
            p + [PAGE, self.page * PAGE]).fetchall()
        pages = max(1, (self.total + PAGE - 1) // PAGE)
        self.page_label.config(text=f"Page {self.page + 1} of {pages}  ({self.total} images)")
        self.render()
        self.selected &= {r[0] for r in self.rows}
        if self.primary not in self.selected:
            self.primary = next(iter(self.selected), None)
        self.update_selection()
        self.refresh_sidebar()

    def thumb_image(self, path):
        """Cached PhotoImage, or a grey placeholder until the background loader fills it in."""
        if path in self.cache:
            return self.cache[path]
        if self.placeholder is None:
            self.placeholder = ImageTk.PhotoImage(Image.new("RGB", (self.thumb_size, self.thumb_size), "#cccccc"))
        return self.placeholder

    def on_size_drag(self, value):
        n = min(max(int(round(float(value))), 0), len(SIZES) - 1)
        self.size_label.config(text=f"{SIZES[n]}px")

    def on_size_release(self, event=None):
        n = min(max(int(round(float(self.size_scale.get()))), 0), len(SIZES) - 1)
        self.size_scale.set(n)  # snap the handle to the nearest step
        self.set_thumb_size(SIZES[n])

    def on_zoom_wheel(self, event):
        if str(event.widget).startswith(str(self.canvas)):  # only while over the thumbnail grid
            self.step_size(1 if event.delta > 0 else -1)

    def step_size(self, delta):
        n = min(max(SIZES.index(self.thumb_size) + delta, 0), len(SIZES) - 1)
        self.size_scale.set(n)
        self.set_thumb_size(SIZES[n])

    def set_thumb_size(self, size):
        if size == self.thumb_size:
            return
        self.thumb_size = size
        self.db.execute("INSERT OR REPLACE INTO settings VALUES('thumb_size', ?)", (str(size),))
        self.db.commit()
        self.cache.clear()
        self.placeholder = None
        self.cols = max(1, self.canvas.winfo_width() // (size + 16))
        self.render()

    def start_thumbs(self):
        self.thumb_state["token"] += 1
        token = self.thumb_state["token"]
        todo = [(i, p) for i, p, _ in self.rows if p not in self.cache]
        self.thumb_left = {i for i, _ in todo}
        if not todo:
            return
        threading.Thread(target=thumb_worker, args=(todo, token, self.thumb_state, self.thumb_q, self.thumb_size),
                         daemon=True).start()
        self.root.after(30, lambda: self.poll_thumbs(token))

    def poll_thumbs(self, token):
        if token != self.thumb_state["token"]:
            return  # a newer page has been drawn
        got = 0
        try:
            while got < 12:  # a few at a time keeps the window responsive
                t, iid, path, img = self.thumb_q.get_nowait()
                if t != token:
                    continue
                self.thumb_left.discard(iid)
                w = self.widgets.get(iid)
                if w is None:
                    continue
                ph = ImageTk.PhotoImage(img)
                while len(self.cache) > 600:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[path] = ph
                w.config(image=ph)
                w.image = ph  # the label must keep its own reference
                got += 1
        except queue.Empty:
            pass
        if self.thumb_left:
            self.root.after(40, lambda: self.poll_thumbs(token))

    def render(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.widgets = {}
        for n, (iid, path, fav) in enumerate(self.rows):
            ph = self.thumb_image(path)
            lbl = tk.Label(self.grid_frame, image=ph, bd=0, padx=3, pady=3, cursor="hand2")
            lbl.image = ph
            lbl.grid(row=n // self.cols, column=n % self.cols, padx=3, pady=3)
            lbl.bind("<Button-1>", lambda e, i=iid: self.on_click(e, i))
            lbl.bind("<Double-Button-1>", lambda e, i=iid: self.open_viewer(i))
            self.widgets[iid] = lbl
        self.recolor()
        self.canvas.yview_moveto(0)
        self.start_thumbs()

    def recolor(self):
        for iid, _, fav in self.rows:
            w = self.widgets.get(iid)
            if w is not None:
                w.config(bg=COL_SEL if iid in self.selected
                         else COL_FAV if fav else self.normal_bg)

    # ---------- sidebar ----------
    def refresh_sidebar(self):
        def count(sql):
            return self.db.execute(sql).fetchone()[0]
        items = [
            ("All images", ("all", None), count("SELECT COUNT(*) FROM images")),
            ("♥ Favourites", ("fav", None), count("SELECT COUNT(*) FROM images WHERE fav=1")),
            ("★ Rated 4+", ("rated", None), count("SELECT COUNT(*) FROM images WHERE rating>=4")),
            ("Untagged", ("untagged", None),
             count("SELECT COUNT(*) FROM images WHERE id NOT IN (SELECT image_id FROM tags)")),
        ]
        for aid, name, n in self.db.execute(
                "SELECT a.id, a.name, COUNT(ai.image_id) FROM albums a "
                "LEFT JOIN album_images ai ON ai.album_id=a.id GROUP BY a.id ORDER BY a.pos, a.name"):
            items.append((f"▣ {name}", ("album", aid), n))
        for tag, n in self.db.execute("SELECT tag, COUNT(*) FROM tags GROUP BY tag ORDER BY tag"):
            items.append((f"# {tag}", ("tag", tag), n))
        self.side_items = items
        self.side_list.delete(0, "end")
        chosen = None
        for n, (label, key, cnt) in enumerate(items):
            self.side_list.insert("end", f"{label}  ({cnt})")
            if key == self.side_filter:
                chosen = n
        if chosen is None:
            self.side_filter, chosen = ("all", None), 0
        self.side_list.selection_set(chosen)

    def on_side_select(self, event=None):
        sel = self.side_list.curselection()
        if sel:
            self.side_filter = self.side_items[sel[0]][1]
            self.reset_page()

    # ---------- selection ----------
    def on_click(self, event, iid):
        ids = [r[0] for r in self.rows]
        shift, ctrl = event.state & 0x1, event.state & 0x4
        if shift and self.anchor in ids:
            a, b = sorted((ids.index(self.anchor), ids.index(iid)))
            self.selected = set(ids[a:b + 1])
        elif ctrl:
            self.selected ^= {iid}
            self.anchor = iid
        else:
            self.selected = {iid}
            self.anchor = iid
        if iid in self.selected:
            self.primary = iid
        else:
            self.primary = next(iter(self.selected), None)
        self.update_selection()

    def select_page(self):
        self.selected = {r[0] for r in self.rows}
        self.primary = self.rows[0][0] if self.rows else None
        self.update_selection()

    def clear_selection(self):
        self.selected, self.primary = set(), None
        self.update_selection()

    def update_selection(self):
        self.recolor()
        n = len(self.selected)
        self.sel_label.config(text=f"{n} selected" if n else "")
        self.show_detail(self.primary)

    def set_prompt(self, text):
        self.prompt.config(state="normal")
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", text)
        self.prompt.config(state="disabled")

    def show_detail(self, iid):
        if iid is None:
            self.sel_path = None
            self.preview.config(image="", text="Select an image")
            self.info.config(text="")
            self.tags_var.set("")
            self.fav_var.set(False)
            self.rating_var.set("0")
            self.set_prompt("")
            return
        path, taken, kind, prompt, fav, rating = self.db.execute(
            "SELECT path, taken, kind, prompt, fav, rating FROM images WHERE id=?",
            (iid,)).fetchone()
        self.sel_path = path
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((290, 260))
            self._preview_img = ImageTk.PhotoImage(img)
            self.preview.config(image=self._preview_img, text="")
        except Exception:
            self.preview.config(image="", text="(file missing)")
        extra = f"\n{len(self.selected)} images selected" if len(self.selected) > 1 else ""
        names = [r[0] for r in self.db.execute(
            "SELECT a.name FROM albums a JOIN album_images ai ON ai.album_id=a.id "
            "WHERE ai.image_id=? ORDER BY a.pos, a.name", (iid,))]
        if names:
            extra = f"\nAlbums: {', '.join(names)}" + extra
        self.info.config(text=f"{Path(path).name}\n{kind} · {taken[:10]}\n{path}{extra}")
        tags = [r[0] for r in self.db.execute(
            "SELECT tag FROM tags WHERE image_id=? ORDER BY tag", (iid,))]
        self.tags_var.set(", ".join(tags))
        self.fav_var.set(bool(fav))
        self.rating_var.set(str(rating or 0))
        self.set_prompt(prompt or "")

    # ---------- tags, favourites, ratings ----------
    def parse_tags(self):
        return {t.strip().lower() for t in self.tags_var.get().split(",") if t.strip()}

    def save_tags(self):
        """Replace the tags of the main (last clicked) image."""
        if self.primary is None:
            return
        self.db.execute("DELETE FROM tags WHERE image_id=?", (self.primary,))
        self.db.executemany("INSERT OR IGNORE INTO tags VALUES(?,?)",
                            [(self.primary, t) for t in self.parse_tags()])
        self.db.commit()
        self.status.config(text="Tags saved")
        self.after_tag_change()

    def add_tags_selected(self):
        tags = self.parse_tags()
        if not self.selected or not tags:
            return
        self.bulk_point(f"Add tags to {len(self.selected)} images")
        self.db.executemany("INSERT OR IGNORE INTO tags VALUES(?,?)",
                            [(i, t) for i in self.selected for t in tags])
        self.db.commit()
        self.status.config(text=f"Added to {len(self.selected)} images")
        self.after_tag_change()

    def remove_tags_selected(self):
        tags = self.parse_tags()
        if not self.selected or not tags:
            return
        self.bulk_point(f"Remove tags from {len(self.selected)} images")
        self.db.executemany("DELETE FROM tags WHERE image_id=? AND tag=?",
                            [(i, t) for i in self.selected for t in tags])
        self.db.commit()
        self.status.config(text=f"Removed from {len(self.selected)} images")
        self.after_tag_change()

    def after_tag_change(self):
        if self.side_filter[0] in ("tag", "untagged") or self.search_var.get().strip():
            self.refresh()
        else:
            self.refresh_sidebar()
            self.show_detail(self.primary)

    def apply_fav(self):
        if not self.selected:
            return
        self.bulk_point(f"Change favourite on {len(self.selected)} images")
        v = int(self.fav_var.get())
        ids = list(self.selected)
        self.db.execute(f"UPDATE images SET fav=? WHERE id IN ({','.join('?' * len(ids))})",
                        [v] + ids)
        self.db.commit()
        self.rows = [(i, p, v if i in self.selected else f) for i, p, f in self.rows]
        self.recolor()
        if self.side_filter[0] == "fav":
            self.refresh()
        else:
            self.refresh_sidebar()

    def apply_rating(self):
        if not self.selected:
            return
        self.bulk_point(f"Change rating on {len(self.selected)} images")
        ids = list(self.selected)
        self.db.execute(f"UPDATE images SET rating=? WHERE id IN ({','.join('?' * len(ids))})",
                        [int(self.rating_var.get())] + ids)
        self.db.commit()
        if self.side_filter[0] == "rated":
            self.refresh()
        else:
            self.refresh_sidebar()

    def open_file(self):
        if self.sel_path and os.path.exists(self.sel_path):
            if os.name == "nt":
                os.startfile(self.sel_path)
            else:
                subprocess.run(["xdg-open", self.sel_path])

    def show_in_folder(self):
        if self.sel_path and os.path.exists(self.sel_path):
            if os.name == "nt":
                subprocess.run(["explorer", "/select,", os.path.normpath(self.sel_path)])
            else:
                subprocess.run(["xdg-open", str(Path(self.sel_path).parent)])

    # ---------- indexing (background) ----------
    def backup_library(self):
        """Keep library.db.bak as the last known-good copy, taken at launch."""
        try:
            ok = self.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError:
            ok = False
        if ok:
            try:
                shutil.copy2(DB_PATH, DB_PATH.with_name(DB_PATH.name + ".bak"))
            except OSError:
                pass
        else:
            messagebox.showwarning(
                "Library problem",
                "library.db looks damaged. Close the app and restore library.db.bak "
                "(rename it to library.db) to go back to the last good copy.")

    def add_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of images to add")
        if folder:
            self.start_index([folder])

    def start_index(self, sources, silent=False):
        """Import folders and/or image files in the background (silent = no progress bar)."""
        if self.index_job:
            if not silent:
                messagebox.showinfo("Add images", "An import is already running. "
                                    "Wait for it to finish or press Cancel.")
            return
        q, cancel = queue.Queue(), threading.Event()
        self.index_job = (q, cancel, silent)
        if not silent:
            self.prog_bar.config(value=0, maximum=1)
            self.prog_label.config(text="Scanning…")
            self.prog.pack(side="right")
        self.index_thread = threading.Thread(
            target=index_worker, args=(list(sources), str(DB_PATH), q, cancel, 5 if silent else 0),
            daemon=True)
        self.index_thread.start()
        self.root.after(100, self.poll_index)

    def cancel_index(self):
        if self.index_job:
            self.index_job[1].set()
            self.prog_label.config(text="Cancelling…")

    def poll_index(self):
        if not self.index_job:
            return
        q, _, silent = self.index_job
        finished, added = False, 0
        try:
            while True:
                msg = q.get_nowait()
                if msg[0] == "total" and not silent:
                    self.prog_bar.config(maximum=max(1, msg[1]))
                    self.prog_label.config(text=f"Found {msg[1]} images")
                elif msg[0] == "progress" and not silent:
                    self.prog_bar.config(value=msg[1])
                    self.prog_label.config(text=f"{msg[1]} checked, {msg[2]} new")
                elif msg[0] == "done":
                    _, added, skipped, failed, cancelled = msg
                    if silent:
                        if added:
                            self.status.config(text=f"Watch: {added} new image(s) added")
                    else:
                        text = f"Added {added} new, {skipped} already in library"
                        if failed:
                            text += f", {failed} failed"
                        self.status.config(text=text + (" (cancelled)" if cancelled else ""))
                    finished = True
                elif msg[0] == "error":
                    if silent:
                        self.status.config(text="Watch check failed")
                    else:
                        messagebox.showerror("Indexing failed", msg[1])
                    finished = True
        except queue.Empty:
            pass
        if not finished:
            self.root.after(150, self.poll_index)
            return
        self.index_job = None
        self.prog.pack_forget()
        if not silent:
            self.reset_page()
        elif added:
            if self.page == 0 and not self.selected:
                self.refresh()  # nothing to lose, so show the new images straight away
            else:
                self.refresh_sidebar()

    # ---------- albums (virtual: nothing is moved or copied unless you export) ----------
    def albums(self):
        return self.db.execute("SELECT id, name FROM albums ORDER BY pos, name").fetchall()

    def fill_album_menu(self):
        m = self.album_menu
        m.delete(0, "end")
        albums = self.albums()
        for aid, name in albums:
            m.add_command(label=name, command=lambda a=aid: self.add_to_album(a))
        if albums:
            m.add_separator()
        m.add_command(label="New album…", command=self.new_album)

    def new_album(self):
        name = simpledialog.askstring("New album", "Album name:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        try:
            pos = self.db.execute("SELECT COALESCE(MAX(pos), 0) + 1 FROM albums").fetchone()[0]
            cur = self.db.execute("INSERT INTO albums(name, pos) VALUES(?, ?)", (name, pos))
            self.db.commit()
        except sqlite3.IntegrityError:
            messagebox.showinfo("New album", f"An album called '{name}' already exists.")
            return
        if self.selected:
            self.add_to_album(cur.lastrowid)  # a new album made with images selected starts with them
        else:
            self.refresh_sidebar()

    def add_to_album(self, aid):
        if not self.selected:
            messagebox.showinfo("Albums", "Select one or more images first.")
            return
        name = self.db.execute("SELECT name FROM albums WHERE id=?", (aid,)).fetchone()[0]
        self.bulk_point(f"Add {len(self.selected)} images to album {name}")
        self.db.executemany("INSERT OR IGNORE INTO album_images VALUES(?,?)",
                            [(aid, i) for i in self.selected])
        self.db.commit()
        self.status.config(text=f"Added {len(self.selected)} image(s) to '{name}'")
        self.refresh_sidebar()
        self.show_detail(self.primary)

    def remove_from_album(self):
        mode, aid = self.side_filter
        if mode != "album":
            self.status.config(text="Open an album in the sidebar first, then select its images")
            return
        if not self.selected:
            self.status.config(text="Select the images to remove from this album")
            return
        self.bulk_point(f"Remove {len(self.selected)} images from an album")
        self.db.executemany("DELETE FROM album_images WHERE album_id=? AND image_id=?",
                            [(aid, i) for i in self.selected])
        self.db.commit()
        self.status.config(text=f"Removed {len(self.selected)} image(s) from the album (files untouched)")
        self.refresh()

    def show_album(self, aid):
        self.side_filter = ("album", aid)
        self.reset_page()

    def on_side_menu(self, event):
        i = self.side_list.nearest(event.y)
        if i < 0 or i >= len(self.side_items) or self.side_items[i][1][0] != "album":
            return
        aid = self.side_items[i][1][1]
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Rename…", command=lambda: self.rename_album(aid))
        m.add_command(label="Move up", command=lambda: self.move_album(aid, -1))
        m.add_command(label="Move down", command=lambda: self.move_album(aid, 1))
        m.add_separator()
        m.add_command(label="Copy or move to a real folder…", command=lambda: self.export_album(aid))
        m.add_separator()
        m.add_command(label="Delete album…", command=lambda: self.delete_album(aid))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def rename_album(self, aid):
        old = self.db.execute("SELECT name FROM albums WHERE id=?", (aid,)).fetchone()[0]
        name = simpledialog.askstring("Rename album", "New name:", initialvalue=old, parent=self.root)
        if not name or not name.strip() or name.strip() == old:
            return
        try:
            self.db.execute("UPDATE albums SET name=? WHERE id=?", (name.strip(), aid))
            self.db.commit()
        except sqlite3.IntegrityError:
            messagebox.showinfo("Rename album", f"An album called '{name.strip()}' already exists.")
            return
        self.refresh_sidebar()

    def move_album(self, aid, delta):
        ids = [r[0] for r in self.db.execute("SELECT id FROM albums ORDER BY pos, name")]
        i = ids.index(aid)
        j = i + delta
        if not 0 <= j < len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        self.db.executemany("UPDATE albums SET pos=? WHERE id=?", [(n, a) for n, a in enumerate(ids, 1)])
        self.db.commit()
        self.refresh_sidebar()

    def delete_album(self, aid):
        name = self.db.execute("SELECT name FROM albums WHERE id=?", (aid,)).fetchone()[0]
        n = self.db.execute("SELECT COUNT(*) FROM album_images WHERE album_id=?", (aid,)).fetchone()[0]
        if not messagebox.askyesno(
                "Delete album",
                f"Delete the album '{name}'?\n\nThe {n} images in it are not deleted or moved, only the "
                "album is removed. You can undo this from Restore points."):
            return
        self.end_point(self.begin_point(f"Delete album {name}"), True)
        self.db.execute("DELETE FROM album_images WHERE album_id=?", (aid,))
        self.db.execute("DELETE FROM albums WHERE id=?", (aid,))
        self.db.commit()
        if self.side_filter == ("album", aid):
            self.side_filter = ("all", None)
            self.reset_page()
        else:
            self.refresh_sidebar()

    def export_album(self, aid):
        """Albums are virtual. This puts a real copy (or move) of the album's images in a folder."""
        self.show_album(aid)
        self.sort_dialog(scope="current", mode="album")

    # ---------- restore points ----------
    def begin_point(self, label, kind="auto"):
        if not self.restore:
            return None
        try:
            return self.restore.begin(label, kind)
        except Exception:
            return None

    def end_point(self, rp, changed):
        if rp is not None and self.restore:
            try:
                self.restore.end(rp, changed)
            except Exception:
                pass

    def log_move(self, rp, op, old, new):
        if rp is not None and self.restore:
            self.restore.log_move(rp, op, old, new)

    def bulk_point(self, label):
        """Restore point for changes to many images at once (single-image tweaks don't need one)."""
        if len(self.selected) >= BULK_MIN:
            self.end_point(self.begin_point(label), True)

    def trash_file(self, rp, path):
        if rp is not None and self.restore:
            self.restore.trash_file(rp, path)
        else:
            to_recycle_bin(path)  # no restore point available: fall back to the Recycle Bin

    def do_restore(self, point_id, label):
        rp = self.begin_point(f"Before restoring to: {label}")  # so the restore can itself be undone
        done, problems = self.restore.revert(point_id, rp)
        self.restore.restore_library(point_id)
        self.end_point(rp, True)
        self.cache.clear()
        self.refresh()
        return done, problems

    def restore_dialog(self):
        if not self.restore:
            messagebox.showinfo("Restore points", "Restore points are not available "
                                "(the restore folder next to the app could not be created).")
            return
        win = tk.Toplevel(self.root)
        win.title("Restore points")
        win.geometry("900x580")
        win.transient(self.root)
        win.grab_set()
        ttk.Label(win, wraplength=860, justify="left", text=(
            "A restore point is a snapshot of your library taken just before a risky change. "
            "Restoring returns the library, and every file the app renamed, moved or deleted, to how "
            "they were just before that change, and undoes all later changes too. "
            "Deleted files wait in the app trash until their restore point is pruned or deleted. "
            "Select several points with Ctrl or Shift to delete them by hand.")
        ).pack(anchor="w", padx=12, pady=10)
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12)
        lb = tk.Listbox(body, width=52, exportselection=False, activestyle="none", selectmode="extended")
        lb.pack(side="left", fill="y")
        box = tk.Text(body, wrap="word", state="disabled")
        box.pack(side="left", fill="both", expand=True, padx=(8, 0))
        status = ttk.Label(win, text="")
        status.pack(anchor="w", padx=12, pady=4)
        pts = []

        def show(text):
            box.config(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text)
            box.config(state="disabled")

        def reload():
            pts[:] = self.restore.points()
            lb.delete(0, "end")
            for pid, created, label, kind in pts:
                stamp = datetime.fromisoformat(created).strftime("%d %b %Y %H:%M")
                lb.insert("end", f"{stamp} · {label}")
            status.config(text=f"{len(pts)} restore points (the newest {MAX_POINTS} are kept) · "
                               f"app trash holds {self.restore.trash_size() / 1048576:.1f} MB")
            show("Select a restore point to see what restoring to it would do." if pts else
                 "No restore points yet. One is made automatically before risky changes such as "
                 "renaming, moving or deleting files, and bulk edits.")

        def picked():
            sel = lb.curselection()
            return pts[sel[0]] if sel else None

        def preview(event=None):
            p = picked()
            if not p:
                return
            ids, steps, problems = self.restore.revert_plan(p[0])
            own = self.restore.summary(p[0])
            lines = [f"Restore to: {p[2]}", f"Taken: {p[1].replace('T', ' ')}", "",
                     f"Restoring undoes {len(ids)} operation(s), newest first.",
                     f"Files that will be moved back: {len(steps)}",
                     f"Files that cannot be put back: {len(problems)}"]
            if own:
                lines += ["", "This point itself recorded: " + ", ".join(f"{n} {op}" for op, n in own.items())]
            for path, why in problems[:40]:
                lines.append(f"  - {path}: {why}")
            if len(problems) > 40:
                lines.append(f"  ... and {len(problems) - 40} more")
            if problems:
                lines += ["", "Those files are skipped; their library entries come back but show as missing."]
            show("\n".join(lines))

        def restore_now():
            p = picked()
            if not p:
                return
            if len(lb.curselection()) != 1:
                messagebox.showinfo("Restore", "Select a single restore point to restore to.", parent=win)
                return
            if self.index_job:
                messagebox.showinfo("Restore", "Wait for the running import to finish first.", parent=win)
                return
            ids, steps, problems = self.restore.revert_plan(p[0])
            msg = (f"Restore to \"{p[2]}\"?\n\nThis undoes {len(ids)} operation(s), moves {len(steps)} "
                   f"files back and puts the library back as it was.")
            if problems:
                msg += f"\n\n{len(problems)} files cannot be put back and will be skipped."
            msg += "\n\nA new restore point is made first, so you can undo the restore."
            if not messagebox.askyesno("Restore", msg, parent=win):
                return
            done, bad = self.do_restore(p[0], p[2])
            reload()
            show(f"Restored. {done} files moved back."
                 + ("".join(f"\n  - {path}: {why}" for path, why in bad[:40]) if bad else ""))

        def make_point():
            name = simpledialog.askstring("Restore point", "Name for this restore point:", parent=win)
            if name and name.strip():
                self.end_point(self.begin_point(f"Manual: {name.strip()}", "manual"), True)
                reload()

        def empty_trash():
            if not messagebox.askyesno(
                    "Empty app trash",
                    "Send everything in the app trash to the Windows Recycle Bin?\n"
                    "Those deletions can then no longer be undone from here.", parent=win):
                return
            n = self.restore.empty_trash()
            reload()
            show(f"Sent {n} files to the Windows Recycle Bin.")

        def open_trash():
            self.restore.trash.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(self.restore.trash)
            else:
                subprocess.run(["xdg-open", str(self.restore.trash)])

        free_var = tk.BooleanVar(value=False)

        def delete_selected():
            sel = lb.curselection()
            if not sel:
                messagebox.showinfo("Delete", "Select one or more restore points first.", parent=win)
                return
            ids = [pts[i][0] for i in sel]
            msg = (f"Delete {len(ids)} restore point(s)?\n\nYou will no longer be able to restore to "
                   "exactly those moments. Your other restore points keep working, and files these "
                   "points moved can still be put back by restoring to an older point.")
            if free_var.get():
                msg += ("\n\nFiles these points deleted will be sent to the Windows Recycle Bin to free "
                        "space. Older restore points will no longer be able to bring those files back.")
            if not messagebox.askyesno("Delete restore points", msg, parent=win):
                return
            n, freed = self.restore.delete_points(ids, free_var.get())
            reload()
            show(f"Deleted {n} restore point(s)."
                 + (f" Sent {freed} files to the Windows Recycle Bin." if freed else ""))

        ttk.Checkbutton(win, variable=free_var,
                        text="When deleting, also free the trash space those points use "
                             "(sends their deleted files to the Windows Recycle Bin)"
                        ).pack(anchor="w", padx=12)
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=10)
        ttk.Button(btns, text="Restore to this point…", command=restore_now).pack(side="left")
        ttk.Button(btns, text="Create restore point…", command=make_point).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete selected…", command=delete_selected).pack(side="left")
        ttk.Button(btns, text="Open trash folder", command=open_trash).pack(side="left", padx=6)
        ttk.Button(btns, text="Empty trash…", command=empty_trash).pack(side="left")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
        lb.bind("<<ListboxSelect>>", preview)
        reload()

    # ---------- light / dark theme ----------
    def toggle_theme(self):
        mode = "dark" if self.theme == "light" else "light"
        self.db.execute("INSERT OR REPLACE INTO settings VALUES('theme', ?)", (mode,))
        self.db.commit()
        self.apply_theme(mode)

    def apply_theme(self, mode, walk=True):
        pal = THEMES[mode]
        self.theme = mode
        style = ttk.Style()
        if mode == "dark":
            style.theme_use("clam")

            def cfg(name, **kw):
                try:
                    style.configure(name, **kw)
                except tk.TclError:
                    pass

            def mp(name, **kw):
                try:
                    style.map(name, **kw)
                except tk.TclError:
                    pass

            cfg(".", background=pal["bg"], foreground=pal["fg"], fieldbackground=pal["field"],
                bordercolor=pal["border"], lightcolor=pal["border"], darkcolor=pal["border"],
                troughcolor=pal["field"], insertcolor=pal["fg"], focuscolor=pal["bg"])
            cfg("TButton", background=pal["btn"], foreground=pal["fg"], padding=(8, 3))
            mp("TButton", background=[("active", pal["btn_active"]), ("disabled", pal["bg"])],
               foreground=[("disabled", pal["dim"])])
            cfg("TEntry", fieldbackground=pal["field"], foreground=pal["fg"], insertcolor=pal["fg"])
            cfg("TCombobox", fieldbackground=pal["field"], background=pal["btn"],
                foreground=pal["fg"], arrowcolor=pal["fg"], selectbackground=pal["field"],
                selectforeground=pal["fg"])
            mp("TCombobox", fieldbackground=[("readonly", pal["field"])],
               foreground=[("readonly", pal["fg"])], background=[("readonly", pal["btn"])])
            for name in ("TCheckbutton", "TRadiobutton"):
                cfg(name, background=pal["bg"], foreground=pal["fg"],
                    indicatorbackground=pal["field"], indicatorforeground=pal["fg"])
                mp(name, background=[("active", pal["bg"])],
                   indicatorbackground=[("selected", pal["sel"])])
            cfg("TScrollbar", background=pal["btn"], troughcolor=pal["bg"], arrowcolor=pal["fg"],
                bordercolor=pal["bg"])
            mp("TScrollbar", background=[("active", pal["btn_active"])])
            cfg("Horizontal.TProgressbar", background=pal["sel"], troughcolor=pal["field"])
            cfg("TLabel", background=pal["bg"], foreground=pal["fg"])
        else:
            style.theme_use(self.default_theme)  # back to the native Windows look

        r = self.root
        r.configure(bg=pal["bg"])
        for pat, val in (
                ("*Listbox.background", pal["field"]), ("*Listbox.foreground", pal["fg"]),
                ("*Listbox.selectBackground", pal["sel"]), ("*Listbox.selectForeground", pal["selfg"]),
                ("*Text.background", pal["field"]), ("*Text.foreground", pal["fg"]),
                ("*Text.insertBackground", pal["fg"]), ("*Text.selectBackground", pal["sel"]),
                ("*Text.selectForeground", pal["selfg"]),
                ("*Canvas.background", pal["bg"]), ("*Toplevel.background", pal["bg"]),
                ("*Menu.background", pal["field"]), ("*Menu.foreground", pal["fg"]),
                ("*Menu.activeBackground", pal["sel"]), ("*Menu.activeForeground", pal["selfg"]),
                ("*Label.background", pal["bg"]), ("*Label.foreground", pal["fg"]),
                ("*Scale.background", pal["bg"]), ("*Scale.foreground", pal["fg"]),
                ("*Scale.troughColor", pal["field"]),
                ("*TCombobox*Listbox.background", pal["field"]),
                ("*TCombobox*Listbox.foreground", pal["fg"]),
                ("*TCombobox*Listbox.selectBackground", pal["sel"]),
                ("*TCombobox*Listbox.selectForeground", pal["selfg"])):
            r.option_add(pat, val)
        self.normal_bg = pal["bg"]
        if hasattr(self, "theme_btn"):
            self.theme_btn.config(text="☀ Light mode" if mode == "dark" else "☾ Dark mode")
        if walk:
            self.recolor_tree(r, pal)
            self.recolor()  # thumbnail borders use normal_bg
            self.set_titlebar(mode == "dark")

    def recolor_tree(self, w, pal):
        """Recolour tk (non-ttk) widgets that are already on screen."""
        if getattr(w, "no_theme", False):
            return
        cls = w.winfo_class()
        try:
            if cls in ("Tk", "Toplevel", "Frame", "Canvas"):
                w.configure(bg=pal["bg"])
            elif cls in ("Listbox", "Text"):
                w.configure(bg=pal["field"], fg=pal["fg"], selectbackground=pal["sel"],
                            selectforeground=pal["selfg"])
                if cls == "Text":
                    w.configure(insertbackground=pal["fg"])
            elif cls == "Scale":
                w.configure(bg=pal["bg"], fg=pal["fg"], troughcolor=pal["field"])
        except tk.TclError:
            pass
        for child in w.winfo_children():
            self.recolor_tree(child, pal)

    def set_titlebar(self, dark):
        """Dark title bar on Windows 10/11 (may need a click on the window to repaint)."""
        if os.name != "nt":
            return
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            val = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val),
                                                       ctypes.sizeof(val))
        except Exception:
            pass

    # ---------- similar images ----------
    def describe(self, iid):
        path = self.db.execute("SELECT path FROM images WHERE id=?", (iid,)).fetchone()[0]
        try:
            with Image.open(path) as im:
                w, h = im.size
        except Exception:
            w = h = 0
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return {"id": iid, "path": path, "w": w, "h": h, "pixels": w * h, "size": size}

    def similar_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Find similar images")
        win.geometry("1060x700")
        win.transient(self.root)
        win.grab_set()
        st = {"groups": [], "cur": None, "items": [], "photos": [], "job": None}

        top = ttk.Frame(win)
        top.pack(fill="x", padx=12, pady=10)
        ttk.Label(top, text="Strictness:").pack(side="left")
        scale = tk.Scale(top, from_=0, to=10, orient="horizontal", length=220)
        scale.set(5)
        scale.pack(side="left", padx=6)
        ttk.Label(top, text="0 = near-identical, higher = looser matches. Try 3 to 5 first.").pack(
            side="left", padx=6)
        search_btn = ttk.Button(top, text="Search")
        search_btn.pack(side="left", padx=8)
        auto_btn = ttk.Button(top, text="Keep the largest in every group…", state="disabled")
        auto_btn.pack(side="left")
        bar = ttk.Progressbar(win, mode="determinate")
        bar.pack(fill="x", padx=12)
        status = ttk.Label(win, text="Press Search. Each image is analysed once and remembered, "
                           "so later searches are much quicker.")
        status.pack(anchor="w", padx=12, pady=4)

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12, pady=4)
        gl = tk.Listbox(body, width=22, exportselection=False, activestyle="none")
        gl.pack(side="left", fill="y")
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        canvas = tk.Canvas(right, highlightthickness=0)
        sb = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        action = ttk.Frame(win)
        action.pack(fill="x", padx=12, pady=10)
        apply_btn = ttk.Button(action, text="Apply to this group (unticked go to the trash, undoable)")
        apply_btn.pack(side="left")
        skip_btn = ttk.Button(action, text="Skip group")
        skip_btn.pack(side="left", padx=6)

        def clear_view():
            for w in inner.winfo_children():
                w.destroy()
            st["items"], st["photos"], st["cur"] = [], [], None

        def fill_groups():
            gl.delete(0, "end")
            for n, g in enumerate(st["groups"], 1):
                gl.insert("end", f"Group {n} · {len(g)} images")
            clear_view()
            auto_btn.config(state="normal" if st["groups"] else "disabled")
            if st["groups"]:
                gl.selection_set(0)
                show_group()

        def show_group(event=None):
            sel = gl.curselection()
            if not sel:
                return
            clear_view()
            gid = st["cur"] = sel[0]
            infos = [self.describe(i) for i in st["groups"][gid][:30]]
            best = max(infos, key=lambda d: (d["pixels"], d["size"]))
            for n, d in enumerate(infos):
                cell = ttk.Frame(inner, padding=6)
                cell.grid(row=n // 3, column=n % 3, sticky="n")
                try:
                    im = Image.open(d["path"])
                    im.draft("RGB", (460, 460))
                    im = im.convert("RGB")
                    im.thumbnail((230, 230))
                except Exception:
                    im = Image.new("RGB", (230, 170), "#888888")
                ph = ImageTk.PhotoImage(im)
                st["photos"].append(ph)
                tk.Label(cell, image=ph).pack()
                ttk.Label(cell, justify="center", wraplength=230,
                          text=f"{Path(d['path']).name}\n{d['w']}×{d['h']} · {d['size'] / 1048576:.2f} MB"
                          ).pack()
                d["var"] = tk.BooleanVar(value=d is best)
                ttk.Checkbutton(cell, text="Keep", variable=d["var"]).pack()
                st["items"].append(d)
            canvas.yview_moveto(0)

        def remove_group(gid):
            del st["groups"][gid]
            fill_groups()
            if st["groups"]:
                gl.selection_clear(0, "end")
                gl.selection_set(min(gid, len(st["groups"]) - 1))
                show_group()

        def apply():
            gid = st["cur"]
            if gid is None:
                return
            keep = [d for d in st["items"] if d["var"].get()]
            drop = [d for d in st["items"] if not d["var"].get()]
            if not keep:
                messagebox.showwarning("Keep at least one", "Tick Keep on at least one image.",
                                       parent=win)
                return
            if drop:
                done, failed, freed = self.delete_dups(
                    [((keep[0]["id"], keep[0]["path"]), [(d["id"], d["path"]) for d in drop])])
                status.config(text=f"Moved {done} to the trash ({freed / 1048576:.1f} MB), undo from Restore points"
                              + (f", {failed} failed" if failed else ""))
            remove_group(gid)

        def skip():
            if st["cur"] is not None:
                remove_group(st["cur"])

        def auto_all():
            if not st["groups"]:
                return
            plan = []
            for g in st["groups"]:
                infos = [self.describe(i) for i in g]
                best = max(infos, key=lambda d: (d["pixels"], d["size"]))
                plan.append(((best["id"], best["path"]),
                             [(d["id"], d["path"]) for d in infos if d is not best]))
            extras = sum(len(x) for _, x in plan)
            if not messagebox.askyesno(
                    "Keep the largest",
                    f"Keep the largest image in each of {len(plan)} groups and move the other "
                    f"{extras} to the app trash?\nYou can undo this from Restore points.", parent=win):
                return
            done, failed, freed = self.delete_dups(plan)
            messagebox.showinfo("Done", f"Moved {done} files to the trash "
                                f"({freed / 1048576:.1f} MB).\nFailed: {failed}", parent=win)
            st["groups"] = []
            fill_groups()

        def poll():
            job = st["job"]
            if not job:
                return
            q = job[0]
            finished = False
            try:
                while True:
                    msg = q.get_nowait()
                    if msg[0] == "total":
                        bar.config(maximum=max(1, msg[1]), value=0)
                        status.config(text=f"Analysing {msg[1]} new images…" if msg[1]
                                      else "Comparing images…")
                    elif msg[0] == "progress":
                        bar.config(value=msg[1])
                    elif msg[0] == "searching":
                        bar.config(value=bar.cget("maximum"))
                        status.config(text="Comparing images…")
                    elif msg[0] == "groups":
                        st["groups"] = msg[1]
                        fill_groups()
                        n = len(msg[1])
                        status.config(text=f"Found {n} group{'s' if n != 1 else ''} of similar images."
                                      if n else "No similar images found. Try a higher strictness.")
                    elif msg[0] == "done":
                        if msg[1]:
                            status.config(text="Cancelled. Progress is kept.")
                        finished = True
                    elif msg[0] == "error":
                        messagebox.showerror("Search failed", msg[1], parent=win)
                        finished = True
            except queue.Empty:
                pass
            if finished:
                st["job"] = None
                search_btn.config(state="normal")
            else:
                win.after(150, poll)

        def search():
            if st["job"]:
                return
            q, cancel = queue.Queue(), threading.Event()
            st["job"] = (q, cancel)
            search_btn.config(state="disabled")
            status.config(text="Starting…")
            threading.Thread(target=similar_worker, args=(str(DB_PATH), scale.get(), q, cancel),
                             daemon=True).start()
            poll()

        def close():
            if st["job"]:
                st["job"][1].set()
                st["job"] = None
            win.destroy()
            self.cache.clear()
            self.refresh()

        search_btn.config(command=search)
        auto_btn.config(command=auto_all)
        apply_btn.config(command=apply)
        skip_btn.config(command=skip)
        ttk.Button(action, text="Close", command=close).pack(side="right")
        gl.bind("<<ListboxSelect>>", show_group)
        win.protocol("WM_DELETE_WINDOW", close)

    # ---------- drag and drop (optional) ----------
    def setup_dnd(self):
        if not DND_FILES:
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.on_drop)
            self.status.config(text="Tip: drop folders or images onto this window to add them")
        except Exception:
            pass

    def on_drop(self, event):
        self.start_index(list(self.root.tk.splitlist(event.data)))

    # ---------- watch folders ----------
    def watch_folders(self):
        return [r[0] for r in self.db.execute("SELECT folder FROM watch ORDER BY folder")]

    def watch_enabled(self):
        r = self.db.execute("SELECT value FROM settings WHERE key='watch_enabled'").fetchone()
        return r is None or r[0] == "1"

    def watch_tick(self):
        try:
            if self.watch_enabled():
                folders = self.watch_folders()
                if folders:
                    self.start_index(folders, silent=True)
        finally:
            self.root.after(WATCH_MS, self.watch_tick)

    def watch_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Watch folders")
        win.geometry("580x400")
        win.transient(self.root)
        win.grab_set()
        ttk.Label(win, justify="left", text=(
            "New images in these folders are added to the library automatically.\n"
            "They are checked once at startup, then every minute while the app is open.")
        ).pack(anchor="w", padx=12, pady=10)
        lb = tk.Listbox(win, activestyle="none")
        lb.pack(fill="both", expand=True, padx=12)

        def reload():
            lb.delete(0, "end")
            for f in self.watch_folders():
                lb.insert("end", f)

        def add():
            f = filedialog.askdirectory(parent=win, title="Folder to watch")
            if f:
                self.db.execute("INSERT OR IGNORE INTO watch VALUES(?)", (str(Path(f)),))
                self.db.commit()
                reload()
                self.start_index([str(Path(f))])  # import what is already in it

        def remove():
            for i in lb.curselection():
                self.db.execute("DELETE FROM watch WHERE folder=?", (lb.get(i),))
            self.db.commit()
            reload()

        def toggle():
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('watch_enabled', ?)",
                            ("1" if enabled.get() else "0",))
            self.db.commit()

        def check_now():
            folders = self.watch_folders()
            if folders:
                self.start_index(folders)

        enabled = tk.BooleanVar(value=self.watch_enabled())
        ttk.Checkbutton(win, text="Check these folders automatically", variable=enabled,
                        command=toggle).pack(anchor="w", padx=12, pady=6)
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(btns, text="Add folder…", command=add).pack(side="left")
        ttk.Button(btns, text="Remove selected", command=remove).pack(side="left", padx=4)
        ttk.Button(btns, text="Check now", command=check_now).pack(side="left")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
        reload()

    def on_close(self):
        if self.index_job:
            self.index_job[1].set()
            if self.index_thread:
                self.index_thread.join(timeout=3)
        self.root.destroy()

    # ---------- manual type override ----------
    def mark_showing(self):
        if not self.total:
            return
        kind = self.mark_var.get()
        if not messagebox.askyesno(
                "Mark type",
                f"Set the type to '{kind}' for all {self.total} images matching the "
                f"current search / filter (every page)?"):
            return
        w, p = self.where()
        self.end_point(self.begin_point(f"Mark {self.total} images as {kind}"), True)
        self.db.execute("UPDATE images SET kind=?" + w, [kind] + p)
        self.db.commit()
        self.status.config(text=f"Marked {self.total} as {kind}")
        self.refresh()

    # ---------- full-size viewer ----------
    def copy_prompt(self):
        text = self.prompt.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status.config(text="Prompt copied")

    def open_viewer(self, start_id=None):
        w, p = self.where()
        items = self.db.execute("SELECT id, path FROM images" + w + SORTS[self.sort_var.get()],
                                p).fetchall()
        if not items:
            return
        idx = next((n for n, r in enumerate(items) if r[0] == start_id), 0)
        win = tk.Toplevel(self.root)
        win.geometry("1000x760")
        win.configure(bg="black")
        win.no_theme = True
        tk.Label(win, text="← → browse    0-5 rate    F favourite    Esc close",
                 bg="black", fg="#888888").pack(side="bottom")
        lbl = tk.Label(win, bg="black", fg="white")
        lbl.pack(fill="both", expand=True)
        st = {"i": idx, "img": None, "size": (0, 0), "job": None}

        def show():
            iid, path = items[st["i"]]
            fav, rating = self.db.execute("SELECT fav, rating FROM images WHERE id=?",
                                          (iid,)).fetchone()
            try:
                img = Image.open(path).convert("RGB")
                img.thumbnail((max(200, win.winfo_width() - 4), max(200, win.winfo_height() - 30)))
                st["img"] = ImageTk.PhotoImage(img)
                lbl.config(image=st["img"], text="")
            except Exception:
                lbl.config(image="", text="(file missing)")
            win.title(f"{Path(path).name}    {st['i'] + 1} / {len(items)}    "
                      f"{'♥ ' if fav else ''}{'★' * (rating or 0)}")

        def go(delta=None, to=None):
            n = st["i"] + delta if delta is not None else to
            st["i"] = min(max(n, 0), len(items) - 1)
            show()

        def on_key(e):
            iid = items[st["i"]][0]
            if e.char and e.char in "012345":
                self.db.execute("UPDATE images SET rating=? WHERE id=?", (int(e.char), iid))
            elif e.char and e.char.lower() == "f":
                self.db.execute("UPDATE images SET fav=1-fav WHERE id=?", (iid,))
            else:
                return
            self.db.commit()
            show()

        def close():
            win.destroy()
            self.refresh()

        def on_resize(e):
            if e.widget is win and (e.width, e.height) != st["size"]:
                st["size"] = (e.width, e.height)
                if st["job"]:
                    win.after_cancel(st["job"])
                st["job"] = win.after(150, show)

        win.bind("<Right>", lambda e: go(1))
        win.bind("<space>", lambda e: go(1))
        win.bind("<Left>", lambda e: go(-1))
        win.bind("<Home>", lambda e: go(to=0))
        win.bind("<End>", lambda e: go(to=len(items) - 1))
        win.bind("<Escape>", lambda e: close())
        win.bind("<Key>", on_key)
        win.bind("<Configure>", on_resize)
        win.protocol("WM_DELETE_WINDOW", close)
        win.focus_force()
        win.after(100, show)

    # ---------- housekeeping ----------
    def missing_ids(self):
        return [i for i, p in self.db.execute("SELECT id, path FROM images").fetchall()
                if not os.path.exists(p)]

    def remove_entries(self, ids):
        self.db.executemany("DELETE FROM tags WHERE image_id=?", [(i,) for i in ids])
        self.db.executemany("DELETE FROM album_images WHERE image_id=?", [(i,) for i in ids])
        self.db.executemany("DELETE FROM images WHERE id=?", [(i,) for i in ids])
        self.db.commit()

    def cleanup_missing(self):
        total = self.db.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        missing = self.missing_ids()
        if not missing:
            messagebox.showinfo("Clean up", "No missing files found. Everything in the library still exists.")
            return
        warn = ("\n\nWARNING: every file looks missing. Is an external drive disconnected?"
                if len(missing) == total else "")
        if not messagebox.askyesno(
                "Clean up",
                f"{len(missing)} library entries point to files that no longer exist.\n"
                f"Remove them from the library? No image files are touched.{warn}"):
            return
        rp = self.begin_point(f"Remove {len(missing)} missing entries")
        self.remove_entries(missing)
        self.end_point(rp, True)
        self.status.config(text=f"Removed {len(missing)} missing entries")
        self.cache.clear()
        self.refresh()

    def dup_plan(self, mode):
        """Return [(keeper, [extras])] for exact duplicates; each item is (id, path)."""
        groups = {}
        for iid, path, h in self.db.execute(
                "SELECT id, path, hash FROM images WHERE hash IN "
                "(SELECT hash FROM images GROUP BY hash HAVING COUNT(*) > 1) ORDER BY hash, id"):
            if os.path.exists(path):
                groups.setdefault(h, []).append((iid, path))
        plan = []
        for g in groups.values():
            if len(g) < 2:
                continue
            if mode == "short":
                keep = min(g, key=lambda r: (len(r[1]), r[0]))
            elif mode == "oldest":
                keep = min(g, key=lambda r: (os.path.getmtime(r[1]), r[0]))
            else:
                keep = min(g, key=lambda r: r[0])
            plan.append((keep, [r for r in g if r is not keep]))
        return plan

    def delete_dups(self, plan):
        """Move the extra copies to the app trash (undoable from Restore points); tags, favourite
        and rating are merged into the kept copy."""
        done = failed = freed = 0
        rp = self.begin_point(f"Delete {sum(len(o) for _, o in plan)} duplicate files")
        for keep, others in plan:
            for iid, path in others:
                try:
                    size = os.path.getsize(path)
                    self.trash_file(rp, path)
                except OSError:
                    failed += 1
                    continue
                self.db.execute("INSERT OR IGNORE INTO tags SELECT ?, tag FROM tags WHERE image_id=?",
                                (keep[0], iid))
                self.db.execute(
                    "UPDATE images SET fav=MAX(fav, (SELECT fav FROM images WHERE id=?)), "
                    "rating=MAX(rating, (SELECT rating FROM images WHERE id=?)) WHERE id=?",
                    (iid, iid, keep[0]))
                self.db.execute("INSERT OR IGNORE INTO album_images "
                                "SELECT album_id, ? FROM album_images WHERE image_id=?", (keep[0], iid))
                self.db.execute("DELETE FROM album_images WHERE image_id=?", (iid,))
                self.db.execute("DELETE FROM tags WHERE image_id=?", (iid,))
                self.db.execute("DELETE FROM images WHERE id=?", (iid,))
                freed += size
                done += 1
        self.db.commit()
        self.end_point(rp, done > 0)
        return done, failed, freed

    def delete_duplicates_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Delete duplicates")
        win.geometry("760x600")
        win.transient(self.root)
        win.grab_set()
        mode_var = tk.StringVar(value="first")
        ttk.Label(win, text="Which copy do you want to keep?",
                  font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        for value, text in [("first", "The one added to the library first"),
                            ("short", "The one with the shortest path (often the original)"),
                            ("oldest", "The oldest file (by modified date)")]:
            ttk.Radiobutton(win, text=text, variable=mode_var, value=value,
                            command=lambda: preview()).pack(anchor="w", padx=24)
        ttk.Label(win, text="Preview", font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        box = tk.Text(win, wrap="none", state="disabled")
        box.pack(fill="both", expand=True, padx=12)
        status = ttk.Label(win, text="")
        status.pack(anchor="w", padx=12, pady=4)

        def preview():
            plan = self.dup_plan(mode_var.get())
            extras = sum(len(x) for _, x in plan)
            size = sum(os.path.getsize(path) for _, x in plan for _, path in x if os.path.exists(path))
            box.config(state="normal")
            box.delete("1.0", "end")
            if not plan:
                box.insert("end", "No duplicate files found.")
            for keep, others in plan[:80]:
                box.insert("end", f"KEEP    {keep[1]}\n")
                for _, path in others:
                    box.insert("end", f"delete  {path}\n")
                box.insert("end", "\n")
            if len(plan) > 80:
                box.insert("end", f"... and {len(plan) - 80} more sets\n")
            box.config(state="disabled")
            status.config(text=f"{extras} extra copies in {len(plan)} sets, about {size / 1048576:.1f} MB")

        def run():
            plan = self.dup_plan(mode_var.get())
            extras = sum(len(x) for _, x in plan)
            if not extras:
                return
            if not messagebox.askyesno(
                    "Delete duplicates",
                    f"Move {extras} extra copies to the app trash?\n"
                    "You can undo this from Restore points. Tags, favourites and ratings are kept on the copy you keep.",
                    parent=win):
                return
            done, failed, freed = self.delete_dups(plan)
            messagebox.showinfo("Delete duplicates",
                                f"Moved {done} files to the trash ({freed / 1048576:.1f} MB).\n"
                                f"Failed: {failed}", parent=win)
            win.destroy()
            self.cache.clear()
            self.refresh()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=10)
        ttk.Button(btns, text="Move extras to trash", command=run).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        preview()

    # ---------- bulk rename ----------
    def rename_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Rename images")
        win.geometry("620x560")
        win.transient(self.root)
        win.grab_set()
        scope_var = tk.StringVar(value="selected" if self.selected else "current")
        pat_var = tk.StringVar(value="{date}_{n:03}")
        start_var = tk.StringVar(value="1")

        def heading(text):
            ttk.Label(win, text=text, font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))

        heading("Which images?")
        for value, text in [("selected", "Only the selected images"),
                            ("current", "Everything showing now (current search / filter)")]:
            ttk.Radiobutton(win, text=text, variable=scope_var, value=value).pack(anchor="w", padx=24)
        heading("New name pattern")
        ttk.Entry(win, textvariable=pat_var).pack(fill="x", padx=12)
        ttk.Label(win, justify="left", text=(
            "{date} taken date    {type} photo / ai / other    {tag} first tag\n"
            "{name} original name    {n} counter    {n:03} counter padded to 3 digits"
        )).pack(anchor="w", padx=12, pady=4)
        srow = ttk.Frame(win)
        srow.pack(fill="x", padx=12)
        ttk.Label(srow, text="Counter starts at:").pack(side="left")
        ttk.Entry(srow, textvariable=start_var, width=6).pack(side="left", padx=6)
        heading("Preview")
        box = tk.Text(win, height=12, wrap="none", state="disabled")
        box.pack(fill="both", expand=True, padx=12)
        status = ttk.Label(win, text="")
        status.pack(anchor="w", padx=12, pady=4)

        def first_tag(iid):
            t = self.db.execute("SELECT tag FROM tags WHERE image_id=? ORDER BY tag LIMIT 1",
                                (iid,)).fetchone()
            return t[0] if t else ""

        def get_rows():
            if scope_var.get() == "selected":
                ids = list(self.selected)
                if not ids:
                    return []
                return self.db.execute(
                    f"SELECT id, path, taken, kind FROM images WHERE id IN ({','.join('?' * len(ids))})"
                    " ORDER BY taken, path", ids).fetchall()
            w, p = self.where()
            return self.db.execute("SELECT id, path, taken, kind FROM images" + w +
                                   " ORDER BY taken, path", p).fetchall()

        def plan():
            try:
                start = int(start_var.get())
            except ValueError:
                start = 1
            out = []
            for k, r in enumerate(get_rows()):
                src = Path(r[1])
                new = build_name(pat_var.get(), start + k, r[2], r[3], first_tag(r[0]), src.stem)
                out.append((r[0], src, src.with_name(new + src.suffix)))
            return out

        def preview():
            items = plan()
            box.config(state="normal")
            box.delete("1.0", "end")
            if not items:
                box.insert("end", "No images to rename.")
            for _, src, tgt in items[:15]:
                box.insert("end", f"{src.name}  →  {tgt.name}\n")
            if len(items) > 15:
                box.insert("end", f"... and {len(items) - 15} more\n")
            box.config(state="disabled")
            status.config(text=f"{len(items)} files (a name that already exists gets _1, _2 added)")

        def run():
            items = plan()
            if not items:
                return
            if not messagebox.askyesno(
                    "Rename files",
                    f"Rename {len(items)} files on disk?\nThis changes the real filenames.",
                    parent=win):
                return
            rp = self.begin_point(f"Rename {len(items)} files")
            done = missing = failed = 0
            for iid, src, tgt in items:
                if not src.exists():
                    missing += 1
                    continue
                if tgt == src:
                    continue
                try:
                    tgt = unique_path(tgt)
                    src.rename(tgt)
                    self.log_move(rp, "rename", str(src), str(tgt))
                    self.db.execute("UPDATE images SET path=? WHERE id=?", (str(tgt), iid))
                    done += 1
                except (OSError, sqlite3.IntegrityError):
                    failed += 1
            self.db.commit()
            self.end_point(rp, done > 0)
            messagebox.showinfo("Rename finished",
                                f"Renamed {done} files.\nMissing skipped: {missing}\nFailed: {failed}",
                                parent=win)
            win.destroy()
            self.cache.clear()
            self.refresh()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=10)
        ttk.Button(btns, text="Preview", command=preview).pack(side="left")
        ttk.Button(btns, text="Rename", command=run).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    # ---------- sort into folders ----------
    def sort_rows(self, scope):
        if scope == "selected":
            ids = list(self.selected)
            if not ids:
                return []
            return self.db.execute(
                f"SELECT id, path, taken, kind FROM images WHERE id IN ({','.join('?' * len(ids))})",
                ids).fetchall()
        w, p = self.where() if scope == "current" else ("", [])
        return self.db.execute("SELECT id, path, taken, kind FROM images" + w, p).fetchall()

    def folder_for(self, row, mode):
        iid, _, taken, kind = row
        y, m = taken[:4], taken[5:7]
        kname = KIND_NAMES.get(kind, "Other")
        if mode == "album":
            ctx = self.side_filter[1] if self.side_filter[0] == "album" else None
            r = None
            if ctx:  # exporting the album being viewed: use it even if the image is in other albums too
                r = self.db.execute("SELECT a.name FROM albums a JOIN album_images ai ON ai.album_id=a.id "
                                    "WHERE a.id=? AND ai.image_id=?", (ctx, iid)).fetchone()
            if not r:
                r = self.db.execute("SELECT a.name FROM albums a JOIN album_images ai ON ai.album_id=a.id "
                                    "WHERE ai.image_id=? ORDER BY a.pos, a.name LIMIT 1", (iid,)).fetchone()
            return Path(safe_name(r[0]) if r else "No album")
        if mode == "y":
            return Path(y)
        if mode == "ym":
            return Path(y) / m
        if mode == "kind":
            return Path(kname)
        if mode == "kind_ym":
            return Path(kname) / y / m
        t = self.db.execute("SELECT tag FROM tags WHERE image_id=? ORDER BY tag LIMIT 1",
                            (iid,)).fetchone()
        return Path(safe_name(t[0]) if t else "Untagged")

    def sort_dialog(self, scope=None, mode=None):
        win = tk.Toplevel(self.root)
        win.title("Sort into folders")
        win.geometry("560x640")
        win.transient(self.root)
        win.grab_set()
        scope_var = tk.StringVar(value=scope or ("selected" if self.selected else "current"))
        mode_var = tk.StringVar(value=mode or "ym")
        action_var = tk.StringVar(value="copy")
        dest_var = tk.StringVar()

        def heading(text):
            ttk.Label(win, text=text, font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))

        def radios(var, options):
            for value, text in options:
                ttk.Radiobutton(win, text=text, variable=var, value=value).pack(anchor="w", padx=24)

        heading("Which images?")
        radios(scope_var, [("selected", "Only the selected images"),
                           ("current", "Everything showing now (current search / filter)"),
                           ("all", "Whole library")])
        heading("Folder structure")
        radios(mode_var, SORT_MODES)
        heading("Copy or move?")
        radios(action_var, [("copy", "Copy (originals stay where they are)"),
                            ("move", "Move (library is updated to the new locations)")])
        heading("Destination folder")
        drow = ttk.Frame(win)
        drow.pack(fill="x", padx=12)
        ttk.Entry(drow, textvariable=dest_var).pack(side="left", fill="x", expand=True)
        ttk.Button(drow, text="Browse…", command=lambda: dest_var.set(
            filedialog.askdirectory(parent=win, title="Choose destination") or dest_var.get()
        )).pack(side="left", padx=(6, 0))

        heading("Preview")
        box = tk.Text(win, height=10, wrap="none", state="disabled")
        box.pack(fill="both", expand=True, padx=12)
        status = ttk.Label(win, text="")
        status.pack(anchor="w", padx=12, pady=4)

        def preview():
            rows = self.sort_rows(scope_var.get())
            counts = Counter(str(self.folder_for(r, mode_var.get())) for r in rows)
            box.config(state="normal")
            box.delete("1.0", "end")
            if not rows:
                box.insert("end", "No images to sort.")
            for folder, n in sorted(counts.items()):
                box.insert("end", f"{folder}   ({n})\n")
            box.config(state="disabled")
            status.config(text=f"{len(rows)} images → {len(counts)} folders")

        def run():
            dest = dest_var.get().strip()
            if not dest:
                messagebox.showwarning("Sort", "Choose a destination folder first.", parent=win)
                return
            rows = self.sort_rows(scope_var.get())
            if not rows:
                return
            move = action_var.get() == "move"
            if move and not messagebox.askyesno(
                    "Move files", f"Move {len(rows)} files into '{dest}'?", parent=win):
                return
            dest, mode = Path(dest), mode_var.get()
            rp = self.begin_point(f"Move {len(rows)} files into folders") if move else None
            done = missing = failed = 0
            for n, r in enumerate(rows):
                src = Path(r[1])
                if not src.exists():
                    missing += 1
                    continue
                target = dest / self.folder_for(r, mode) / src.name
                if target.exists() and os.path.samefile(src, target):
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target = unique_path(target)
                    if move:
                        shutil.move(str(src), str(target))
                        self.log_move(rp, "move", str(src), str(target))
                        self.db.execute("UPDATE images SET path=? WHERE id=?", (str(target), r[0]))
                    else:
                        shutil.copy2(src, target)
                    done += 1
                except (OSError, sqlite3.IntegrityError):
                    failed += 1
                if n % 10 == 0:
                    status.config(text=f"Working… {done} done")
                    win.update_idletasks()
            self.db.commit()
            if move:
                self.end_point(rp, done > 0)
            messagebox.showinfo(
                "Sort finished",
                f"{'Moved' if move else 'Copied'} {done} images.\n"
                f"Missing files skipped: {missing}\nFailed: {failed}", parent=win)
            win.destroy()
            if move:
                self.cache.clear()
                self.refresh()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=10)
        ttk.Button(btns, text="Preview", command=preview).pack(side="left")
        ttk.Button(btns, text="Sort", command=run).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    # ---------- duplicates ----------
    def show_duplicates(self):
        sets = self.db.execute(
            "SELECT hash FROM images GROUP BY hash HAVING COUNT(*)>1").fetchall()
        win = tk.Toplevel(self.root)
        win.title("Duplicates")
        win.geometry("700x450")
        text = tk.Text(win, wrap="none")
        text.pack(fill="both", expand=True)
        if not sets:
            text.insert("end", "No exact duplicates found.")
        for n, (h,) in enumerate(sets, 1):
            text.insert("end", f"Set {n}\n")
            for (path,) in self.db.execute("SELECT path FROM images WHERE hash=?", (h,)):
                text.insert("end", f"   {path}\n")
            text.insert("end", "\n")
        text.config(state="disabled")


if __name__ == "__main__":
    try:
        root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
    except Exception:  # drag-and-drop library present but not usable
        root = tk.Tk()
    try:  # window, taskbar and dialog icon
        root.iconbitmap(default=str(resource_path("icon.ico")))
    except Exception:
        pass
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
