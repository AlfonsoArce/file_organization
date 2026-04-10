"""
iCloud Folder Inventory Script
Scans your iCloud directory and produces a CSV + summary report.
"""

import os
import csv
import json
import time
import hashlib
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict


# ── Configuration ─────────────────────────────────────────────────────────────

ICLOUD_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"

OUTPUT_DIR = Path(__file__).parent / "output"

# Files that are in iCloud but not yet downloaded locally have this suffix.
# We record them but mark them as "not downloaded".
ICLOUD_PLACEHOLDER_SUFFIX = ".icloud"

# macOS 12+ marks evicted iCloud files with UF_DATALESS instead of .icloud placeholders.
_UF_DATALESS = 0x40000000

# Extensions to treat as "media" for the summary breakdown
MEDIA_EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
              ".heic", ".heif", ".webp", ".raw", ".cr2", ".nef", ".arw"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv", ".flv",
              ".webm", ".mpg", ".mpeg", ".3gp", ".ts"},
    "audio": {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".aiff"},
    "document": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                 ".pages", ".numbers", ".keynote", ".txt", ".rtf", ".md"},
    "code": {".py", ".js", ".swift", ".kt", ".java", ".c", ".cpp",
             ".h", ".go", ".rs", ".rb", ".sh", ".json", ".yaml", ".yml",
             ".toml", ".xml", ".html", ".css"},
    "archive": {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".dmg", ".pkg"},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def classify(ext: str) -> str:
    ext = ext.lower()
    for category, extensions in MEDIA_EXTENSIONS.items():
        if ext in extensions:
            return category
    return "other"


def is_placeholder(path: Path) -> bool:
    """iCloud files not yet downloaded locally end with .icloud and start with '.'"""
    return path.suffix == ICLOUD_PLACEHOLDER_SUFFIX and path.name.startswith(".")


def real_name(path: Path) -> str:
    """Strip the leading dot and .icloud suffix from placeholder filenames."""
    if is_placeholder(path):
        return path.stem.lstrip(".")
    return path.name


# ── Core scan ─────────────────────────────────────────────────────────────────

def scan(root: Path, verbose: bool = True):
    rows = []
    errors = []
    total_files = 0
    skipped_dirs = 0

    start = time.time()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Skip hidden system dirs (e.g. .Trash, .DocumentRevisions-V100)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        current_dir = Path(dirpath)
        rel_dir = current_dir.relative_to(root)

        for filename in filenames:
            filepath = current_dir / filename
            total_files += 1

            if verbose and total_files % 1000 == 0:
                elapsed = time.time() - start
                print(f"  Scanned {total_files:,} files in {elapsed:.1f}s …", flush=True)

            placeholder = is_placeholder(filepath)
            display_name = real_name(filepath)
            ext = Path(display_name).suffix.lower()
            category = classify(ext)

            try:
                stat = filepath.stat()
                size_bytes = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
                ctime = datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds")
                # macOS 12+ evicts files using UF_DATALESS flag rather than .icloud placeholders
                dataless = hasattr(stat, "st_flags") and bool(stat.st_flags & _UF_DATALESS)
            except (PermissionError, OSError) as e:
                errors.append({"path": str(filepath), "error": str(e)})
                size_bytes = 0
                mtime = ""
                ctime = ""
                dataless = False

            rows.append({
                "relative_path": str(rel_dir / display_name),
                "filename": display_name,
                "extension": ext,
                "category": category,
                "size_bytes": size_bytes,
                "size_human": human_size(size_bytes),
                "modified": mtime,
                "created": ctime,
                "downloaded": not (placeholder or dataless),
                "directory": str(rel_dir),
            })

    elapsed = time.time() - start
    if verbose:
        print(f"\nScan complete: {total_files:,} files in {elapsed:.1f}s")

    return rows, errors


# ── Reports ───────────────────────────────────────────────────────────────────

def write_csv(rows: list, path: Path):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV saved  → {path}")


def write_summary(rows: list, errors: list, path: Path):
    total_size = sum(r["size_bytes"] for r in rows)
    total_files = len(rows)
    not_downloaded = sum(1 for r in rows if not r["downloaded"])

    # By category
    by_cat: dict[str, dict] = defaultdict(lambda: {"count": 0, "size": 0})
    for r in rows:
        by_cat[r["category"]]["count"] += 1
        by_cat[r["category"]]["size"] += r["size_bytes"]

    # By extension (top 30)
    by_ext: dict[str, dict] = defaultdict(lambda: {"count": 0, "size": 0})
    for r in rows:
        key = r["extension"] or "(no extension)"
        by_ext[key]["count"] += 1
        by_ext[key]["size"] += r["size_bytes"]
    top_ext = sorted(by_ext.items(), key=lambda x: x[1]["size"], reverse=True)[:30]

    # Top 20 largest files (already downloaded)
    top_files = sorted(
        (r for r in rows if r["downloaded"]),
        key=lambda r: r["size_bytes"],
        reverse=True,
    )[:20]

    # Top 20 largest directories
    by_dir: dict[str, int] = defaultdict(int)
    for r in rows:
        top_level = r["directory"].split(os.sep)[0] if r["directory"] != "." else "(root)"
        by_dir[top_level] += r["size_bytes"]
    top_dirs = sorted(by_dir.items(), key=lambda x: x[1], reverse=True)[:20]

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ICLOUD_ROOT),
        "totals": {
            "files": total_files,
            "size_bytes": total_size,
            "size_human": human_size(total_size),
            "not_downloaded_locally": not_downloaded,
        },
        "by_category": {
            cat: {"count": v["count"], "size_human": human_size(v["size"]), "size_bytes": v["size"]}
            for cat, v in sorted(by_cat.items(), key=lambda x: x[1]["size"], reverse=True)
        },
        "top_extensions": [
            {"ext": ext, "count": v["count"], "size_human": human_size(v["size"])}
            for ext, v in top_ext
        ],
        "top_20_largest_files": [
            {"path": r["relative_path"], "size": r["size_human"]}
            for r in top_files
        ],
        "top_20_largest_directories": [
            {"directory": d, "size": human_size(s), "size_bytes": s}
            for d, s in top_dirs
        ],
        "errors": errors,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary    → {path}")
    return summary


def print_summary(summary: dict):
    t = summary["totals"]
    print("\n" + "=" * 60)
    print("  iCloud Inventory Summary")
    print("=" * 60)
    print(f"  Root          : {summary['root']}")
    print(f"  Total files   : {t['files']:,}")
    print(f"  Total size    : {t['size_human']}")
    print(f"  Not downloaded: {t['not_downloaded_locally']:,}")
    print()
    print("  By category:")
    for cat, v in summary["by_category"].items():
        print(f"    {cat:<12}  {v['count']:>8,} files   {v['size_human']:>10}")
    print()
    print("  Top 10 extensions by size:")
    for item in summary["top_extensions"][:10]:
        print(f"    {item['ext'] or '(none)':<16}  {item['count']:>8,} files   {item['size_human']:>10}")
    print()
    print("  Top 10 directories by size:")
    for item in summary["top_20_largest_directories"][:10]:
        print(f"    {item['directory'][:40]:<42}  {item['size']:>10}")
    print("=" * 60)
    if summary["errors"]:
        print(f"  Warnings: {len(summary['errors'])} paths could not be read (see summary JSON)")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Inventory your iCloud folder.")
    parser.add_argument(
        "--root",
        type=Path,
        default=ICLOUD_ROOT,
        help="Path to scan (default: ~/Library/Mobile Documents/com~apple~CloudDocs)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory where output files are saved (default: ./output)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress progress output"
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = args.out / f"inventory_{timestamp}.csv"
    summary_path = args.out / f"summary_{timestamp}.json"

    print(f"\nScanning: {args.root}")
    print("This may take several minutes for large drives …\n")

    rows, errors = scan(args.root, verbose=not args.quiet)
    write_csv(rows, csv_path)
    summary = write_summary(rows, errors, summary_path)
    print_summary(summary)


if __name__ == "__main__":
    main()
