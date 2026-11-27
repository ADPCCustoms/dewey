# Changelog

## v1.18
- Export resized or converted copies (JPG, PNG, WebP) with size modes, quality, name patterns, presets and a preview
- Metadata stripped by default, optional keep; transparency handled for JPG; EXIF rotation applied
- Background export with progress and cancel; originals and library untouched
- Ctrl+E, side panel button and album right-click entry

## v1.17
- Compare view: two images side by side with linked zoom and pan, or blink mode (manual and auto)
- Rate, favourite or trash either image from the compare window
- C key and Compare 2 selected button

## v1.16
- Viewer: mouse-wheel zoom, drag to pan, fit / 100% toggle, control bar
- Viewer: slideshow with adjustable delay (remembered), shuffle, looping; full screen with F11
- Viewer draws only the visible area, so large images stay smooth
- Images per page follow the thumbnail size (160 to 20), keeping your place when it changes

## v1.15
- Smart albums: save any view (search, sidebar view, type, detail filters) and it stays up to date
- Tag manager: rename, merge and delete tags library-wide, with restore points for large changes
- Ctrl+S saves a smart album, Ctrl+T opens the tag manager
- Filter boxes ignore nan / inf instead of failing

## v1.14
- Keyboard shortcuts (F1 lists them): arrows, Enter, Esc, Delete, 0-5 ratings, F favourite, F5, Ctrl+F, Ctrl+O, Ctrl+A, Page Up/Down
- Detail filters: shape, width/height, file size, taken date, format
- Image sizes stored on import and read in the background for existing images
- Delete key moves images to the app trash with a restore point

## v1.13
- Renamed to Dewey (window title, Dewey.exe, README). Library files are unchanged

## v1.12
- New app icon (exe, window, taskbar, dialogs), drawn by make_icon.py

## v1.11
- Albums: virtual collections in the sidebar (add, remove, rename, reorder, delete)
- Right-click an album to copy or move its images into a real folder (new Album mode in Sort into folders)
- Albums covered by restore points and carried across when duplicates are merged

## v1.10
- Delete restore points by hand (multi-select), with an option to free the trash space they use
- File moves of a deleted point are merged into the nearest older point, so older restore points keep working

## v1.9
- Restore points: library snapshots plus a log of file moves, with preview and restore (itself undoable)
- Covers rename, move-sort, delete duplicates (both tools), clean up missing, mark type, bulk tag/rating/favourite
- Deleted duplicates go to an app trash so they can be restored; pruned or emptied trash goes to the Windows Recycle Bin
- Newest 20 restore points kept; settings are not rolled back

## v1.8
- Thumbnail size slider (80 to 280 px) and Ctrl + mouse wheel zoom, remembered between sessions
- Separate thumbnail cache per size

## v1.7
- Dark mode toggle, remembered between sessions
- Theme applies to the sidebar, dialogs, lists, text boxes and dropdowns
- Dark title bar on Windows 10 and 11

## v1.6
- Find similar: groups look-alike images (resized, re-saved, brightness changes) with a strictness slider
- Side-by-side review per group; keep the largest by default, others to the Recycle Bin with tags merged
- Images analysed once and remembered; analysis also warms the thumbnail cache
- Blank and flat-colour images are ignored

## v1.5
- Watch folders: new images in chosen folders are added automatically (at startup, then every minute)
- Drag and drop folders or image files onto the window
- Files still being written are skipped until they settle
- Individual image files can be added, not just folders

## v1.4
- Folder import runs in the background with progress bar and Cancel
- Re-adding a folder skips files already in the library without reading them
- Thumbnails cached on disk and loaded in the background
- Automatic library.db health check and backup (library.db.bak) on launch
- More robust thumbnail handling on very large libraries

## v1.3
- Full-size viewer with arrow-key browsing, quick rating and favourite
- Sort options: newest, oldest, name A-Z / Z-A, highest rated, favourites first
- Delete duplicates (to the Recycle Bin) with tag, favourite and rating merge
- Clean up missing files
- Copy prompt button

## v1.2
- Added the MIT license
- Release notes now include a SmartScreen note and the exe's SHA256 checksum

## v1.1
- Select several images; bulk tagging
- Browse sidebar: All images, Favourites, Rated 4+, Untagged and tags with counts
- Favourites and 0-5 star ratings
- Bulk rename with patterns
- Sort into folders can use just the selected images

## v1.0
- First release: index folders, search, tags, duplicate finder, sort into folders, mark type
