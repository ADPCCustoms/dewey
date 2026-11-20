# Image Library

A small desktop app for organising a mixed collection of images: photos, art and AI-generated pictures. It builds an index of your images, lets you tag and search them, finds duplicates, and can sort them into folders you choose.

Your original files are never changed. Nothing is moved or deleted unless you choose **Move** in the sort dialog.

## Features

- **Add folder**: index a folder and all its subfolders. Re-adding a folder only picks up new images.
- **Thumbnail grid** with a large preview, file details and embedded prompt (when the file has one).
- **Search** by filename, prompt text or tag. Several words must all match.
- **Filter by type**: photo, ai or other.
- **Tags**: add comma-separated tags to any image.
- **Find duplicates**: lists exact copies, grouped into sets.
- **Sort into folders**: copy or move images into a structure you pick:
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

`publish.bat` does everything in one go: commits your changes, creates the GitHub repo on the first run (or pushes to it afterwards), builds the exe, and publishes it as a release. It needs [Git](https://git-scm.com) and the [GitHub CLI](https://cli.github.com), and you must be signed in with `gh auth login`.

## How image types are detected

- **photo**: the file has an EXIF "date taken" value.
- **ai**: the file has a prompt in its metadata, an embedded "AI-generated" label, or "gemini" / "generated_image" in its filename.
- **other**: everything else.

Detection isn't perfect, since many tools strip metadata, so use **Mark showing as** to correct types in bulk.

## Notes

- `library.db` contains the paths, tags and prompts of your images, so it is excluded from Git by `.gitignore`.
- Supported formats: JPG, PNG, WebP, GIF.
