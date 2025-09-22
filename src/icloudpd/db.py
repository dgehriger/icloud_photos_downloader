"""Download ledger database for persistent download tracking"""

from __future__ import annotations
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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
CREATE INDEX IF NOT EXISTS idx_downloads_master ON downloads(master_record_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
PRAGMA user_version = 1;
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