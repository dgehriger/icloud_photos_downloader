"""Path functions"""

from __future__ import annotations
import ctypes
from ctypes import wintypes
from pathlib import Path
import os


def default_download_db_path() -> Path:
    """Get default path for download database.
    
    Windows: %USERPROFILE%\\Documents\\icloudpd\\downloads.db (resolves via Known Folders)
    POSIX: ~/.config/icloudpd/downloads.db
    """
    if os.name != 'nt':
        return Path.home() / '.config' / 'icloudpd' / 'downloads.db'

    # Use Windows Known Folders API to get Documents folder
    folderid_documents = ctypes.c_wchar_p('{FDD39AD0-238F-46AF-ADB4-6C85480369C7}')
    try:
        sh_get_known_folder_path = ctypes.windll.shell32.SHGetKnownFolderPath
        sh_get_known_folder_path.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
        sh_get_known_folder_path.restype = wintypes.HRESULT
        p = wintypes.LPWSTR()
        hr = sh_get_known_folder_path(folderid_documents, 0, None, ctypes.byref(p))
        try:
            doc = Path(p.value) if hr == 0 and p.value else (Path.home() / 'Documents')
        finally:
            if p:
                ctypes.windll.ole32.CoTaskMemFree(p)
    except (OSError, AttributeError):
        # Fall back to home/Documents if Windows API fails
        doc = Path.home() / 'Documents'
    
    return doc / 'icloudpd' / 'downloads.db'


def remove_unicode_chars(value: str) -> str:
    """Removes unicode chars from the string"""
    return value.encode("utf-8").decode("ascii", "ignore")


def clean_filename(filename: str) -> str:
    """Replaces invalid chars in filenames with '_'"""
    invalid = '<>:"/\\|?*\0'
    result = filename

    for char in invalid:
        result = result.replace(char, "_")

    return result


def local_download_path(filename: str, download_dir: str) -> str:
    """Returns the full download path, including size"""
    return os.path.join(download_dir, filename)
