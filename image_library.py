"""Image Library - index, tag and search your images. Files are never moved or changed.
Requires: pip install pillow
"""
import hashlib, os, re, shutil, sqlite3, subprocess, sys
import tkinter as tk
from collections import Counter
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
DB_PATH = BASE / "library.db"
THUMB, PAGE = 140, 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS images(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, hash TEXT,
  taken TEXT, kind TEXT, prompt TEXT);
CREATE TABLE IF NOT EXISTS tags(
  image_id INTEGER, tag TEXT, UNIQUE(image_id, tag));
"""


SORT_MODES = [
    ("ym", "Year / Month   (e.g. 2026/11)"),
    ("y", "Year only   (e.g. 2026)"),
    ("kind", "Type   (Photos, AI Images, Other)"),
    ("kind_ym", "Type, then Year / Month"),
    ("tag", "Tag   (first tag; untagged go in 'Untagged')"),
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
        self.db = sqlite3.connect(DB_PATH)
        self.db.executescript(SCHEMA)
        for col in ("fav INTEGER DEFAULT 0", "rating INTEGER DEFAULT 0"):
            try:
                self.db.execute(f"ALTER TABLE images ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # column already exists
        self.page, self.rows, self.total = 0, [], 0
        self.cache, self.cols = {}, 5
        self.normal_bg = root.cget("bg")
        self.selected, self.primary, self.anchor = set(), None, None
        self.sel_path, self.widgets = None, {}
        self.side_filter, self.side_items = ("all", None), []
        self.debounce = None
        self.build_ui()
        self.refresh()

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
        row = ttk.Frame(d)
        row.pack(fill="x")
        ttk.Button(row, text="Open", command=self.open_file).pack(side="left")
        ttk.Button(row, text="Show in folder", command=self.show_in_folder).pack(side="left", padx=4)
        ttk.Label(d, text="Prompt / metadata:").pack(anchor="w", pady=(8, 0))
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

    def on_resize(self, event):
        cols = max(1, event.width // (THUMB + 16))
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
            "SELECT id, path, fav FROM images" + w + " ORDER BY taken DESC LIMIT ? OFFSET ?",
            p + [PAGE, self.page * PAGE]).fetchall()
        pages = max(1, (self.total + PAGE - 1) // PAGE)
        self.page_label.config(text=f"Page {self.page + 1} of {pages}  ({self.total} images)")
        self.render()
        self.selected &= {r[0] for r in self.rows}
        if self.primary not in self.selected:
            self.primary = next(iter(self.selected), None)
        self.update_selection()
        self.refresh_sidebar()

    def thumb(self, path):
        if path in self.cache:
            return self.cache[path]
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((THUMB, THUMB))
        except Exception:
            img = Image.new("RGB", (THUMB, THUMB), "#888888")
        if len(self.cache) > 600:
            self.cache.clear()
        self.cache[path] = ImageTk.PhotoImage(img)
        return self.cache[path]

    def render(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.widgets = {}
        for n, (iid, path, fav) in enumerate(self.rows):
            lbl = tk.Label(self.grid_frame, image=self.thumb(path), bd=0, padx=3, pady=3,
                           cursor="hand2")
            lbl.grid(row=n // self.cols, column=n % self.cols, padx=3, pady=3)
            lbl.bind("<Button-1>", lambda e, i=iid: self.on_click(e, i))
            self.widgets[iid] = lbl
        self.recolor()
        self.canvas.yview_moveto(0)

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
        self.db.executemany("INSERT OR IGNORE INTO tags VALUES(?,?)",
                            [(i, t) for i in self.selected for t in tags])
        self.db.commit()
        self.status.config(text=f"Added to {len(self.selected)} images")
        self.after_tag_change()

    def remove_tags_selected(self):
        tags = self.parse_tags()
        if not self.selected or not tags:
            return
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

    # ---------- indexing ----------
    def add_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of images to add")
        if not folder:
            return
        added = 0
        for n, p in enumerate(Path(folder).rglob("*")):
            if p.suffix.lower() not in EXTS:
                continue
            try:
                h = hashlib.sha1(p.read_bytes()).hexdigest()
                taken, kind, prompt = file_info(p)
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO images(path,hash,taken,kind,prompt) VALUES(?,?,?,?,?)",
                    (str(p), h, taken, kind, prompt))
                added += cur.rowcount
            except OSError:
                continue
            if n % 20 == 0:
                self.status.config(text=f"Indexing… {added} added")
                self.root.update_idletasks()
        self.db.commit()
        self.status.config(text=f"Added {added} new images")
        self.reset_page()

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
        self.db.execute("UPDATE images SET kind=?" + w, [kind] + p)
        self.db.commit()
        self.status.config(text=f"Marked {self.total} as {kind}")
        self.refresh()

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
                    self.db.execute("UPDATE images SET path=? WHERE id=?", (str(tgt), iid))
                    done += 1
                except (OSError, sqlite3.IntegrityError):
                    failed += 1
            self.db.commit()
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

    def sort_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Sort into folders")
        win.geometry("560x640")
        win.transient(self.root)
        win.grab_set()
        scope_var = tk.StringVar(value="selected" if self.selected else "current")
        mode_var = tk.StringVar(value="ym")
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
    root = tk.Tk()
    App(root)
    root.mainloop()
