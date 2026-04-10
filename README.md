# iCloud Inventory

A local web app to scan, visualize, and explore your iCloud Drive — see what's taking up space, which files are cloud-only vs. downloaded, and navigate directly to any file in Finder.

## Features

- **Full iCloud scan** — walks the entire iCloud Drive tree, records every file with size, type, timestamps, and download status
- **Dashboard** — summary cards, category breakdown, top extensions by size (bar chart), top folders by size (bar chart), top 20 largest files, and a D3 treemap (Storage Map)
- **File Explorer** — paginated, sortable, filterable table with search, category, extension, directory, and download-status filters
- **Click-through navigation** — click any chart bar, folder, extension, or file row to jump to a pre-filtered File Explorer view
- **Reveal in Finder** — click any file in the explorer to open its parent folder in macOS Finder (works for both local and cloud-only placeholder files)
- **Cloud-only detection** — correctly identifies files evicted from local storage using both the legacy `.icloud` placeholder suffix and the modern macOS `UF_DATALESS` filesystem flag
- **Multiple reports** — each scan creates a timestamped CSV + JSON summary; switch between reports via the UI dropdown
- **Live scan UI** — trigger a new scan from the browser, watch live log output, and auto-refresh when complete

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, Flask |
| Package management | [uv](https://github.com/astral-sh/uv) |
| Templating | Jinja2 |
| Frontend reactivity | Alpine.js |
| Charts | Chart.js |
| Treemap | D3.js v7 |
| Styling | Custom CSS (glass-morphism dark theme) |

## Project Structure

```
file_organization/
├── app.py            # Flask web server & API routes
├── inventory.py      # iCloud scanner (produces CSV + JSON)
├── templates/
│   ├── index.html    # Dashboard (summary, charts, treemap)
│   └── files.html    # File Explorer
├── output/           # Generated reports (gitignored)
│   ├── inventory_YYYYMMDD_HHMMSS.csv
│   └── summary_YYYYMMDD_HHMMSS.json
├── pyproject.toml    # uv project config & dependencies
└── uv.lock           # Locked dependency versions
```

## Installation

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone <repo-url>
cd file_organization
uv sync
```

## Usage

### Run the web UI

```bash
uv run python app.py
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

### Run a scan from the terminal

```bash
uv run python inventory.py
```

Options:

| Flag | Default | Description |
|---|---|---|
| `--root PATH` | `~/Library/Mobile Documents/com~apple~CloudDocs` | Directory to scan |
| `--out PATH` | `./output` | Where to save CSV and JSON |
| `--quiet` | off | Suppress progress output |

Output files are saved to `./output/` as `inventory_YYYYMMDD_HHMMSS.csv` and `summary_YYYYMMDD_HHMMSS.json`.

### Trigger a scan from the browser

Click **Run New Scan** on the dashboard. Progress is streamed live and the page auto-refreshes when the scan completes.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard |
| `GET` | `/files` | File Explorer |
| `GET` | `/api/reports` | List all timestamped reports |
| `GET` | `/api/summary?report=<ts>` | Load summary JSON for a report |
| `GET` | `/api/files?report=<ts>&...` | Paginated, filtered, sorted file rows |
| `GET` | `/api/extensions?report=<ts>` | List unique extensions in a report |
| `GET` | `/api/treemap?report=<ts>` | Hierarchical size data for D3 treemap |
| `POST` | `/api/scan/start` | Start a new inventory scan |
| `GET` | `/api/scan/status` | Poll scan progress (status, log lines, file count) |
| `POST` | `/api/scan/cancel` | Cancel a running scan |
| `POST` | `/api/reveal` | Reveal a file in macOS Finder |
| `GET` | `/output/<filename>` | Download a report file (CSV or JSON) |

### `/api/files` query parameters

| Parameter | Example | Description |
|---|---|---|
| `report` | `20240410_153022` | Timestamp of the report to query |
| `search` | `vacation` | Case-insensitive substring search on path |
| `category` | `video` | Filter by file category |
| `ext` | `.mp4` | Filter by extension |
| `downloaded` | `true` / `false` | Filter by local download status |
| `dir` | `Photos` | Filter by top-level folder name |
| `sort` | `size_bytes` | Sort column |
| `order` | `desc` | Sort direction (`asc` / `desc`) |
| `page` | `1` | Page number (1-based) |
| `per_page` | `50` | Rows per page (max 500) |

## File Categories

Files are classified into categories based on extension:

| Category | Extensions (examples) |
|---|---|
| `image` | `.jpg`, `.png`, `.heic`, `.raw`, `.cr2` |
| `video` | `.mp4`, `.mov`, `.mkv`, `.ts` |
| `audio` | `.mp3`, `.m4a`, `.flac`, `.wav` |
| `document` | `.pdf`, `.docx`, `.pages`, `.md` |
| `code` | `.py`, `.js`, `.swift`, `.go`, `.rs` |
| `archive` | `.zip`, `.tar`, `.gz`, `.dmg` |
| `other` | everything else |

## Cloud-Only File Detection

macOS marks iCloud files that are not stored locally in two ways depending on OS version:

- **Legacy (macOS < 12):** Placeholder files with `.icloud` suffix and a leading dot (e.g., `.MyFile.pdf.icloud`)
- **Modern (macOS 12+):** Files with the `UF_DATALESS` filesystem flag (`0x40000000`) set in `stat.st_flags`

Both methods are detected. Files with either marker are recorded as `downloaded = False` in the CSV. Finder reveal works for both — the placeholder or dataless file exists on disk and `open -R` will navigate to it.

## Output Files

### CSV (`inventory_<timestamp>.csv`)

One row per file with columns: `relative_path`, `filename`, `extension`, `category`, `size_bytes`, `size_human`, `modified`, `created`, `downloaded`, `directory`.

### JSON (`summary_<timestamp>.json`)

High-level summary including totals, breakdown by category, top 30 extensions by size, top 20 largest files, top 20 largest directories, and any scan errors.
