# Image Library

A small desktop app for organising a mixed collection of images: photos, art and AI-generated pictures. It builds an index of your images, lets you tag and search them, finds duplicates, and can sort them into folders you choose.

Your original files are never changed. Nothing is moved or deleted unless you choose **Move** in the sort dialog.

## Features

- **Add folder**: index a folder and all its subfolders. Re-adding a folder only picks up new images.
- **Thumbnail grid** with a large preview, file details and embedded prompt (when the file has one).
- **Search** by filename, prompt text or tag. Several words must all match.
- **Filter by type**: photo, ai or other.
- **Select several images**: click, Ctrl-click to add or remove one, Shift-click for a range, or **Select page**.
- **Tags**: add comma-separated tags to one image, or use **Add to all selected** / **Remove** to tag many at once.
- **Browse sidebar**: All images, Favourites, Rated 4+, Untagged, and every tag with its count. Click one to filter.
- **Favourites and ratings**: mark a favourite (gold border in the grid) and give 0 to 5 stars, for one or many selected images.
- **Rename in bulk**: patterns such as `{date}_{n:03}` using `{date}`, `{type}`, `{tag}`, `{name}` and a counter, with a preview first.
- **Find duplicates**: lists exact copies, grouped into sets.
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

## Run

```
python image_library.py
```

The library is saved as `library.db` next to the script (or next to the exe).

## Build an .exe (Windows)

Double-click `build.bat`, or run:

```
pip install pyinstaller
pyinstaller --onefile --windowed --name ImageLibrary image_library.py
```

The exe appears in the `dist` folder. To use a custom icon, put an `icon.ico` file next to `build.bat` before building.

Keep `library.db` alongside the exe. Rebuilding never touches the database.

## Publish to GitHub (Windows)

`publish.bat` does everything in one go: asks for a version tag and short update notes, commits your changes, creates the GitHub repo on the first run (or pushes to it afterwards), builds the exe, and publishes it as a release with your notes and the exe's SHA256 checksum. It needs [Git](https://git-scm.com) and the [GitHub CLI](https://cli.github.com), and you must be signed in with `gh auth login`.

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
