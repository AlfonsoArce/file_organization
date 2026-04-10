"""
Flask UI for iCloud Inventory
Run:  uv run python app.py
Then open http://127.0.0.1:5000
"""

import csv
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

OUTPUT_DIR  = Path(__file__).parent / "output"
ICLOUD_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"

app = Flask(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _list_reports() -> list[dict]:
    """Return paired (summary JSON + CSV) reports sorted newest-first."""
    if not OUTPUT_DIR.exists():
        return []
    summaries = sorted(OUTPUT_DIR.glob("summary_*.json"), reverse=True)
    reports = []
    for s in summaries:
        ts = s.stem.replace("summary_", "")
        csv_path = OUTPUT_DIR / f"inventory_{ts}.csv"
        reports.append({
            "timestamp": ts,
            "label": _fmt_ts(ts),
            "summary_file": s.name,
            "csv_file": csv_path.name if csv_path.exists() else None,
            "has_csv": csv_path.exists(),
        })
    return reports


def _fmt_ts(ts: str) -> str:
    """'20240410_153022'  →  'Apr 10, 2024  15:30'"""
    try:
        from datetime import datetime
        dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
        return dt.strftime("%b %d, %Y  %H:%M")
    except Exception:
        return ts


def _load_summary(timestamp: str) -> dict | None:
    path = OUTPUT_DIR / f"summary_{timestamp}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# CSV rows are cached in memory per timestamp (cleared on process restart).
_csv_cache: dict[str, list[dict]] = {}


def _load_csv(timestamp: str) -> list[dict]:
    if timestamp in _csv_cache:
        return _csv_cache[timestamp]
    path = OUTPUT_DIR / f"inventory_{timestamp}.csv"
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["size_bytes"] = int(row.get("size_bytes") or 0)
            row["downloaded"] = row.get("downloaded", "True") == "True"
            rows.append(row)
    _csv_cache[timestamp] = rows
    return rows


def _latest_timestamp() -> str | None:
    reports = _list_reports()
    return reports[0]["timestamp"] if reports else None


# ── Scan state ────────────────────────────────────────────────────────────────

_PROGRESS_RE = re.compile(r"Scanned ([\d,]+) files")
_COMPLETE_RE = re.compile(r"Scan complete: ([\d,]+) files")
_MAX_LOG_LINES = 300

_scan: dict = {
    "status": "idle",       # idle | running | done | error
    "pid": None,
    "lines": [],
    "files_scanned": 0,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "new_report_ts": None,
}
_scan_lock = threading.Lock()
_scan_proc: subprocess.Popen | None = None


def _reader_thread(proc: subprocess.Popen) -> None:
    """Read subprocess stdout line by line and update shared _scan state."""
    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.rstrip()
        with _scan_lock:
            _scan["lines"].append(line)
            if len(_scan["lines"]) > _MAX_LOG_LINES:
                _scan["lines"] = _scan["lines"][-_MAX_LOG_LINES:]
            m = _PROGRESS_RE.search(line) or _COMPLETE_RE.search(line)
            if m:
                _scan["files_scanned"] = int(m.group(1).replace(",", ""))

    exit_code = proc.wait()
    with _scan_lock:
        _scan["exit_code"] = exit_code
        _scan["finished_at"] = time.time()
        _scan["pid"] = None
        if exit_code == 0:
            _scan["status"] = "done"
            reports = _list_reports()
            if reports:
                _scan["new_report_ts"] = reports[0]["timestamp"]
        else:
            _scan["status"] = "error"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    reports = _list_reports()
    ts = request.args.get("report") or (reports[0]["timestamp"] if reports else None)
    summary = _load_summary(ts) if ts else None
    with _scan_lock:
        scan_running = _scan["status"] == "running"
    return render_template(
        "index.html",
        reports=reports,
        current_ts=ts,
        summary=summary,
        scan_running=scan_running,
    )


@app.route("/files")
def files_page():
    reports = _list_reports()
    ts = request.args.get("report") or (reports[0]["timestamp"] if reports else None)
    return render_template("files.html", reports=reports, current_ts=ts)


# ── JSON API ──────────────────────────────────────────────────────────────────

@app.route("/api/reports")
def api_reports():
    return jsonify(_list_reports())


@app.route("/api/summary")
def api_summary():
    ts = request.args.get("report") or _latest_timestamp()
    if not ts:
        return jsonify({"error": "No reports found"}), 404
    data = _load_summary(ts)
    if data is None:
        return jsonify({"error": "Report not found"}), 404
    return jsonify(data)


@app.route("/api/files")
def api_files():
    ts = request.args.get("report") or _latest_timestamp()
    if not ts:
        return jsonify({"error": "No reports found", "rows": [], "total": 0}), 404

    rows = _load_csv(ts)
    if not rows:
        return jsonify({"rows": [], "total": 0, "page": 1, "per_page": 50, "pages": 0})

    search = request.args.get("search", "").lower().strip()
    category = request.args.get("category", "").lower().strip()
    ext_filter = request.args.get("ext", "").lower().strip()
    downloaded = request.args.get("downloaded", "")
    dir_filter = request.args.get("dir", "").strip()

    filtered = rows
    if search:
        filtered = [r for r in filtered if search in r["relative_path"].lower()]
    if category:
        filtered = [r for r in filtered if r["category"] == category]
    if ext_filter:
        filtered = [r for r in filtered if r["extension"] == ext_filter]
    if downloaded == "true":
        filtered = [r for r in filtered if r["downloaded"]]
    elif downloaded == "false":
        filtered = [r for r in filtered if not r["downloaded"]]
    if dir_filter:
        # Match the top-level directory segment (same grouping as the summary chart)
        def _top(r):
            d = r["directory"]
            return d.split(os.sep)[0] if d and d != "." else "(root)"
        filtered = [r for r in filtered if _top(r) == dir_filter]

    sort_col = request.args.get("sort", "size_bytes")
    sort_dir = request.args.get("order", "desc")
    reverse = sort_dir == "desc"

    valid_cols = {"relative_path", "filename", "extension", "category",
                  "size_bytes", "modified", "created", "directory"}
    if sort_col not in valid_cols:
        sort_col = "size_bytes"

    if sort_col == "size_bytes":
        filtered.sort(key=lambda r: r["size_bytes"], reverse=reverse)
    else:
        filtered.sort(key=lambda r: (r.get(sort_col) or "").lower(), reverse=reverse)

    per_page = min(int(request.args.get("per_page", 50)), 500)
    page = max(int(request.args.get("page", 1)), 1)
    total = len(filtered)
    pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    page_rows = filtered[start: start + per_page]

    return jsonify({"rows": page_rows, "total": total, "page": page,
                    "per_page": per_page, "pages": pages})


@app.route("/api/extensions")
def api_extensions():
    ts = request.args.get("report") or _latest_timestamp()
    if not ts:
        return jsonify([])
    rows = _load_csv(ts)
    exts = sorted({r["extension"] for r in rows if r["extension"]})
    return jsonify(exts)


@app.route("/api/treemap")
def api_treemap():
    ts = request.args.get("report") or _latest_timestamp()
    if not ts:
        return jsonify({"error": "No reports found"}), 404
    rows = _load_csv(ts)
    if not rows:
        return jsonify({"name": "iCloud", "children": []})

    from collections import defaultdict

    tree: dict = defaultdict(lambda: defaultdict(int))
    for row in rows:
        d = row["directory"]
        if not d or d == ".":
            top, second = "(root)", "(files)"
        else:
            parts = d.replace("\\", "/").split("/")
            top = parts[0]
            second = parts[1] if len(parts) > 1 else "(files)"
        tree[top][second] += row["size_bytes"]

    sorted_tops = sorted(tree.items(), key=lambda x: sum(x[1].values()), reverse=True)[:30]
    children = []
    for top_dir, subs in sorted_tops:
        sub_children = [
            {"name": sub or "(files)", "value": size}
            for sub, size in sorted(subs.items(), key=lambda x: x[1], reverse=True)[:25]
            if size > 0
        ]
        if sub_children:
            children.append({"name": top_dir, "children": sub_children})

    return jsonify({"name": "iCloud", "children": children})


# ── Finder integration ───────────────────────────────────────────────────────

@app.route("/api/reveal", methods=["POST"])
def reveal_in_finder():
    """Reveal a file in macOS Finder using 'open -R <path>'."""
    data = request.get_json(silent=True) or {}
    relative_path = data.get("path", "").strip()
    if not relative_path:
        return jsonify({"error": "No path provided"}), 400

    # Resolve and validate the path stays inside iCloud root
    full_path = (ICLOUD_ROOT / relative_path).resolve()
    try:
        full_path.relative_to(ICLOUD_ROOT.resolve())
    except ValueError:
        return jsonify({"error": "Path outside iCloud root"}), 403

    try:
        subprocess.Popen(["open", "-R", str(full_path)])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Scan API ──────────────────────────────────────────────────────────────────

@app.route("/api/scan/start", methods=["POST"])
def scan_start():
    global _scan_proc
    with _scan_lock:
        if _scan["status"] == "running":
            return jsonify({"error": "Scan already running"}), 409
        _scan.update({
            "status": "running",
            "lines": ["  Starting inventory scan…", ""],
            "files_scanned": 0,
            "started_at": time.time(),
            "finished_at": None,
            "exit_code": None,
            "new_report_ts": None,
        })

    inventory_script = Path(__file__).parent / "inventory.py"
    proc = subprocess.Popen(
        [sys.executable, str(inventory_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _scan_proc = proc
    with _scan_lock:
        _scan["pid"] = proc.pid

    t = threading.Thread(target=_reader_thread, args=(proc,), daemon=True)
    t.start()
    return jsonify({"status": "started", "pid": proc.pid})


@app.route("/api/scan/status")
def scan_status():
    with _scan_lock:
        elapsed = None
        if _scan["started_at"]:
            end = _scan["finished_at"] or time.time()
            elapsed = round(end - _scan["started_at"], 1)
        return jsonify({
            "status": _scan["status"],
            "files_scanned": _scan["files_scanned"],
            "elapsed": elapsed,
            "lines": list(_scan["lines"][-60:]),
            "new_report_ts": _scan["new_report_ts"],
        })


@app.route("/api/scan/cancel", methods=["POST"])
def scan_cancel():
    global _scan_proc
    with _scan_lock:
        pid = _scan.get("pid")
        if not pid:
            return jsonify({"error": "No scan running"}), 400
    try:
        if _scan_proc is not None:
            _scan_proc.terminate()
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    with _scan_lock:
        _scan["status"] = "error"
        _scan["lines"].append("")
        _scan["lines"].append("  ✕  Scan cancelled by user.")
        _scan["finished_at"] = time.time()
        _scan["pid"] = None
    return jsonify({"status": "cancelled"})


@app.route("/output/<path:filename>")
def serve_output(filename: str):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    print("\n  iCloud Inventory UI")
    print("  Open → http://127.0.0.1:5000\n")
    app.run(debug=True, port=5000, threaded=True)
