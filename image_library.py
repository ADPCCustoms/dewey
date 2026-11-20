"""Image Library - index, tag and search your images. Files are never moved or changed.
Requires: pip install pillow
"""
import hashlib, os, shutil, sqlite3, subprocess, sys
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
        root.geometry("1200x720")
        self.db = sqlite3.connect(DB_PATH)
        self.db.executescript(SCHEMA)
        self.page, self.rows, self.total = 0, [], 0
        self.cache, self.cols = {}, 5
        self.sel_id, self.sel_path, self.sel_widget = None, None, None
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
        ttk.Button(d, text="Save tags", command=self.save_tags).pack(anchor="w", pady=4)
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
            "SELECT id, path FROM images" + w + " ORDER BY taken DESC LIMIT ? OFFSET ?",
            p + [PAGE, self.page * PAGE]).fetchall()
        pages = max(1, (self.total + PAGE - 1) // PAGE)
        self.page_label.config(text=f"Page {self.page + 1} of {pages}  ({self.total} images)")
        self.render()

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
        self.sel_widget = None
        for n, (iid, path) in enumerate(self.rows):
            lbl = tk.Label(self.grid_frame, image=self.thumb(path), bd=3, cursor="hand2")
            lbl.grid(row=n // self.cols, column=n % self.cols, padx=4, pady=4)
            lbl.bind("<Button-1>", lambda e, i=iid, p=path, w=lbl: self.select(i, p, w))
            if iid == self.sel_id:
                lbl.config(relief="solid")
                self.sel_widget = lbl
        self.canvas.yview_moveto(0)

    # ---------- selection / tags ----------
    def select(self, iid, path, widget):
        if self.sel_widget is not None:
            try:
                self.sel_widget.config(relief="flat")
            except tk.TclError:
                pass
        widget.config(relief="solid")
        self.sel_id, self.sel_path, self.sel_widget = iid, path, widget
        taken, kind, prompt = self.db.execute(
            "SELECT taken, kind, prompt FROM images WHERE id=?", (iid,)).fetchone()
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((290, 260))
            self._preview_img = ImageTk.PhotoImage(img)
            self.preview.config(image=self._preview_img, text="")
        except Exception:
            self.preview.config(image="", text="(file missing)")
        self.info.config(text=f"{Path(path).name}\n{kind} · {taken[:10]}\n{path}")
        tags = [r[0] for r in self.db.execute(
            "SELECT tag FROM tags WHERE image_id=? ORDER BY tag", (iid,))]
        self.tags_var.set(", ".join(tags))
        self.prompt.config(state="normal")
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", prompt or "")
        self.prompt.config(state="disabled")

    def save_tags(self):
        if self.sel_id is None:
            return
        tags = {t.strip().lower() for t in self.tags_var.get().split(",") if t.strip()}
        self.db.execute("DELETE FROM tags WHERE image_id=?", (self.sel_id,))
        self.db.executemany("INSERT OR IGNORE INTO tags VALUES(?,?)",
                            [(self.sel_id, t) for t in tags])
        self.db.commit()
        self.status.config(text="Tags saved")

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

    # ---------- sort into folders ----------
    def sort_rows(self, scope):
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
        scope_var = tk.StringVar(value="current")
        mode_var = tk.StringVar(value="ym")
        action_var = tk.StringVar(value="copy")
        dest_var = tk.StringVar()

        def heading(text):
            ttk.Label(win, text=text, font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))

        def radios(var, options):
            for value, text in options:
                ttk.Radiobutton(win, text=text, variable=var, value=value).pack(anchor="w", padx=24)

        heading("Which images?")
        radios(scope_var, [("current", "Only what's showing now (current search / filter)"),
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
