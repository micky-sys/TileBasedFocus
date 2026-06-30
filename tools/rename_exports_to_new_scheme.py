"""Rename existing Bay14 frame exports to the new naming scheme.

Old layout (produced by older Bay14Reader builds)::

    Cam{ID}_{YYYYMMDD}_{project}_L{line}_{HHMMSS}_p{N}_S{sweep}_F{frame}.{ext}

New layout::

    Cam{ID}_{YYYYMMDD}_{project}_{HHMMSS}_L{line}_S{sweep}_F{frame}.{ext}

The ``_p{N}`` "part" suffix is dropped (it only matters at the .bay14line
container level), and the line/time tokens swap so the time comes first.

The tool walks one or more root folders recursively, identifies files
whose basename matches the old pattern, and renames them in place. Files
that already match the new pattern (or anything else) are left alone.

Usage::

    python rename_exports_to_new_scheme.py D:\\Exports
    python rename_exports_to_new_scheme.py D:\\Exports --dry-run
    python rename_exports_to_new_scheme.py D:\\A D:\\B --extensions tif jpg

Exit code is 0 if every match was renamed (or would be on dry run), 1 if
any rename was skipped because the target path already existed.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("rename_exports")

# Old export-file basename:
#   Cam2439007_20260525_ParallaxTestRishon_L13_144545_p0_S1_F1.tif
# Project name allows underscores; the trailing structure is anchored by
# ``_L{n}_{HHMMSS}(_p{N})?_S{n}_F{n}.{ext}`` which is unambiguous.
OLD_NAME_RE = re.compile(
    r"^(?P<cam>Cam\d+)_(?P<date>\d{8})_(?P<project>.+?)"
    r"_L(?P<line>\d+)_(?P<hhmmss>\d{6})(?:_p\d+)?"
    r"_S(?P<sweep>\d+)_F(?P<frame>\d+)"
    r"\.(?P<ext>[A-Za-z0-9]+)$"
)


def rewrite_basename(name: str) -> str | None:
    """Return the new basename for *name*, or ``None`` if it doesn't match.

    Already-renamed files (no ``_L{line}_{hhmmss}`` ordering) return None
    by design, so re-running the tool is a safe no-op for them.
    """
    m = OLD_NAME_RE.match(name)
    if not m:
        return None
    return (
        f"{m.group('cam')}_{m.group('date')}_{m.group('project')}"
        f"_{m.group('hhmmss')}_L{m.group('line')}"
        f"_S{m.group('sweep')}_F{m.group('frame')}.{m.group('ext')}"
    )


def iter_candidates(
    roots: list[Path],
    *,
    recursive: bool,
    extensions: set[str] | None,
) -> list[Path]:
    """Collect files under *roots* whose basenames look renameable."""
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            log.warning("root does not exist: %s", root)
            continue
        if root.is_file():
            candidates = [root]
        else:
            iterator = root.rglob("*") if recursive else root.glob("*")
            candidates = [p for p in iterator if p.is_file()]
        for p in candidates:
            if extensions is not None and p.suffix.lower().lstrip(".") not in extensions:
                continue
            if OLD_NAME_RE.match(p.name):
                out.append(p)
    return out


def rename_one(src: Path, *, dry_run: bool) -> tuple[str, Path | None]:
    """Plan/execute the rename for *src*.

    Returns ``(status, dest)`` where ``status`` is one of
    ``"renamed"`` / ``"would_rename"`` / ``"skipped_exists"`` /
    ``"unchanged"``. ``dest`` is the planned/actual new path (or None if
    nothing to do).
    """
    new_name = rewrite_basename(src.name)
    if new_name is None or new_name == src.name:
        return "unchanged", None
    dest = src.with_name(new_name)
    if dest.exists():
        return "skipped_exists", dest
    if dry_run:
        return "would_rename", dest
    src.rename(dest)
    return "renamed", dest


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "roots",
        nargs="+",
        type=Path,
        help="One or more folders (or single files) to rename under.",
    )
    p.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only look at the top level of each root, not subfolders.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without renaming anything.",
    )
    p.add_argument(
        "--extensions",
        nargs="*",
        default=None,
        help=(
            "Optional whitelist of extensions to consider, without the "
            "leading dot (e.g. --extensions tif jpg). Defaults to all."
        ),
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print each rename (otherwise only a summary is shown).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    exts = (
        {e.lower().lstrip(".") for e in args.extensions}
        if args.extensions
        else None
    )
    candidates = iter_candidates(
        [Path(r) for r in args.roots],
        recursive=not args.no_recursive,
        extensions=exts,
    )
    if not candidates:
        log.info("No files matched the old naming scheme under the given roots.")
        return 0

    counts = {"renamed": 0, "would_rename": 0, "skipped_exists": 0, "unchanged": 0}
    skipped: list[tuple[Path, Path]] = []
    for src in candidates:
        status, dest = rename_one(src, dry_run=args.dry_run)
        counts[status] = counts.get(status, 0) + 1
        if status in ("renamed", "would_rename"):
            log.debug("%s: %s -> %s", status, src, dest.name if dest else "")
        elif status == "skipped_exists" and dest is not None:
            skipped.append((src, dest))
            log.warning("SKIP (target exists): %s -> %s", src, dest.name)

    verb = "Would rename" if args.dry_run else "Renamed"
    log.info(
        "%s %d file(s); skipped %d (target already exists); %d already in new format.",
        verb,
        counts["would_rename"] + counts["renamed"],
        counts["skipped_exists"],
        counts["unchanged"],
    )
    return 1 if counts["skipped_exists"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
