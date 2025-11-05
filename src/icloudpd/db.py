"""Download ledger database for persistent download tracking"""

from __future__ import annotations
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List


SCHEMA_SQL = """
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
CREATE TABLE IF NOT EXISTS download_ranges (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  query_fingerprint   TEXT NOT NULL,           -- hash of query parameters
  range_start_utc     TEXT NOT NULL,           -- ISO8601 UTC start of completed range
  range_end_utc       TEXT NOT NULL,           -- ISO8601 UTC end of completed range
  completed_utc       TEXT NOT NULL,           -- when this range was completed
  asset_count         INTEGER NOT NULL,        -- number of assets in this range
  library_kind        TEXT                     -- personal|shared
);
CREATE INDEX IF NOT EXISTS idx_downloads_master ON downloads(master_record_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_ranges_fingerprint ON download_ranges(query_fingerprint);
CREATE INDEX IF NOT EXISTS idx_ranges_dates ON download_ranges(range_start_utc, range_end_utc);
PRAGMA user_version = 2;
PRAGMA journal_mode = WAL;
"""


@dataclass
class DownloadRow:
    """Represents a row in the downloads table"""
    asset_id: str
    master_record_id: Optional[str]
    library_kind: Optional[str]
    asset_type: Optional[str]
    icloud_filename: Optional[str]
    bytes: Optional[int]
    created_utc: Optional[str]
    original_checksum: Optional[str]
    local_sha1: Optional[str]
    last_local_path: Optional[str]
    status: str
    first_seen_utc: str
    last_seen_utc: str
    last_attempt_utc: Optional[str]
    last_error: Optional[str]


@dataclass
class DownloadRangeRow:
    """Represents a row in the download_ranges table"""
    id: int
    query_fingerprint: str
    range_start_utc: str
    range_end_utc: str
    completed_utc: str
    asset_count: int
    library_kind: Optional[str]


def now_iso() -> str:
    """Return current UTC time in ISO format"""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class Database:
    """SQLite database for tracking downloaded assets"""
    
    def __init__(self, path: Path):
        """Initialize database connection with WAL mode"""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        
        self.conn = sqlite3.connect(
            str(path), 
            timeout=30,
            isolation_level=None  # autocommit mode
        )
        self.conn.row_factory = sqlite3.Row  # Enable dict-like access
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._ensure_schema()

    def _ensure_schema(self):
        """Create tables and indexes if they don't exist"""
        self.conn.executescript(SCHEMA_SQL)

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get(self, asset_id: str) -> Optional[DownloadRow]:
        """Get download record by asset ID"""
        cur = self.conn.execute(
            """SELECT asset_id, master_record_id, library_kind, asset_type, 
               icloud_filename, bytes, created_utc, original_checksum, 
               local_sha1, last_local_path, status, first_seen_utc, 
               last_seen_utc, last_attempt_utc, last_error 
               FROM downloads WHERE asset_id=?""",
            (asset_id,)
        )
        row = cur.fetchone()
        if row:
            return DownloadRow(*row)
        return None

    def upsert(self, **fields) -> None:
        """Insert or update download record"""
        # Ensure required fields
        if 'asset_id' not in fields:
            raise ValueError("asset_id is required")
        if 'status' not in fields:
            raise ValueError("status is required")
        
        # Add timestamps if not provided
        if 'first_seen_utc' not in fields and 'last_seen_utc' not in fields:
            now = now_iso()
            fields.setdefault('first_seen_utc', now)
            fields.setdefault('last_seen_utc', now)
        elif 'last_seen_utc' not in fields:
            fields['last_seen_utc'] = now_iso()

        # Prepare SQL statement
        cols = list(fields.keys())
        placeholders = ','.join('?' for _ in cols)
        update_cols = [f"{k}=excluded.{k}" for k in cols if k != 'asset_id']
        
        sql = f"""INSERT INTO downloads ({','.join(cols)}) 
                  VALUES ({placeholders}) 
                  ON CONFLICT(asset_id) DO UPDATE SET {','.join(update_cols)}"""
        
        self.conn.execute(sql, tuple(fields.values()))

    def count_by_status(self, status: str) -> int:
        """Count records by status"""
        cur = self.conn.execute("SELECT COUNT(*) FROM downloads WHERE status=?", (status,))
        return cur.fetchone()[0]

    def list_by_status(self, status: str, limit: Optional[int] = None) -> List[DownloadRow]:
        """List records by status"""
        sql = "SELECT * FROM downloads WHERE status=? ORDER BY last_seen_utc DESC"
        if limit:
            sql += f" LIMIT {limit}"
        
        cur = self.conn.execute(sql, (status,))
        return [DownloadRow(*row) for row in cur.fetchall()]

    def forget_downloaded(
        self, 
        asset_ids: Optional[List[str]] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None
    ) -> int:
        """Mark downloaded records as forgotten. Returns count of updated records."""
        conditions = ["status='downloaded'"]
        params = []
        
        if asset_ids:
            placeholders = ','.join('?' for _ in asset_ids)
            conditions.append(f"asset_id IN ({placeholders})")
            params.extend(asset_ids)
            
        if created_after:
            conditions.append("created_utc > ?")
            params.append(created_after)
            
        if created_before:
            conditions.append("created_utc < ?")
            params.append(created_before)
        
        sql = f"UPDATE downloads SET status='forgotten', last_seen_utc=? WHERE {' AND '.join(conditions)}"
        params.insert(0, now_iso())
        
        cur = self.conn.execute(sql, params)
        return cur.rowcount

    def export_csv(self, path: Path) -> None:
        """Export all records to CSV"""
        cur = self.conn.execute("SELECT * FROM downloads ORDER BY first_seen_utc")
        
        with open(path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            
            # Write header
            writer.writerow([
                'asset_id', 'master_record_id', 'library_kind', 'asset_type',
                'icloud_filename', 'bytes', 'created_utc', 'original_checksum',
                'local_sha1', 'last_local_path', 'status', 'first_seen_utc',
                'last_seen_utc', 'last_attempt_utc', 'last_error'
            ])
            
            # Write data
            for row in cur:
                writer.writerow(row)

    def vacuum(self) -> None:
        """Vacuum database to reclaim space"""
        self.conn.execute("VACUUM")

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        stats = {}
        
        # Count by status
        status_counts = {}
        cur = self.conn.execute("SELECT status, COUNT(*) FROM downloads GROUP BY status")
        for status, count in cur.fetchall():
            status_counts[status] = count
        stats['status_counts'] = status_counts
        
        # Total records
        cur = self.conn.execute("SELECT COUNT(*) FROM downloads")
        stats['total_records'] = cur.fetchone()[0]
        
        # Database file size
        stats['db_size_bytes'] = self.path.stat().st_size if self.path.exists() else 0
        
        return stats

    def record_download_range(
        self,
        query_fingerprint: str,
        range_start_utc: datetime,
        range_end_utc: datetime,
        asset_count: int,
        library_kind: Optional[str] = None
    ) -> None:
        """Record a completed download range"""
        self.conn.execute("""
            INSERT INTO download_ranges (
                query_fingerprint, range_start_utc, range_end_utc, 
                completed_utc, asset_count, library_kind
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            query_fingerprint,
            range_start_utc.isoformat(),
            range_end_utc.isoformat(),
            datetime.now(timezone.utc).isoformat(),
            asset_count,
            library_kind
        ))

    def get_download_ranges(self, query_fingerprint: str) -> List[DownloadRangeRow]:
        """Get all download ranges for a specific query fingerprint"""
        cur = self.conn.execute("""
            SELECT id, query_fingerprint, range_start_utc, range_end_utc, 
                   completed_utc, asset_count, library_kind
            FROM download_ranges 
            WHERE query_fingerprint = ?
            ORDER BY range_start_utc ASC
        """, (query_fingerprint,))
        
        ranges = []
        for row in cur.fetchall():
            # Create DownloadRangeRow with only the fields we selected
            ranges.append(DownloadRangeRow(
                id=row[0],
                query_fingerprint=row[1], 
                range_start_utc=row[2],
                range_end_utc=row[3],
                completed_utc=row[4],
                asset_count=row[5],
                library_kind=row[6]
            ))
        return ranges

    def find_optimal_cutoff_date(
        self, 
        query_fingerprint: str, 
        original_cutoff: Optional[datetime] = None
    ) -> Optional[datetime]:
        """Find the optimal skip-created-before date based on completed ranges
        
        The goal is to find the most recent date up to which we've successfully
        downloaded all photos, so we can skip re-checking those photos.
        """
        ranges = self.get_download_ranges(query_fingerprint)
        if not ranges:
            return original_cutoff
            
        # Sort ranges by start date (oldest first)
        ranges.sort(key=lambda r: r.range_start_utc)
        
        # Find the most recent contiguous coverage starting from the original cutoff
        # We're looking for ranges that cover [original_cutoff, most_recent_end]
        most_recent_end = None
        
        for range_row in ranges:
            range_start = datetime.fromisoformat(range_row.range_start_utc.replace('Z', '+00:00'))
            range_end = datetime.fromisoformat(range_row.range_end_utc.replace('Z', '+00:00'))
            
            # Skip ranges that start after the original cutoff (they're for different queries)
            if original_cutoff and range_start > original_cutoff + timedelta(days=1):
                continue
            
            # This range covers from range_start to range_end
            # If we haven't found any coverage yet, or this range extends our coverage
            if most_recent_end is None:
                most_recent_end = range_end
            else:
                # Check if this range connects to our existing coverage
                # Allow for 1-day gap for safety (timezone considerations)
                gap_days = (range_start - most_recent_end).days
                if gap_days <= 1:  # Ranges connect or overlap
                    # Extend our coverage to the end of this range
                    if range_end > most_recent_end:
                        most_recent_end = range_end
                # If there's a gap, we can't use this range to extend coverage
        
        if most_recent_end and original_cutoff:
            # Subtract 1-day safety margin to avoid missing photos at boundaries
            safety_cutoff = most_recent_end - timedelta(days=1)
            # Use the later of the safety cutoff or original cutoff
            return max(safety_cutoff, original_cutoff)
        else:
            return original_cutoff

    def migrate_legacy_db(self, legacy_path: Path) -> bool:
        """Migrate from legacy database location if it exists"""
        if not legacy_path.exists() or self.path.exists():
            return False
            
        try:
            # Create parent directory for new location
            self.path.parent.mkdir(parents=True, exist_ok=True)
            
            # Move the database file
            legacy_path.replace(self.path)
            
            # Ensure we're using the new connection
            self.conn.close()
            self.conn = sqlite3.connect(
                str(self.path), 
                timeout=30,
                isolation_level=None
            )
            self.conn.row_factory = sqlite3.Row
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA foreign_keys=ON')
            
            return True
        except OSError:
            return False