# Image Library

A small desktop app for organising a mixed collection of images: photos, art and AI-generated pictures. It builds an index of your images, lets you tag and search them, finds duplicates, and can sort them into folders you choose.

Your original files are never changed. Nothing is moved or deleted unless you choose **Move** in the sort dialog.

## Features

- **Add folder**: index a folder and all its subfolders in the background, with a progress bar and Cancel button. Re-adding a folder only picks up new images and skips known files without reading them.
- **Watch folders**: choose folders to monitor. New images are added automatically at startup and then every minute while the app is open. Files still being downloaded are skipped until they finish.
- **Drag and drop**: drop folders or image files onto the window to add them (needs the optional `tkinterdnd2` package, see below).
- **Restore points**: before risky changes (bulk rename, move-sort, deleting duplicates, clean up, bulk tag/rating/favourite on 10+ images) the app snapshots your library and logs every file it moves, renames or deletes. **Restore points…** previews and restores to any of the newest 20, undoing later changes too. Deleted duplicates wait in the app trash (`restore/trash`), so they come back with the restore. Copy-mode sorting is not covered, and edits made to image contents outside the app are not reverted.
- **Dark mode**: toggle in the top bar; your choice is remembered.
- **Thumbnail size**: slider in the bottom bar (80 to 280 px) or Ctrl + mouse wheel over the grid; remembered between sessions.
- **Thumbnail grid** with a large preview, file details and embedded prompt (when the file has one).
- **Search** by filename, prompt text or tag. Several words must all match.
- **Filter by type**: photo, ai or other.
- **Select several images**: click, Ctrl-click to add or remove one, Shift-click for a range, or **Select page**.
- **Tags**: add comma-separated tags to one image, or use **Add to all selected** / **Remove** to tag many at once.
- **Browse sidebar**: All images, Favourites, Rated 4+, Untagged, and every tag with its count. Click one to filter.
- **Favourites and ratings**: mark a favourite (gold border in the grid) and give 0 to 5 stars, for one or many selected images.
- **Rename in bulk**: patterns such as `{date}_{n:03}` using `{date}`, `{type}`, `{tag}`, `{name}` and a counter, with a preview first.
- **Full-size viewer**: double-click a thumbnail (or press **View**). Left/Right arrows browse the current search or filter, 0-5 rates, F toggles favourite, Esc closes.
- **Sort**: newest, oldest, name A-Z / Z-A, highest rated, favourites first.
- **Find duplicates**: lists exact copies, grouped into sets.
- **Find similar**: finds look-alike images (the same picture resized, re-saved at another quality, or with slightly different brightness). Pick a strictness, review each group side by side, tick the ones to keep (the largest is ticked by default) and the rest go to the app trash (undoable). It does not detect rotated, flipped or heavily cropped copies.
- **Delete duplicates**: choose which copy to keep, preview the sets, then move the extras to the app trash (undoable). Tags, favourite and rating are merged into the copy you keep.
- **Clean up missing files**: remove library entries whose files have been deleted or moved (no image files are touched).
- **Copy prompt**: copies the embedded prompt of the selected image to the clipboard.
- **Sort into folders**: copy or move the selected images, everything showing, or the whole library into a structure you pick:
  - Year / Month
  - Year only
  - Type (Photos, AI Images, Other)
  - Type, then Year / Month
  - Tag (first tag; untagged go in `Untagged`)

  A preview shows the folders and image counts before anything happens.
- **Mark showing as**: set the type (photo / ai / other) for every image matching the current search or filter.

## Requirements

- Python 3.9 or newer
- Pillow

```
pip install -r requirements.txt
```

Optional, for drag and drop:

```
pip install tkinterdnd2
```

The app works without it; you just use the **Add folder** button instead.

## Run

```
python image_library.py
```

The library is saved as `library.db` next to the script (or next to the exe). On each launch it is health-checked and, if healthy, copied to `library.db.bak`. Thumbnails are cached in a `thumbs` folder next to it, which is safe to delete.

## Build an .exe (Windows)

Double-click `build.bat`, or run:

```
pip install pyinstaller
pyinstaller --onefile --windowed --name ImageLibrary image_library.py
```

The exe appears in the `dist` folder. To use a custom icon, put an `icon.ico` file next to `build.bat` before building.

Keep `library.db` alongside the exe. Rebuilding never touches the database.

## Publish to GitHub (Windows)

Each version ships with three small files: `version.txt` (the tag, for example `v1.3`), `release-notes.md` (what's new in this version) and `CHANGELOG.md` (the running history). Running `publish.bat` then does everything: builds the exe, adds a SmartScreen note and the exe's SHA256 checksum to the release notes, commits and pushes the code, and publishes the release. It stops early if that version already exists on GitHub.

It needs [Git](https://git-scm.com) and the [GitHub CLI](https://cli.github.com), and you must be signed in with `gh auth login`.

## Windows SmartScreen warning

The exe isn't code-signed, so Windows may show "Windows protected your PC". Click **More info**, then **Run anyway**. To check a download is the file that was published, compare its checksum with the SHA256 in the release notes:

```
Get-FileHash .\ImageLibrary.exe -Algorithm SHA256
```

## License

MIT, see [LICENSE](LICENSE).

## How image types are detected

- **photo**: the file has an EXIF "date taken" value.
- **ai**: the file has a prompt in its metadata, an embedded "AI-generated" label, or "gemini" / "generated_image" in its filename.
- **other**: everything else.

Detection isn't perfect, since many tools strip metadata, so use **Mark showing as** to correct types in bulk.

## Notes

- `library.db` contains the paths, tags and prompts of your images, so it is excluded from Git by `.gitignore`.
- Supported formats: JPG, PNG, WebP, GIF.
