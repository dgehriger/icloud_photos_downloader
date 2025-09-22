Use a persistent “download ledger” to block re-downloads even if files are deleted locally. Store per-asset tombstones keyed by immutable iCloud IDs. Default DB location is the user’s **Documents** so OneDrive sync captures it.

## Objectives

* Never re-download an asset once successfully fetched, unless explicitly overridden.
* Respect `--skip-created-before` and record policy skips.
* Work across Personal vs Shared libraries without collisions.
* Zero external services. Single file DB. Crash-safe.

## Storage and paths

* DB: SQLite in WAL mode.
* Windows default: `%USERPROFILE%\Documents\icloudpd\downloads.db`

  * Resolve via **Known Folders** so OneDrive redirection is honored.
* POSIX default: `~/.config/icloudpd/downloads.db`
* CLI override: `--download-db PATH`

### Windows path resolution

```python
# icloudpd/paths.py
from __future__ import annotations
import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import sys

def default_download_db_path() -> Path:
    if os.name != 'nt':
        return Path.home() / '.config' / 'icloudpd' / 'downloads.db'

    FOLDERID_Documents = ctypes.c_wchar_p('{FDD39AD0-238F-46AF-ADB4-6C85480369C7}')
    SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
    SHGetKnownFolderPath.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    SHGetKnownFolderPath.restype = wintypes.HRESULT
    p = wintypes.LPWSTR()
    hr = SHGetKnownFolderPath(FOLDERID_Documents, 0, None, ctypes.byref(p))
    try:
        doc = Path(p.value) if hr == 0 and p.value else (Path.home() / 'Documents')
    finally:
        if p:
            ctypes.windll.ole32.CoTaskMemFree(p)
    return doc / 'icloudpd' / 'downloads.db'
```

## Schema v1

```sql
CREATE TABLE IF NOT EXISTS downloads (
  asset_id            TEXT PRIMARY KEY,        -- immutable iCloud asset GUID
  master_record_id    TEXT,                    -- secondary ID if exposed
  library_kind        TEXT,                    -- personal|shared
  asset_type          TEXT,                    -- photo|video|live_photo_photo|live_photo_video|raw|sidecar
  icloud_filename     TEXT,
  bytes               INTEGER,
  created_utc         TEXT,                    -- ISO8601 UTC (asset/EXIF creation)
  original_checksum   TEXT,                    -- if API provides
  local_sha1          TEXT,                    -- optional; gated by flag
  last_local_path     TEXT,
  status              TEXT NOT NULL,           -- downloaded|skipped_by_policy|error|forgotten
  first_seen_utc      TEXT NOT NULL,
  last_seen_utc       TEXT NOT NULL,
  last_attempt_utc    TEXT,
  last_error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_downloads_master ON downloads(master_record_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
PRAGMA user_version = 1;
PRAGMA journal_mode = WAL;
```

## CLI additions

* `--download-db PATH` override.
* `--no-download-db` disable ledger logic.
* `--redownload` ignore `status='downloaded'`.
* `--forget-downloaded` with filters (date/ids) to clear tombstones.
* `--db-export CSV_PATH` dump rows.
* `--db-vacuum`
* `--db-seed PATH` one-time seed from an existing cache.

## Integration points

1. **Init**

   * Resolve DB path. `mkdir -p` parent. Open SQLite. Set WAL. Ensure schema. Migrate if needed.
   * If legacy DB exists at old default and new path empty, move legacy to new path.

     * Legacy Windows: none (new feature). POSIX legacy: `~/.config/icloudpd/downloads.db`.

2. **Enumeration**

   * For each asset `A`:

     * `row = db.get(A.asset_id)`
     * If `row.status == 'downloaded'` and not `--redownload`: **skip**.
     * Apply date filter: if `created < --skip-created-before`, `upsert(status='skipped_by_policy')` and **skip**.
     * Record `first_seen_utc` / `last_seen_utc` when first observed.

3. **Download**

   * On attempt: set `last_attempt_utc`.
   * Download to temp. Move to final naming path. Optional checksum verify.
   * On success: `upsert(status='downloaded', bytes, created_utc, checksums, last_local_path, filenames, asset_type, library_kind)`.
   * On failure: `upsert(status='error', last_error=...)`.

4. **Shared vs Personal**

   * Pass current library selection into `library_kind`.
   * Key remains `asset_id`. If Apple reuses IDs across libs (unlikely), composite key would be `(asset_id, library_kind)`. Keep single key unless a collision is observed in tests.

## DB module outline

```python
# icloudpd/db.py
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Optional, Dict

@dataclass
class Row:
    asset_id: str
    status: str
    # other fields...

class Database:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # autocommit
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._ensure_schema()

    def _ensure_schema(self):
        self.conn.executescript(SCHEMA_SQL)

    def get(self, asset_id: str) -> Optional[Row]:
        cur = self.conn.execute("SELECT asset_id,status,master_record_id,... FROM downloads WHERE asset_id=?", (asset_id,))
        r = cur.fetchone()
        return Row(*r) if r else None

    def upsert(self, **fields):
        cols = ",".join(fields.keys())
        vals = tuple(fields.values())
        placeholders = ",".join("?" for _ in fields)
        update = ",".join(f"{k}=excluded.{k}" for k in fields.keys())
        self.conn.execute(
            f"INSERT INTO downloads ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(asset_id) DO UPDATE SET {update}",
            vals
        )

    def export_csv(self, path: Path):
        # simple SELECT * INTO csv
        ...
```

## Main wiring

```python
# icloudpd/__main__.py (args)
parser.add_argument('--download-db', default=None)
parser.add_argument('--no-download-db', action='store_true')
parser.add_argument('--redownload', action='store_true')
parser.add_argument('--forget-downloaded', action='store_true')
parser.add_argument('--db-export')
parser.add_argument('--db-vacuum', action='store_true')
parser.add_argument('--db-seed')

# resolve path
from icloudpd.paths import default_download_db_path
db_path = Path(args.download_db) if args.download_db else default_download_db_path()
if not args.no_download_db:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    # legacy POSIX migration
    legacy = Path.home() / '.config' / 'icloudpd' / 'downloads.db'
    if not args.download_db and legacy.exists() and not db_path.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
        legacy.replace(db_path)

# in enumeration loop
if not args.no_download_db:
    row = db.get(asset.asset_id)
    if row and row.status == 'downloaded' and not args.redownload:
        continue
    if asset.created_utc and asset.created_utc < args.skip_created_before:
        db.upsert(asset_id=asset.asset_id,
                  status='skipped_by_policy',
                  first_seen_utc=now_iso(),
                  last_seen_utc=now_iso(),
                  created_utc=asset.created_utc,
                  library_kind=current_library_kind,
                  icloud_filename=asset.filename)
        continue

# on success
if not args.no_download_db:
    db.upsert(asset_id=asset.asset_id,
              master_record_id=asset.master_id,
              status='downloaded',
              icloud_filename=asset.filename,
              bytes=os.path.getsize(final_path),
              created_utc=asset.created_utc,
              original_checksum=asset.checksum,
              local_sha1=(sha1(final_path) if args.compute_sha1 else None),
              last_local_path=str(final_path),
              library_kind=current_library_kind,
              first_seen_utc=row.first_seen_utc if row else now_iso(),
              last_seen_utc=now_iso(),
              last_attempt_utc=now_iso())
```

## Seeding from existing cache

Goal: mark already-downloaded assets so they never fetch again.

* `--db-seed PATH`:

  * Walk files. Extract candidate `(filename, bytes, EXIF DateTimeOriginal UTC)`.
  * Match against iCloud assets:

    1. Exact filename + bytes.
    2. `|created_utc − asset.created_utc| ≤ 60 s` + bytes.
    3. Optional SHA1 sidecar match if present.
  * On confident match: insert `status='downloaded'` with `last_local_path`.
  * Log ambiguous cases to CSV.

## Forgetting tombstones

* `--forget-downloaded` accepts filters:

  * `--filter-created-after`, `--filter-created-before`, `--filter-ids FILE` (CSV list of `asset_id`).
* Implementation: `UPDATE downloads SET status='forgotten' WHERE status='downloaded' AND <filters>`.

## Tests

* Path resolution returns redirected OneDrive Documents on Windows (mock `SHGetKnownFolderPath`).
* Tombstone skip: asset with `downloaded` status is not fetched, file missing or not.
* Date policy writes `skipped_by_policy`.
* `--redownload` bypasses tombstone.
* Seeding marks rows and prevents download.
* WAL mode enabled and concurrent read safe.

## Performance

* Keep transactions short. One UPSERT per asset. Batch only for seed.
* Add `--until-found N` fast-stop remains compatible.
* Avoid hashing large videos unless `--compute-sha1` set.

## Logging

* On skip due to tombstone: single INFO line with `asset_id`.
* On policy skip: INFO with reason and dates.
* On success: INFO with `asset_id`, bytes, path.
* On error: WARN with `asset_id` and message.

## Backward compatibility

* Defaults unchanged unless user relies on legacy POSIX DB location; migration moves it once when not overridden.
* When `--no-download-db` set, behavior reverts to upstream.

## Edge cases

* Asset edited in iCloud resulting in a new `asset_id`: downloads once, then tombstone. If same ID but changed checksum/bytes, first success wins unless `--redownload`.
* Live Photos: photo and motion video are separate rows; both tombstoned after success.
* Shared library (if later enabled): set `library_kind` to avoid ambiguity in reporting; key remains `asset_id`.

This is sufficient for the agent to implement, test, and document.
