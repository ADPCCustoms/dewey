## What's new in v1.4

- Adding a folder now runs in the background with a progress bar and a Cancel button, so the window stays responsive
- Re-adding a folder is much faster: files already in the library are skipped without being read
- Thumbnails are cached on disk and load in the background, so pages appear straight away after the first view
- Automatic backup: library.db is checked on launch and copied to library.db.bak when healthy, with a warning if it looks damaged
- More robust thumbnail handling on very large libraries

Existing libraries work as they are. A `thumbs` folder appears next to the app and can be deleted at any time.
