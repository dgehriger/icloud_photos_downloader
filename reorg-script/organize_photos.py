#!/usr/bin/env python3
"""
Photo Library Organizer

Organizes photos into YYYY/MM folder structure based on existing folder names.
Supports dry-run mode and comprehensive logging for undo capability.
"""

import argparse
import json
import logging
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict


# Regex patterns to extract year and month from folder names
DATE_PATTERNS = [
    # Match YYYY/MM MonthName YYYY format (e.g., 2021/01 Janvier 2021, 2021/07 Juillet 2021 Paros_Grèce)
    re.compile(r'(?P<year>\d{4})[/\\](?P<month>\d{2})\s+[A-Za-zéèêëàâäôöûüçÉÈÊËÀÂÄÔÖÛÜÇ]+\s+\d{4}'),
    
    # Match YYYY/MM MonthName format (e.g., 2023/01 janvier, 2023/02 février)
    re.compile(r'(?P<year>\d{4})[/\\](?P<month>\d{2})\s+[A-Za-zéèêëàâäôöûüçÉÈÊËÀÂÄÔÖÛÜÇ]+'),
    
    # Match YYYY/MM-MMM format (e.g., 2023/04-Apr)
    re.compile(r'(?P<year>\d{4})[/\\](?P<month>\d{2})-[A-Za-z]{3}'),
    
    # Match YYYY/MM/DD format (e.g., 2023/04/15)
    re.compile(r'(?P<year>\d{4})[/\\](?P<month>\d{2})[/\\]\d{2}'),
    
    # Match YYYY_MM_DD in folder name (e.g., 2004_09_07 Berne, 2004_09_08)
    re.compile(r'(?P<year>\d{4})_(?P<month>\d{2})_\d{2}'),
    
    # Match YYYY-MM-DD in folder name (e.g., 2023-04-15 Trip)
    re.compile(r'(?P<year>\d{4})-(?P<month>\d{2})-\d{2}'),
    
    # Match YYYY/MM format (e.g., 2023/04)
    re.compile(r'(?P<year>\d{4})[/\\](?P<month>\d{2})(?:[/\\]|$)'),
    
    # Match standalone YYYY_MM (e.g., 2004_09)
    re.compile(r'(?P<year>\d{4})_(?P<month>\d{2})(?:[/\\]|$|\s)'),
    
    # Match standalone YYYY-MM (e.g., 2023-04)
    re.compile(r'(?P<year>\d{4})-(?P<month>\d{2})(?:[/\\]|$|\s)'),
]


class PhotoOrganizer:
    """Organizes photos into YYYY/MM structure."""
    
    def __init__(self, library_root: Path, dry_run: bool = False):
        """
        Initialize the organizer.
        
        Args:
            library_root: Root directory of the photo library
            dry_run: If True, only preview changes without moving files
        """
        self.library_root = library_root
        self.dry_run = dry_run
        self.stats = {
            'moved': 0,
            'skipped': 0,
            'conflicts': 0,
            'errors': 0,
            'no_date': 0
        }
        self.operations: List[Dict] = []
        
        # Set up logging
        log_filename = f'photo_organizer_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_filename),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Starting photo organizer (dry_run={dry_run})")
        self.logger.info(f"Library root: {library_root}")
        
    def extract_date_from_path(self, file_path: Path) -> Optional[Tuple[str, str]]:
        """
        Extract year and month from file path.
        
        Args:
            file_path: Full path to the file
            
        Returns:
            Tuple of (year, month) or None if no date found
        """
        # Get the relative path from library root
        try:
            rel_path = file_path.relative_to(self.library_root)
        except ValueError:
            rel_path = file_path
            
        path_str = str(rel_path)
        
        # Try each pattern
        for pattern in DATE_PATTERNS:
            match = pattern.search(path_str)
            if match:
                year = match.group('year')
                month = match.group('month')
                
                # Validate year and month
                if 1900 <= int(year) <= 2100 and 1 <= int(month) <= 12:
                    return (year, month)
        
        return None
    
    def is_already_organized(self, file_path: Path) -> bool:
        """
        Check if file is already in YYYY/MM structure.
        
        Args:
            file_path: Full path to the file
            
        Returns:
            True if already organized, False otherwise
        """
        try:
            rel_path = file_path.relative_to(self.library_root)
            parts = rel_path.parts
            
            # Check if path is EXACTLY YYYY/MM/filename (no subfolders within YYYY/MM)
            if len(parts) == 3:
                year_part = parts[0]
                month_part = parts[1]
                
                # Check if year is 4 digits and month is 2 digits
                if (re.match(r'^\d{4}$', year_part) and 
                    re.match(r'^\d{2}$', month_part)):
                    year = int(year_part)
                    month = int(month_part)
                    if 1900 <= year <= 2100 and 1 <= month <= 12:
                        return True
        except (ValueError, IndexError):
            pass
        
        return False
    
    def get_unique_filename(self, target_dir: Path, filename: str) -> str:
        """
        Get unique filename by adding numeric suffix if needed.
        
        Args:
            target_dir: Target directory
            filename: Original filename
            
        Returns:
            Unique filename
        """
        target_path = target_dir / filename
        if not target_path.exists():
            return filename
        
        # File exists, add numeric suffix
        stem = target_path.stem
        suffix = target_path.suffix
        counter = 2
        
        while True:
            new_filename = f"{stem}_{counter}{suffix}"
            if not (target_dir / new_filename).exists():
                self.stats['conflicts'] += 1
                self.logger.warning(f"Conflict: {filename} -> {new_filename}")
                return new_filename
            counter += 1
    
    def move_file(self, source: Path, destination: Path) -> bool:
        """
        Move file from source to destination.
        
        Args:
            source: Source file path
            destination: Destination file path
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.dry_run:
                self.logger.info(f"[DRY RUN] Would move: {source} -> {destination}")
            else:
                # Create destination directory if needed
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                self.logger.info(f"Moved: {source} -> {destination}")
            
            # Log operation for undo
            self.operations.append({
                'action': 'move',
                'source': str(source),
                'destination': str(destination),
                'timestamp': datetime.now().isoformat()
            })
            
            self.stats['moved'] += 1
            return True
            
        except Exception as e:
            self.logger.error(f"Error moving {source} to {destination}: {e}")
            self.stats['errors'] += 1
            self.operations.append({
                'action': 'error',
                'source': str(source),
                'destination': str(destination),
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            })
            return False
    
    def process_file(self, file_path: Path) -> None:
        """
        Process a single file.
        
        Args:
            file_path: Path to the file
        """
        # Skip if already organized
        if self.is_already_organized(file_path):
            self.logger.debug(f"Already organized, skipping: {file_path}")
            self.stats['skipped'] += 1
            return
        
        # Extract date from path
        date_info = self.extract_date_from_path(file_path)
        if not date_info:
            self.logger.warning(f"No date found in path, skipping: {file_path}")
            self.stats['no_date'] += 1
            self.operations.append({
                'action': 'skip',
                'reason': 'no_date',
                'source': str(file_path),
                'timestamp': datetime.now().isoformat()
            })
            return
        
        year, month = date_info
        
        # Build target path
        target_dir = self.library_root / year / month
        filename = file_path.name
        
        # Handle conflicts
        unique_filename = self.get_unique_filename(target_dir, filename)
        target_path = target_dir / unique_filename
        
        # Move file
        self.move_file(file_path, target_path)
    
    def remove_empty_directories(self) -> int:
        """
        Remove empty directories recursively.
        
        Returns:
            Number of directories removed
        """
        removed_count = 0
        
        # Get all directories, sorted by depth (deepest first)
        all_dirs = sorted(
            [d for d in self.library_root.rglob('*') if d.is_dir()],
            key=lambda p: len(p.parts),
            reverse=True
        )
        
        for dir_path in all_dirs:
            try:
                # Check if directory is empty
                if not any(dir_path.iterdir()):
                    if self.dry_run:
                        self.logger.info(f"[DRY RUN] Would remove empty directory: {dir_path}")
                    else:
                        dir_path.rmdir()
                        self.logger.info(f"Removed empty directory: {dir_path}")
                    removed_count += 1
            except (OSError, PermissionError) as e:
                self.logger.warning(f"Could not remove directory {dir_path}: {e}")
        
        return removed_count
    
    def organize(self) -> None:
        """Organize all files in the library."""
        self.logger.info("Scanning for files...")
        
        # Get all files recursively
        files = []
        for item in self.library_root.rglob('*'):
            if item.is_file():
                files.append(item)
        
        self.logger.info(f"Found {len(files)} files to process")
        
        # Process each file
        for i, file_path in enumerate(files, 1):
            if i % 100 == 0:
                self.logger.info(f"Progress: {i}/{len(files)} files processed")
            self.process_file(file_path)
        
        # Remove empty directories
        self.logger.info("\nCleaning up empty directories...")
        removed_dirs = self.remove_empty_directories()
        self.logger.info(f"Removed {removed_dirs} empty directories")
        
        # Print summary
        self.print_summary()
        
        # Save operations log
        self.save_operations_log()
    
    def print_summary(self) -> None:
        """Print summary statistics."""
        self.logger.info("\n" + "=" * 60)
        self.logger.info("SUMMARY")
        self.logger.info("=" * 60)
        self.logger.info(f"Files moved: {self.stats['moved']}")
        self.logger.info(f"Files skipped (already organized): {self.stats['skipped']}")
        self.logger.info(f"Files skipped (no date): {self.stats['no_date']}")
        self.logger.info(f"Filename conflicts resolved: {self.stats['conflicts']}")
        self.logger.info(f"Errors: {self.stats['errors']}")
        self.logger.info("=" * 60)
    
    def save_operations_log(self) -> None:
        """Save operations log to JSON file."""
        log_filename = f'photo_operations_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        with open(log_filename, 'w') as f:
            json.dump({
                'library_root': str(self.library_root),
                'dry_run': self.dry_run,
                'timestamp': datetime.now().isoformat(),
                'stats': self.stats,
                'operations': self.operations
            }, f, indent=2)
        self.logger.info(f"Operations log saved to: {log_filename}")


def generate_undo_script(operations_log_path: Path) -> None:
    """
    Generate PowerShell undo script from operations log.
    
    Args:
        operations_log_path: Path to the operations log JSON file
    """
    with open(operations_log_path, 'r') as f:
        data = json.load(f)
    
    undo_script = f'undo_photo_organization_{datetime.now().strftime("%Y%m%d_%H%M%S")}.ps1'
    
    with open(undo_script, 'w') as f:
        f.write("# Undo script for photo organization\n")
        f.write(f"# Generated: {datetime.now().isoformat()}\n")
        f.write(f"# Original library: {data['library_root']}\n\n")
        f.write("$ErrorActionPreference = 'Stop'\n\n")
        
        # Reverse the operations
        move_operations = [op for op in data['operations'] if op['action'] == 'move']
        for op in reversed(move_operations):
            source = op['destination']
            dest = op['source']
            f.write(f"# Undo: {source} -> {dest}\n")
            f.write(f"Move-Item -Path '{source}' -Destination '{dest}' -Force\n\n")
        
        f.write(f"\nWrite-Host 'Undo complete. Moved {len(move_operations)} files back.'\n")
    
    print(f"Undo script generated: {undo_script}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Organize photo library into YYYY/MM structure'
    )
    parser.add_argument(
        'library_path',
        type=Path,
        help='Path to the photo library root directory'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without moving files'
    )
    parser.add_argument(
        '--generate-undo',
        type=Path,
        metavar='LOG_FILE',
        help='Generate undo script from operations log file'
    )
    
    args = parser.parse_args()
    
    if args.generate_undo:
        generate_undo_script(args.generate_undo)
        return
    
    # Validate library path
    if not args.library_path.exists():
        print(f"Error: Library path does not exist: {args.library_path}")
        return
    
    if not args.library_path.is_dir():
        print(f"Error: Library path is not a directory: {args.library_path}")
        return
    
    # Run organizer
    organizer = PhotoOrganizer(args.library_path, dry_run=args.dry_run)
    organizer.organize()
    
    if args.dry_run:
        print("\nThis was a dry run. No files were moved.")
        print("Run without --dry-run to actually move files.")


if __name__ == '__main__':
    main()
