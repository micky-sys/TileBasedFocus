"""Enumerate mounted drives for the per-camera target-drive dropdowns.

Lists fixed + removable drives that have a drive letter and a recognised
local filesystem, with free-space and volume label. Falls back to
``ctypes`` + Win32 API if ``psutil`` is unavailable so the GUI still works.

Specifically *excludes* cloud-stream-style mounts such as Google Drive
(``G:\\``) and OneDrive: they show up as fixed drives in Windows but
``disk_usage()`` on them stalls for many seconds while it pulls metadata
from the cloud, which would freeze the bring-up. We only ever want to
write 250 MB TIFFs to local U.2 drives anyway.
"""

from __future__ import annotations

import concurrent.futures
import os
import string
from dataclasses import dataclass

# Filesystems we actually want to write 250 MB TIFFs to. Everything else
# (FAT32 with its 4 GB file limit, Google Drive's "GoogleDriveFS"-ish
# pseudo-fs, network shares) is filtered out -- the operator's targets
# are always U.2 NVMe drives formatted as one of these.
RECORDABLE_FSTYPES = frozenset({"ntfs", "exfat", "refs"})

# Hard limit on time spent probing one drive's free space; if it takes
# longer than this we assume it's a stalled cloud / network mount and
# skip it. 1 s is comfortably above any local-NVMe read.
_DISK_USAGE_TIMEOUT_S = 1.0


@dataclass(frozen=True)
class DriveInfo:
    """One mounted volume."""

    root: str  # e.g. "D:\\"
    label: str  # volume label, "" if none
    filesystem: str  # "NTFS", "exFAT", ...
    total_bytes: int
    free_bytes: int
    is_removable: bool
    is_system: bool  # True if this is the OS drive (usually C:)

    @property
    def letter(self) -> str:
        return self.root[:1].upper() if self.root else ""

    def human(self) -> str:
        gb_free = self.free_bytes / 1_073_741_824
        gb_total = self.total_bytes / 1_073_741_824
        parts = [self.root]
        if self.label:
            parts.append(f"[{self.label}]")
        parts.append(f"{self.filesystem}")
        parts.append(f"{gb_free:.1f}/{gb_total:.1f} GB free")
        if self.is_removable:
            parts.append("removable")
        if self.is_system:
            parts.append("SYSTEM")
        return "  ".join(parts)


def list_drives() -> list[DriveInfo]:
    """Return one DriveInfo per mounted, letter-assigned volume."""
    try:
        return _list_drives_psutil()
    except Exception:
        return _list_drives_ctypes()


def _system_drive_root() -> str:
    sysdrive = os.environ.get("SystemDrive", "C:").rstrip("\\")
    return sysdrive + "\\"


def _list_drives_psutil() -> list[DriveInfo]:
    import psutil  # type: ignore

    system_root = _system_drive_root().upper()
    out: list[DriveInfo] = []
    for part in psutil.disk_partitions(all=False):
        root = part.mountpoint
        if not root or not (root[0].isalpha() and root[1:2] == ":"):
            continue
        fstype = (part.fstype or "").lower()
        if fstype not in RECORDABLE_FSTYPES:
            # Skips cdroms, network shares, FAT32 (4 GB file limit kills
            # 250 MB chunks), Google Drive Stream's pseudo-fs, etc.
            continue
        opts = (part.opts or "").lower()
        is_removable = "removable" in opts or "cdrom" in opts
        usage = _disk_usage_with_timeout(root)
        if usage is None:
            continue
        label = _volume_label(root)
        out.append(
            DriveInfo(
                root=root if root.endswith("\\") else root + "\\",
                label=label,
                filesystem=part.fstype or "",
                total_bytes=usage.total,
                free_bytes=usage.free,
                is_removable=is_removable,
                is_system=(root.upper().rstrip("\\") + "\\") == system_root,
            )
        )
    out.sort(key=lambda d: d.letter)
    return out


def _disk_usage_with_timeout(root: str):
    """Run ``psutil.disk_usage(root)`` in a worker; return ``None`` on
    timeout / error. We've seen Google Drive Stream block for >5 s on
    this call, which would otherwise freeze the GUI bring-up."""
    import psutil  # type: ignore

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(psutil.disk_usage, root)
        try:
            return fut.result(timeout=_DISK_USAGE_TIMEOUT_S)
        except Exception:
            return None


def _list_drives_ctypes() -> list[DriveInfo]:
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    drives_bitmask = kernel32.GetLogicalDrives()
    system_root = _system_drive_root().upper()

    out: list[DriveInfo] = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not (drives_bitmask & (1 << i)):
            continue
        root = f"{letter}:\\"

        # DRIVE_FIXED = 3, DRIVE_REMOVABLE = 2, DRIVE_CDROM = 5, DRIVE_REMOTE = 4
        drive_type = kernel32.GetDriveTypeW(root)
        if drive_type not in (2, 3):  # only fixed + removable
            continue

        fs_name_buf = ctypes.create_unicode_buffer(64)
        label_buf = ctypes.create_unicode_buffer(256)
        if not kernel32.GetVolumeInformationW(
            root,
            label_buf, 256,
            None, None, None,
            fs_name_buf, 64,
        ):
            fs = ""
            label = ""
        else:
            fs = fs_name_buf.value
            label = label_buf.value

        if fs.lower() not in RECORDABLE_FSTYPES:
            # Mirrors the psutil path's filter (skip Google Drive,
            # FAT32, network shares, etc.).
            continue

        free_bytes = ctypes.c_ulonglong(0)
        total_bytes = ctypes.c_ulonglong(0)
        if not kernel32.GetDiskFreeSpaceExW(
            root,
            ctypes.byref(free_bytes),
            ctypes.byref(total_bytes),
            None,
        ):
            continue

        out.append(
            DriveInfo(
                root=root,
                label=label,
                filesystem=fs,
                total_bytes=total_bytes.value,
                free_bytes=free_bytes.value,
                is_removable=(drive_type == 2),
                is_system=root.upper() == system_root,
            )
        )
    return out


def _volume_label(root: str) -> str:
    import ctypes

    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except Exception:
        return ""
    fs_name_buf = ctypes.create_unicode_buffer(64)
    label_buf = ctypes.create_unicode_buffer(256)
    ok = kernel32.GetVolumeInformationW(
        root,
        label_buf, 256,
        None, None, None,
        fs_name_buf, 64,
    )
    return label_buf.value if ok else ""


def free_bytes(drive_root: str) -> int:
    """Current free bytes on a mounted drive. Raises OSError if not mounted."""
    import shutil

    return shutil.disk_usage(drive_root).free
