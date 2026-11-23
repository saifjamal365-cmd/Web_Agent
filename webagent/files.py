"""Files the agent may use: the person's inbox (files they gave it) and each task's downloads.

The agent can only see these two folders (no other path on the computer), never runs a file, and refuses to
download program files.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path

from webagent.config import DATA

FILES = Path(os.environ.get("WA_FILES_DIR") or DATA / "files")
INBOX = FILES / "inbox"           # files the person uploads in the dashboard / API (for upload tasks)
DOWNLOADS = FILES / "downloads"   # one folder per task
SESSIONS = Path(os.environ.get("WA_SESSIONS_DIR") or DATA / "sessions")  # saved logins, made by the person

# Program and script files are never downloaded (the agent never runs files, but a person might).
BLOCKED_EXT = {".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".hta",
               ".jar", ".dll", ".sys", ".lnk", ".reg", ".apk", ".app", ".dmg", ".pkg", ".deb", ".rpm", ".sh"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".yaml", ".yml", ".ini"}
PART = 6000


class FileError(Exception):
    """Shown to the model as the tool result."""


def safe_name(name: str) -> str:
    name = Path(str(name or "file")).name  # no folders, ever
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .") or "file"
    return name[:120]


def downloads_dir(task_id: str) -> Path:
    d = DOWNLOADS / safe_name(task_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def unique_path(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / safe_name(name)
    stem, suffix, n = p.stem, p.suffix, 1
    while p.exists():
        n += 1
        p = folder / f"{stem} ({n}){suffix}"
    return p


def blocked(name: str) -> bool:
    return Path(name).suffix.lower() in BLOCKED_EXT


def list_files(task_id: str) -> list[dict]:
    INBOX.mkdir(parents=True, exist_ok=True)
    out = []
    for where, folder in (("inbox", INBOX), ("downloads", downloads_dir(task_id))):
        for p in sorted(folder.iterdir()):
            if p.is_file():
                out.append({"name": p.name, "where": where, "size": p.stat().st_size})
    return out


def resolve(name: str, task_id: str) -> Path:
    """Find a file by name in this task's downloads, then the inbox. Never a path outside them."""
    clean = safe_name(name)
    for folder in (downloads_dir(task_id), INBOX):
        p = (folder / clean).resolve()
        if p.parent == folder.resolve() and p.is_file():
            return p
    names = ", ".join(f["name"] for f in list_files(task_id)) or "none"
    raise FileError(f"There is no file named {clean!r}. Files you can use: {names}.")


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"


# ---------------------------------------------------------------- reading

def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages[:200], 1):
            pages.append(f"--- page {i} ---\n" + (page.extract_text() or "").strip())
        text = "\n".join(pages)
        if not re.sub(r"--- page \d+ ---|\s", "", text):
            return "(This PDF has no text layer: it is probably scanned images. Open it in the browser and use look.)"
        return text
    if ext == ".docx":
        import docx

        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t_i, table in enumerate(d.tables, 1):
            parts.append(f"--- table {t_i} ---")
            parts += [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        return "\n".join(parts)
    if ext in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets[:10]:
            parts.append(f"--- sheet {ws.title} ---")
            for r_i, row in enumerate(ws.iter_rows(values_only=True)):
                if r_i >= 300:
                    parts.append("... (more rows)")
                    break
                if any(v is not None for v in row):
                    parts.append(" | ".join("" if v is None else str(v) for v in row))
        wb.close()
        return "\n".join(parts)
    if ext in (".csv", ".tsv"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        rows = list(csv.reader(io.StringIO(raw), delimiter="\t" if ext == ".tsv" else ","))[:500]
        return "\n".join(" | ".join(r) for r in rows)
    if ext == ".json":
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            return json.dumps(json.loads(raw), indent=1, ensure_ascii=False)
        except ValueError:
            return raw
    if ext in (".html", ".htm"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
    if ext in TEXT_EXT or not ext:
        return path.read_text(encoding="utf-8", errors="replace")
    raise FileError(f"Cannot read {path.suffix} files. Readable: PDF, Word (.docx), Excel (.xlsx), CSV and text.")


def read_part(path: Path, part: int = 1) -> str:
    text = re.sub(r"\n{3,}", "\n\n", extract_text(path)).strip()
    parts = max(1, (len(text) + PART - 1) // PART)
    part = max(1, min(part, parts))
    out = f"File {path.name}, part {part} of {parts}:\n" + text[(part - 1) * PART: part * PART]
    if part < parts:
        out += f"\n[End of part {part} of {parts}: call read_file with part={part + 1} for more.]"
    return out
