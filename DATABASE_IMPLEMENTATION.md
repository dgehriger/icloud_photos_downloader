# Download Database Implementation Summary

This document summarizes the implementation of the persistent "download ledger" feature for icloud_photos_downloader.

## Overview

The download database provides persistent tracking of downloaded assets to prevent re-downloads even if files are deleted locally. It stores per-asset tombstones keyed by immutable iCloud IDs and defaults to the user's Documents folder for OneDrive sync compatibility.

## Implementation Details

### Core Components

#### 1. Path Resolution (`src/icloudpd/paths.py`)
- **Windows**: Uses Known Folders API to resolve `%USERPROFILE%\Documents\icloudpd\downloads.db` with OneDrive redirection support
- **POSIX**: Uses `~/.config/icloudpd/downloads.db`
- **Function**: `default_download_db_path() -> Path`

#### 2. Database Module (`src/icloudpd/db.py`)
- **SQLite** database with WAL mode for crash safety and concurrent access
- **Schema v1** with downloads table containing:
  - `asset_id` (PRIMARY KEY) - immutable iCloud asset GUID
  - `master_record_id` - secondary ID if exposed
  - `library_kind` - personal|shared|seeded
  - `asset_type` - photo|video|live_photo_photo|live_photo_video|raw|sidecar
  - `status` - downloaded|skipped_by_policy|error|forgotten
  - File metadata (filename, size, checksums, paths, timestamps)
- **Database class** with methods:
  - `get(asset_id)` - retrieve record
  - `upsert(**fields)` - insert or update record
  - `forget_downloaded()` - clear tombstones with filters
  - `export_csv()` - export to CSV
  - `vacuum()` - database maintenance

#### 3. CLI Integration (`src/icloudpd/cli.py`)
New command-line arguments:
- `--download-db PATH` - override database location
- `--no-download-db` - disable database entirely
- `--redownload` - ignore downloaded status
- `--forget-downloaded` - clear tombstones (with filters)
- `--db-export CSV_PATH` - export database
- `--db-vacuum` - compact database
- `--db-seed PATH` - seed from existing files
- `--filter-created-after/before DATE` - date filters
- `--filter-ids FILE` - asset ID filters

#### 4. Main Flow Integration (`src/icloudpd/base.py`)
- **Database initialization** in `_process_all_users_once()`
- **Tombstone checking** in `where_builder()` - skip already downloaded assets
- **Policy skip recording** in `where_builder()` - record date-based skips
- **Download success recording** in `download_builder()` - record successful downloads
- **Legacy migration** - automatic migration from old database locations

### Key Features

#### 1. Tombstone Logic
- Assets marked as `downloaded` are skipped unless `--redownload` is used
- Database checks happen before expensive download attempts
- Supports both personal and shared libraries

#### 2. Policy Skip Recording
- Assets skipped by `--skip-created-before` are recorded as `skipped_by_policy`
- Prevents re-evaluation of policy skips on subsequent runs
- Maintains audit trail of filtering decisions

#### 3. Database Seeding
- `--db-seed PATH` populates database from existing download directories
- Matches files by name, size, and creation date
- Creates pseudo asset IDs for existing files using MD5 hash
- Supports common image/video formats

#### 4. Forget Functionality
- `--forget-downloaded` with date/ID filters
- Marks records as `forgotten` to allow re-download
- Supports CSV file input for specific asset IDs

#### 5. Utility Operations
- `--db-export` creates CSV dumps of database
- `--db-vacuum` compacts database for performance
- Built-in statistics and health monitoring

### Database Schema

```sql
CREATE TABLE IF NOT EXISTS downloads (
  asset_id            TEXT PRIMARY KEY,
  master_record_id    TEXT,
  library_kind        TEXT,
  asset_type          TEXT,
  icloud_filename     TEXT,
  bytes               INTEGER,
  created_utc         TEXT,
  original_checksum   TEXT,
  local_sha1          TEXT,
  last_local_path     TEXT,
  status              TEXT NOT NULL,
  first_seen_utc      TEXT NOT NULL,
  last_seen_utc       TEXT NOT NULL,
  last_attempt_utc    TEXT,
  last_error          TEXT
);
```

### Error Handling
- Graceful degradation when database operations fail
- Comprehensive exception handling with debug logging
- Fallback behavior when database is disabled or unavailable
- Safe handling of missing or corrupted database files

### Testing
- Comprehensive unit tests for database operations (`tests/test_database.py`)
- Integration testing for core functionality (`test_integration.py`)
- Path resolution testing for both Windows and POSIX
- CLI argument parsing validation

## Usage Examples

### Basic Usage
```bash
# Default behavior - uses auto-detected database location
icloudpd -u user@example.com -d /downloads

# Custom database location
icloudpd -u user@example.com -d /downloads --download-db /path/to/db.sqlite

# Disable database entirely
icloudpd -u user@example.com -d /downloads --no-download-db
```

### Database Management
```bash
# Export database to CSV
icloudpd -u user@example.com --db-export /path/to/export.csv

# Vacuum database
icloudpd -u user@example.com --db-vacuum

# Seed database from existing files
icloudpd -u user@example.com --db-seed /existing/downloads
```

### Tombstone Management
```bash
# Redownload all assets ignoring database
icloudpd -u user@example.com -d /downloads --redownload

# Forget downloaded assets newer than date
icloudpd -u user@example.com --forget-downloaded --filter-created-after 2024-01-01

# Forget specific asset IDs from CSV
icloudpd -u user@example.com --forget-downloaded --filter-ids asset_ids.csv
```

## Backward Compatibility

- All existing functionality remains unchanged when `--no-download-db` is used
- Default database location chosen to avoid conflicts
- Automatic migration of legacy database locations
- No breaking changes to existing command-line interface
- Zero external dependencies introduced

## Performance Considerations

- SQLite WAL mode enables concurrent reads during downloads
- Indexed asset_id lookups for fast tombstone checks
- Minimal overhead when database operations are disabled
- Efficient bulk operations for seeding and forgetting
- Database vacuum recommended for long-term maintenance

## Security & Privacy

- Local SQLite database with no external services
- No sensitive data stored beyond file metadata
- Database stored in user's Documents folder by default
- Can be synced via OneDrive/cloud storage if desired
- Standard file system permissions apply

## Future Enhancements

The implementation provides a solid foundation for future enhancements:
- Support for composite keys if asset ID collisions occur
- Advanced filtering and querying capabilities  
- Integration with backup and sync systems
- Performance monitoring and analytics
- Schema versioning for future database migrations

This implementation successfully achieves the objective of preventing re-downloads while maintaining compatibility and providing comprehensive database management capabilities.