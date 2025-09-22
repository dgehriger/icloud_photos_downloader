"""Tests for database-related functionality"""

import pytest
import tempfile
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from icloudpd.paths import default_download_db_path
from icloudpd.db import Database, DownloadRow, now_iso


def test_default_download_db_path_windows():
    """Test Windows path resolution using Known Folders API"""
    with patch('os.name', 'nt'):
        with patch('ctypes.windll.shell32.SHGetKnownFolderPath') as mock_api:
            mock_ptr = MagicMock()
            mock_ptr.value = r'C:\Users\TestUser\OneDrive\Documents'
            mock_api.return_value = 0  # S_OK
            
            with patch('ctypes.byref', return_value=MagicMock()):
                with patch('ctypes.wintypes.LPWSTR', return_value=mock_ptr):
                    with patch('ctypes.windll.ole32.CoTaskMemFree'):
                        result = default_download_db_path()
                        expected = Path(r'C:\Users\TestUser\OneDrive\Documents\icloudpd\downloads.db')
                        assert result == expected


def test_default_download_db_path_posix():
    """Test POSIX path resolution"""
    with patch('os.name', 'posix'):
        with patch.object(Path, 'home', return_value=Path('/home/testuser')):
            result = default_download_db_path()
            expected = Path('/home/testuser/.config/icloudpd/downloads.db')
            assert result == expected


def test_default_download_db_path_windows_fallback():
    """Test Windows fallback when API fails"""
    with patch('os.name', 'nt'):
        with patch('ctypes.windll.shell32.SHGetKnownFolderPath', side_effect=OSError):
            with patch.object(Path, 'home', return_value=Path(r'C:\Users\TestUser')):
                result = default_download_db_path()
                expected = Path(r'C:\Users\TestUser\Documents\icloudpd\downloads.db')
                assert result == expected


class TestDatabase:
    """Test Database class functionality"""
    
    def test_database_initialization(self):
        """Test database creation and schema"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            
            with Database(db_path) as db:
                # Check that database file was created
                assert db_path.exists()
                
                # Check that schema was created
                cursor = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                assert 'downloads' in tables
                
                # Check that indexes were created
                cursor = db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
                indexes = [row[0] for row in cursor.fetchall()]
                assert any('idx_downloads_master' in idx for idx in indexes)
                assert any('idx_downloads_status' in idx for idx in indexes)
    
    def test_database_upsert_insert(self):
        """Test inserting new records"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            
            with Database(db_path) as db:
                asset_id = 'test-asset-123'
                db.upsert(
                    asset_id=asset_id,
                    status='downloaded',
                    icloud_filename='test.jpg',
                    bytes=12345,
                    library_kind='personal'
                )
                
                # Check that record was inserted
                record = db.get(asset_id)
                assert record is not None
                assert record.asset_id == asset_id
                assert record.status == 'downloaded'
                assert record.icloud_filename == 'test.jpg'
                assert record.bytes == 12345
                assert record.library_kind == 'personal'
                assert record.first_seen_utc is not None
                assert record.last_seen_utc is not None
    
    def test_database_upsert_update(self):
        """Test updating existing records"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            
            with Database(db_path) as db:
                asset_id = 'test-asset-123'
                
                # Insert initial record
                db.upsert(
                    asset_id=asset_id,
                    status='downloaded',
                    icloud_filename='test.jpg',
                    library_kind='personal'
                )
                
                original_record = db.get(asset_id)
                assert original_record is not None
                first_seen = original_record.first_seen_utc
                
                # Update record
                db.upsert(
                    asset_id=asset_id,
                    status='downloaded',
                    bytes=54321,
                    last_local_path='/new/path/test.jpg'
                )
                
                # Check that record was updated
                updated_record = db.get(asset_id)
                assert updated_record is not None
                assert updated_record.asset_id == asset_id
                assert updated_record.bytes == 54321
                assert updated_record.last_local_path == '/new/path/test.jpg'
                # first_seen should not change on update
                assert updated_record.first_seen_utc == first_seen
                # last_seen should be updated
                assert updated_record.last_seen_utc >= first_seen
    
    def test_database_get_nonexistent(self):
        """Test getting non-existent records"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            
            with Database(db_path) as db:
                result = db.get('nonexistent-asset')
                assert result is None
    
    def test_count_by_status(self):
        """Test counting records by status"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            
            with Database(db_path) as db:
                # Insert test data
                db.upsert(asset_id='asset1', status='downloaded')
                db.upsert(asset_id='asset2', status='downloaded')
                db.upsert(asset_id='asset3', status='error')
                
                assert db.count_by_status('downloaded') == 2
                assert db.count_by_status('error') == 1
                assert db.count_by_status('skipped_by_policy') == 0
    
    def test_forget_downloaded(self):
        """Test forgetting downloaded records"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            
            with Database(db_path) as db:
                # Insert test data
                db.upsert(asset_id='asset1', status='downloaded')
                db.upsert(asset_id='asset2', status='downloaded')
                db.upsert(asset_id='asset3', status='error')
                
                # Forget specific assets
                count = db.forget_downloaded(asset_ids=['asset1'])
                assert count == 1
                
                # Check status was updated
                record = db.get('asset1')
                assert record is not None
                assert record.status == 'forgotten'
                
                # Check other records unchanged
                record2 = db.get('asset2')
                assert record2 is not None
                assert record2.status == 'downloaded'
                
                record3 = db.get('asset3')
                assert record3 is not None
                assert record3.status == 'error'
    
    def test_export_csv(self):
        """Test CSV export functionality"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            csv_path = Path(tmpdir) / 'export.csv'
            
            with Database(db_path) as db:
                # Insert test data
                db.upsert(
                    asset_id='asset1',
                    status='downloaded',
                    icloud_filename='test.jpg',
                    bytes=12345
                )
                
                # Export to CSV
                db.export_csv(csv_path)
                
                # Check CSV file was created and contains data
                assert csv_path.exists()
                with open(csv_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    assert 'asset1' in content
                    assert 'downloaded' in content
                    assert 'test.jpg' in content
    
    def test_database_stats(self):
        """Test database statistics"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            
            with Database(db_path) as db:
                # Insert test data
                db.upsert(asset_id='asset1', status='downloaded')
                db.upsert(asset_id='asset2', status='error')
                
                stats = db.get_stats()
                assert stats['total_records'] == 2
                assert stats['status_counts']['downloaded'] == 1
                assert stats['status_counts']['error'] == 1
                assert 'db_size_bytes' in stats
                assert stats['db_size_bytes'] > 0


def test_now_iso():
    """Test ISO timestamp generation"""
    result = now_iso()
    
    # Should be valid ISO format ending with Z
    assert result.endswith('Z')
    
    # Should be parseable as datetime
    parsed = datetime.fromisoformat(result.replace('Z', '+00:00'))
    assert parsed.tzinfo == timezone.utc
    
    # Should be recent (within last few seconds)
    now = datetime.now(timezone.utc)
    diff = (now - parsed).total_seconds()
    assert abs(diff) < 5  # Within 5 seconds


if __name__ == '__main__':
    pytest.main([__file__])