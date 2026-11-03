"""
services/search/document_extractor.py
---------------------------------------
Robust content extraction for every document type SMARTMEM indexes.

Supports:
  PDF       — via pypdf (pure Python, no system deps)
  DOCX/DOC  — via python-docx
  XLSX/XLS  — via openpyxl (reads cell values as text)
  CSV       — direct read, sample rows
  PPTX      — via python-pptx (optional)
  Plain text — TXT, MD, RST, LOG, TEX, etc.
  Code files — PY, JS, TS, Java, Go, Rust, etc.
  JSON/YAML  — structural preview
  HTML       — tag-stripped text

All extraction is graceful — returns "" if the library is missing or
the file is corrupted, so the embedding falls back to filename only.
"""

import logging
import pathlib
import re

logger = logging.getLogger(__name__)

MAX_CHARS = 2000   # Characters extracted per document for embedding


def extract_text(path: str, max_chars: int = MAX_CHARS) -> str:
    """
    Extract human-readable text from `path`.
    Returns a cleaned string of up to max_chars characters.
    Returns "" on any failure — never raises.
    """
    try:
        ext = pathlib.Path(path).suffix.lower()
        if ext == ".pdf":
            return _extract_pdf(path, max_chars)
        elif ext in {".docx", ".doc"}:
            return _extract_docx(path, max_chars)
        elif ext in {".xlsx", ".xls"}:
            return _extract_xlsx(path, max_chars)
        elif ext == ".csv":
            return _extract_csv(path, max_chars)
        elif ext in {".pptx", ".ppt"}:
            return _extract_pptx(path, max_chars)
        elif ext == ".html":
            return _extract_html(path, max_chars)
        elif ext == ".ipynb":
            return _extract_ipynb(path, max_chars)
        elif ext in _PLAIN_TEXT_EXTS:
            return _extract_plaintext(path, max_chars)
        else:
            return ""
    except Exception as exc:
        logger.debug("extract_text failed for %s: %s", path, exc)
        return ""


_PLAIN_TEXT_EXTS = {
    ".txt", ".md", ".rst", ".log", ".tex", ".org",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".c", ".cpp", ".cc", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".sh", ".bash", ".zsh", ".ps1",
    ".sql", ".r", ".m", ".lua", ".dart", ".zig",
    ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".conf", ".env", ".xml",
}


# ── PDF ───────────────────────────────────────────────────────────────────────

def _extract_pdf(path: str, max_chars: int) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf not installed — PDF extraction disabled. "
                       "Run: pip install pypdf")
        return ""

    try:
        reader = PdfReader(path, strict=False)
        parts = []
        chars = 0
        for page in reader.pages:
            if chars >= max_chars:
                break
            try:
                text = page.extract_text() or ""
                parts.append(text)
                chars += len(text)
            except Exception:
                continue
        return _clean(" ".join(parts), max_chars)
    except Exception as exc:
        logger.debug("PDF extract failed %s: %s", path, exc)
        return ""


# ── DOCX ──────────────────────────────────────────────────────────────────────

def _extract_docx(path: str, max_chars: int) -> str:
    try:
        import docx
    except ImportError:
        logger.warning("python-docx not installed — DOCX extraction disabled. "
                       "Run: pip install python-docx")
        return ""

    try:
        doc = docx.Document(path)
        parts = []
        chars = 0
        for para in doc.paragraphs:
            if chars >= max_chars:
                break
            t = para.text.strip()
            if t:
                parts.append(t)
                chars += len(t)
        # Also grab table cell text
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if chars >= max_chars:
                        break
                    t = cell.text.strip()
                    if t:
                        parts.append(t)
                        chars += len(t)
        return _clean(" ".join(parts), max_chars)
    except Exception as exc:
        logger.debug("DOCX extract failed %s: %s", path, exc)
        return ""


# ── XLSX ──────────────────────────────────────────────────────────────────────

def _extract_xlsx(path: str, max_chars: int) -> str:
    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl not installed — XLSX extraction disabled. "
                       "Run: pip install openpyxl")
        return ""

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts = []
        chars = 0
        for sheet in wb.worksheets:
            if chars >= max_chars:
                break
            parts.append(f"[Sheet: {sheet.title}]")
            for row in sheet.iter_rows(max_row=100, values_only=True):
                if chars >= max_chars:
                    break
                row_text = " | ".join(str(v) for v in row if v is not None)
                if row_text.strip():
                    parts.append(row_text)
                    chars += len(row_text)
        wb.close()
        return _clean(" ".join(parts), max_chars)
    except Exception as exc:
        logger.debug("XLSX extract failed %s: %s", path, exc)
        return ""


# ── CSV ───────────────────────────────────────────────────────────────────────

def _extract_csv(path: str, max_chars: int) -> str:
    try:
        import csv
        parts = []
        chars = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if chars >= max_chars or i > 200:
                    break
                line = " | ".join(str(v) for v in row if v)
                if line:
                    parts.append(line)
                    chars += len(line)
        return _clean(" ".join(parts), max_chars)
    except Exception as exc:
        logger.debug("CSV extract failed %s: %s", path, exc)
        return ""


# ── PPTX ─────────────────────────────────────────────────────────────────────

def _extract_pptx(path: str, max_chars: int) -> str:
    try:
        from pptx import Presentation
    except ImportError:
        return ""
    try:
        prs  = Presentation(path)
        parts = []
        chars = 0
        for slide in prs.slides:
            if chars >= max_chars:
                break
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    parts.append(shape.text.strip())
                    chars += len(shape.text)
        return _clean(" ".join(parts), max_chars)
    except Exception as exc:
        logger.debug("PPTX extract failed %s: %s", path, exc)
        return ""


# ── HTML ──────────────────────────────────────────────────────────────────────

def _extract_html(path: str, max_chars: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read(max_chars * 3)
        # Strip tags
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"&[a-z]+;", " ", text)
        return _clean(text, max_chars)
    except Exception:
        return ""


# ── Jupyter Notebook ──────────────────────────────────────────────────────────

def _extract_ipynb(path: str, max_chars: int) -> str:
    try:
        import json
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            nb = json.load(f)
        parts = []
        chars = 0
        for cell in nb.get("cells", []):
            if chars >= max_chars:
                break
            src = "".join(cell.get("source", []))
            if src.strip():
                parts.append(src)
                chars += len(src)
        return _clean(" ".join(parts), max_chars)
    except Exception:
        return ""


# ── Plain text / code ─────────────────────────────────────────────────────────

def _extract_plaintext(path: str, max_chars: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(max_chars * 2)   # read extra, then trim
        return _clean(content, max_chars)
    except Exception:
        return ""


# ── Utilities ─────────────────────────────────────────────────────────────────

def _clean(text: str, max_chars: int) -> str:
    """Collapse whitespace and trim to max_chars."""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]
