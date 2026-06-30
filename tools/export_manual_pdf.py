#!/usr/bin/env python3
"""Export the Hebrew Iron661Capture user manual markdown to an RTL PDF."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import markdown

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "docs" / "Iron661Capture_user_manual_he.md"
HTML_PATH = ROOT / "docs" / "Iron661Capture_user_manual_he.html"
PDF_PATH = ROOT / "docs" / "Iron661Capture_user_manual_he.pdf"

EDGE_PATHS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)

CSS = """
@page {
    size: A4;
    margin: 2cm 2.2cm;
}
html, body {
    direction: rtl;
    text-align: right;
    font-family: "Segoe UI", "David", "Arial", sans-serif;
    font-size: 11pt;
    line-height: 1.55;
    color: #111;
}
h1 {
    font-size: 22pt;
    font-weight: 700;
    margin-top: 0;
    text-align: right;
}
h2 {
    font-size: 16pt;
    font-weight: 700;
    margin-top: 1.2em;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.2em;
    text-align: right;
}
h3 {
    font-size: 13pt;
    font-weight: 700;
    margin-top: 1em;
    text-align: right;
}
p, li, td, th {
    text-align: right;
}
ul, ol {
    padding-right: 1.5em;
    padding-left: 0;
    margin-right: 0;
}
blockquote {
    border-right: 3px solid #4472C4;
    border-left: none;
    margin: 1em 0;
    padding: 0.5em 1em 0.5em 0;
    background: #f5f8ff;
    text-align: right;
}
code {
    font-family: Consolas, "Courier New", monospace;
    font-size: 9pt;
    direction: ltr;
    unicode-bidi: embed;
}
pre {
    font-family: Consolas, "Courier New", monospace;
    font-size: 9pt;
    direction: ltr;
    text-align: left;
    background: #f4f4f4;
    padding: 0.5em;
    border: 1px solid #ddd;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    direction: rtl;
}
th, td {
    border: 1px solid #999;
    padding: 6px 8px;
    vertical-align: top;
    text-align: right;
}
th {
    background: #eef2f7;
    font-weight: 700;
}
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 1.5em 0;
}
a {
    color: #1a5490;
    text-decoration: none;
}
@media print {
    body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
"""


def _find_browser() -> Path:
    for path in EDGE_PATHS:
        if path.exists():
            return path
    raise FileNotFoundError(
        "No supported browser found (Microsoft Edge or Google Chrome required)."
    )


def _load_markdown() -> str:
    md_text = MD_PATH.read_text(encoding="utf-8").strip()
    if md_text.startswith('<div dir="rtl">'):
        md_text = md_text[len('<div dir="rtl">') :].strip()
    if md_text.endswith("</div>"):
        md_text = md_text[: -len("</div>")].strip()
    return md_text


def _build_html() -> str:
    html_body = markdown.markdown(
        _load_markdown(),
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    return (
        "<!DOCTYPE html>"
        '<html dir="rtl" lang="he">'
        "<head>"
        '<meta charset="utf-8"/>'
        f"<style>{CSS}</style>"
        "</head>"
        f"<body>{html_body}</body>"
        "</html>"
    )


def _file_url(path: Path) -> str:
    # Windows: file:///C:/Users/...  or  file:///G:/My%20Drive/...
    posix = path.resolve().as_posix()
    if len(posix) >= 2 and posix[1] == ":":
        drive = posix[0]
        tail = posix[3:] if len(posix) > 3 and posix[2] == "/" else posix[2:]
        encoded_tail = "/".join(quote(part, safe="") for part in tail.split("/"))
        return f"file:///{drive}:/{encoded_tail}"
    encoded = "/".join(quote(part, safe="") for part in posix.split("/"))
    return "file:///" + encoded


def _validate_pdf(pdf_path: Path) -> None:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    if len(reader.pages) < 2:
        raise RuntimeError(f"PDF looks truncated ({len(reader.pages)} page(s)): {pdf_path}")
    first = reader.pages[0].extract_text() or ""
    if "ERR_FILE_NOT_FOUND" in first or "File not found" in first:
        raise RuntimeError(f"PDF export captured a browser error page: {pdf_path}")


def _stage_html_for_print(html: str) -> tuple[Path, tempfile.TemporaryDirectory]:
    """Write HTML to a local temp folder so headless Edge can load it reliably."""
    tmp = tempfile.TemporaryDirectory(prefix="iron661_manual_")
    html_path = Path(tmp.name) / "manual.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path, tmp


def _print_pdf(browser: Path, html_path: Path, pdf_path: Path) -> Path:
    temp_pdf = pdf_path.with_suffix(".tmp.pdf")
    if temp_pdf.exists():
        temp_pdf.unlink()

    cmd = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--print-to-pdf={temp_pdf.resolve()}",
        "--no-pdf-header-footer",
        _file_url(html_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        raise RuntimeError(
            f"Browser PDF export failed (exit {result.returncode}).\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )
    if not temp_pdf.exists() or temp_pdf.stat().st_size < 1024:
        raise RuntimeError(f"PDF was not created or is too small: {temp_pdf}")

    _validate_pdf(temp_pdf)

    written_path = pdf_path
    try:
        if pdf_path.exists():
            pdf_path.unlink()
        temp_pdf.replace(pdf_path)
    except OSError:
        fallback = pdf_path.with_name(f"{pdf_path.stem}_new{pdf_path.suffix}")
        if fallback.exists():
            fallback.unlink()
        temp_pdf.replace(fallback)
        written_path = fallback
        print(
            f"Note: {pdf_path.name} is open — wrote {fallback.name} instead.",
            file=sys.stderr,
        )

    if temp_pdf.exists():
        temp_pdf.unlink()
    return written_path


def main() -> None:
    browser = _find_browser()
    html = _build_html()
    HTML_PATH.write_text(html, encoding="utf-8")

    staged_html, tmp = _stage_html_for_print(html)
    try:
        written = _print_pdf(browser, staged_html, PDF_PATH)
    finally:
        tmp.cleanup()

    print(f"Wrote {written}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
