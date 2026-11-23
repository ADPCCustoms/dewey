## What's new in v1.9

- Restore points, in the style of System Restore: before a risky change the app saves a snapshot of your library and logs every file it moves, renames or deletes. A new Restore points button lists them, with a preview of what restoring would do, then puts the library and the files back as they were just before that change, undoing later changes too
- Covered: bulk rename, sort into folders with move, both delete-duplicates tools, clean up missing files, mark type, and bulk tag, rating and favourite changes on 10 or more images. You can also create a named restore point by hand
- Deleted duplicates now go to the app's own trash instead of the Windows Recycle Bin, so a restore can bring the files back. Files are only sent on to the Recycle Bin when their restore point is pruned or you empty the trash, so nothing is silently destroyed
- Restoring is itself undoable, and it checks first that every file can be put back, listing any that were moved or changed outside the app
- The newest 20 restore points are kept. Settings such as dark mode and thumbnail size are not rolled back

Existing libraries work as they are. A `restore` folder appears next to the app.
