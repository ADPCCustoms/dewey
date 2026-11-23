## What's new in v1.10

- Delete restore points by hand: select one or more in the Restore points window (Ctrl or Shift) and press Delete selected. This sits alongside the automatic pruning that keeps the newest 20
- Deleting a point never breaks the others: the files it moved are handed to the nearest older point, so restoring to an older point still puts everything back
- Optional tickbox to free the trash space used by the deleted points. Their deleted files then go to the Windows Recycle Bin, and older restore points can no longer bring those files back (they say so in the preview)
- If nothing older exists to bring trashed files back, they go to the Recycle Bin automatically rather than being left behind or destroyed

Existing libraries and restore points work as they are.
