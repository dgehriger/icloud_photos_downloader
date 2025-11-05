# Re-sync NAS photos with iCloud Photos

This task is unrelated to the other [task](./TASK.md) in this repository, which is used to download photos from personal iCloud Photos to the _Camera Roll_ folder on a personal computer.

It is, however, related to our [iCloud Photo Uploader](../photos/README.md) project, which is used to upload photos from the NAS to iCloud Photos. This tool is configured to take photos from the NAS at `\\fajita.local\Multimedia\01_Photos\**` and upload them to iCloud Photos to a dedicated account `photos.gehriger@gmail.com`. From there, I manually move them from the Personal Library to the Shared Library.

## Background

### A) Initial upload

I used the [iCloud Photo Uploader](../photos/README.md) to upload most of the photos from our NAS to iCloud Photos. I then manually moved them from the Personal Library to the Shared Library.

**CRITICAL**: As part of this upload, any photo stored in HEIC format was automatically converted to JPEG format, as iCloud Photos does not support HEIC uploads from third-party tools. The converted file is only stored in iCloud Photos, and the original HEIC file remains on the NAS.

**CRITICAL**: the upload tool uses a SQLite database ("upload DB") to track which photos have already been uploaded, to avoid duplicate uploads. This database is stored in `/share/CACHEDEV1_DATA/.local/share/icloud-photo-uploader/uploads.db`. You can fetch it using `scp admin@192.168.42.2 :/share/CACHEDEV1_DATA/.local/share/icloud-photo-uploader/uploads.db .`.

### B) Editing on iCloud

After having uploaded all 20k+ photos from the NAS to iCloud Photos, I realized that some photos:

1. Show up with a wrong date in iCloud Photos, even though their folder on the NAS is named correctly (e.g. `2020-12-31 New Year's Eve`).
2. Some photos have wrong orientation.
3. Some photos have missing geo-location metadata.

I hand-fixed most of the issues 1-3 on iCloud (using an iPad).

### C) Reorganization on NAS

The folder structure on the NAS wasn't consistent, and I created [a script](./reorg-script/organize_photos.py) to force a strict folder structure of `\\fajita.local\Multimedia\01_Photos\YYYY\MM\*` for all photos.

All operations were logged and the log (*.log and*.json) files are available in the `./reorg-script/logs` folder.

### D) Co-worker edits

At the same time, my co-worker did the same for a small subset of photos, but on the NAS. She also:

- Decided to delete some photos, and marked those photos with a leading `-` in the filename, e.g. `-IMG_1234.JPG`.
- Edited some photos, and prefixed those photos with a leading `+` in the filename, e.g. `+IMG_5678.JPG`.

## Re-syncing

The goal of this task is to plan the following resync of the photos. For this, we need to perform three distinctive phases:

### Phase 1: Resync the upload DB

- Adjust paths using reorg logs; keep iCloud asset IDs.
- Maintain a HEIC↔JPEG mapping table for items converted on upload.
- Dry-run report of remapped, orphaned, and ambiguous entries.

1. If needed (maybe the DB is agnostic to source folder), ensure that the "upload DB" is fixed to reflect the restructured folder structure on the NAS after step C above. Do NOT consider the added prefixes `+` or `-` yet.
2. Re-upload the DB to the NAS.

### Phase 2: Fix meta-data on NAS

**Global rules**

- Use `exiftool` on Windows to write EXIF.
- Timezone: Europe/Zurich. If folder lacks a day, set `YYYY-MM-15 12:00`.
- Add `=` prefix on any task-applied metadata fix **that isn't yet reflected on iCloud**. Combine with existing `+` if present.
- No other renames or normalization.

The purpose of this task is to fix the meta-data on the NAS: Date Taken, Orientation, and Geo-location. We have the following possible sources of truth, **in order of reliability**:

1. The folder name on the NAS (for Date Taken). Note that some of the more detailed information (e.g. day of month or time of day) may have been lost during the reorganization step C above, but the original folder names are available in the log files of the reorganization script.
2. If different from the original photo's EXIF data, the EXIF data from the photo in iCloud Photos.
3. Edited photo on the NAS by my co-worker (EXIF data), during step D above.
4. Original photo on the NAS (EXIF data).

We shall separate handling of the three EXIF fields separately, but ensure that data processed for one field is cached and reused for the other fields, to avoid multiple downloads of the same photo from iCloud Photos.

#### Date Taken

The following rules should be applied:

1. Recursively walk all photos on the NAS below `\\fajita.local\Multimedia\01_Photos\`.
2. Check if the photo was uploaded to iCloud Photos (using the upload DB). If yes, fetch the photo's iCloud EXIF data.
3. Compare the iCloud EXIF Date Taken with the original NAS EXIF Date Taken -> record outcome.
4. For each photo, check if the Date Taken EXIF field reflects the photo's current folder location (i.e. YYYY/MM), and also the **original folder location from the reorg log files** (which may contain day-level precision like `2020-12-31 New Year's Eve`; pre-process logs into a SQLite DB for easy access). The date extraction logic from original folder names is captured in [organize_photos.py](./reorg-script/organize_photos.py) `DATE_PATTERNS`. -> record outcome.
5. Check the photo's last modified timestamp on the NAS -> record outcome.
6. Based on the above information, decide if the Date Taken EXIF field needs to be updated, and if yes, to which value. The rules are:
   - If only the folder name indicates a different date, prefer the **original folder name from reorg logs** (more precise) over current `YYYY/MM` structure. Use that date and prefix with `=`.
   - If only the iCloud EXIF Date Taken indicates a different date, use that date, unless it would move the photo to a different month or year than indicated by the folder name on the NAS. Do not add `=` in that case, as no iCloud change is needed.
   - If both the folder name (from logs) and iCloud EXIF Date Taken indicate different dates, prompt the user to decide which date to use.

#### Orientation

The following rules should be applied:

1. Recursively walk all photos on the NAS below `\\fajita.local\Multimedia\01_Photos\`.
2. Check if the photo was uploaded to iCloud Photos (using the upload DB). If yes, fetch the photo's iCloud EXIF data.
3. Compare the iCloud EXIF Orientation with the original NAS EXIF Orientation -> record outcome
4. Check if the photo was edited on the NAS by my co-worker (i.e. has a leading `+` in the filename) -> record outcome
5. Based on the above information, decide if the Orientation EXIF field needs to be updated, and if yes, to which value. The rules are:
   - If only the iCloud EXIF Orientation indicates a different orientation, use that orientation. Do NOT add `=` in that case, as no iCloud change is needed.
   - If only the photo was edited on the NAS by my co-worker, assume that the orientation is correct and prefix with `=` to trigger iCloud update.
   - If both the iCloud EXIF Orientation indicates a different orientation and the photo was edited on the NAS by my co-worker, prompt the user to decide which orientation to use.

NOTE: HEIC files on the NAS are JPEG on iCloud!

#### Geo-location

The following rules should be applied:

1. Recursively walk all photos on the NAS below `\\fajita.local\Multimedia\01_Photos\`.
2. Check if the photo was uploaded to iCloud Photos (using the upload DB). If yes, fetch the photo's iCloud EXIF data.
3. Compare the iCloud EXIF Geo-location with the original NAS EXIF Geo-location -> record outcome
4. Check if the photo was edited on the NAS by my co-worker (i.e. has a leading `+` in the filename) -> record outcome
5. Based on the above information, decide if the Geo-location EXIF field needs to be updated, and if yes, to which value. The rules are:
   - If only the iCloud EXIF Geo-location indicates a different geo-location, use that geo-location. Do NOT add `=` in that case, as no iCloud change is needed.
   - If only the photo was edited on the NAS by my co-worker, assume that the geo-location is correct and prefix with `=` to trigger iCloud update.
   - If both the iCloud EXIF Geo-location indicates a different geo-location and the photo was edited on the NAS by my co-worker, prompt the user to decide which geo-location to use.

### Phase 3: Removals (iCloud + move on NAS)

The purpose of this task is to delete photos that my co-worker marked for deletion by prefixing the filename with a leading `-`.

1. Recursively walk all photos on the NAS below `\\fajita.local\Multimedia\01_Photos\`.
2. If filename starts with `-` and asset exists on iCloud, delete it from iCloud first.
3. On success, move the NAS file to `\\fajita.local\Multimedia\05_Deleted\YYYY\MM\*`. If year/month unknown, move to `\\fajita.local\Multimedia\05_Deleted\_Unknown\`.
4. If the NAS move fails, keep the `-` and retry in next batch.

### Phase 4: Re-upload or patch iCloud

My co-worker adjusts (crops, brightness, etc.) some photos on the NAS, and prefixes those photos with a leading `+` in the filename. The task also adds `=` prefix when it modifies metadata that isn't yet reflected on iCloud.

1. Recursively walk all photos on the NAS below `\\fajita.local\Multimedia\01_Photos\`.
2. For any `+` (co-worker edits) or `=` (task-applied metadata fixes): try iCloud EXIF write if supported; otherwise delete from iCloud, clear from upload DB, and let next uploader run re-upload.
3. After successful reflection in iCloud, strip `+`/`=` prefixes; keep `-` only in Deleted area.
4. Photos without `+` or `=` prefix require no iCloud update (no changes were made).

### Review and integrity

- Produce `review_changes.csv`, `orphans.csv` (iCloud-only), `ghosts.csv` (NAS-only), and `actions_applied.csv`.
- Canary mode: run full Phases 1–4 on first ~1000 assets, then scale.
- Final integrity: sample 1% and verify NAS EXIF matches iCloud.
