#!/usr/bin/env python3
"""
Builds the submission PDF from docs/report.md.

Pipeline: Markdown -> styled HTML -> headless Microsoft Edge -> A4 PDF.
The report is a single self-contained PDF with the screenshots embedded, which
is exactly what the course asks for (`number_name.pdf`).

Usage:
    py -3.11 scripts/build_report_pdf.py
    py -3.11 scripts/build_report_pdf.py --name "Ada Lovelace" --number "b211210001"
    py -3.11 scripts/build_report_pdf.py --out "b211210001_lovelace.pdf"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

EDGE_CANDIDATES = [
    Path(os.environ.get("EDGE_PATH", "")),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]

CSS = """
@page { size: A4; margin: 17mm 15mm 18mm 15mm; }

:root {
  --ink: #1d2320;
  --muted: #5c6660;
  --line: #d8ded9;
  --accent: #0f766e;
  --code-bg: #f4f6f4;
}

* { box-sizing: border-box; }

body {
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  color: var(--ink);
  font-size: 10.4pt;
  line-height: 1.48;
  margin: 0;
}

h1, h2, h3, h4 { line-height: 1.25; color: #111815; }
h1 {
  font-size: 22pt;
  margin: 0 0 6pt;
  padding-bottom: 6pt;
  border-bottom: 3px solid var(--accent);
}
h1:first-of-type { page-break-before: avoid; }
h2 {
  font-size: 15pt;
  margin: 22pt 0 8pt;
  page-break-after: avoid;
}
h3 { font-size: 11.6pt; margin: 16pt 0 6pt; page-break-after: avoid; }
h4 { font-size: 10.6pt; margin: 12pt 0 4pt; page-break-after: avoid; }

p, li { orphans: 3; widows: 3; }
p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; }
li { margin-bottom: 3pt; }

a { color: var(--accent); text-decoration: none; }

code {
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: 0.9em;
  background: var(--code-bg);
  padding: 1px 4px;
  border-radius: 3px;
}
pre {
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: 8.2pt;
  line-height: 1.4;
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  border-radius: 4px;
  padding: 7pt 9pt;
  white-space: pre-wrap;
  word-break: break-word;
  page-break-inside: avoid;
  margin: 0 0 9pt;
}
pre code { background: none; padding: 0; font-size: inherit; }

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 8.9pt;
  margin: 4pt 0 11pt;
  page-break-inside: auto;
}
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td {
  border: 1px solid var(--line);
  padding: 4pt 5pt;
  text-align: left;
  vertical-align: top;
}
th { background: #eef3f0; font-weight: 600; }

img {
  display: block;
  max-width: 100%;
  max-height: 25cm;
  width: auto;
  height: auto;
  margin: 6pt auto 10pt;
  border: 1px solid var(--line);
  border-radius: 4px;
  page-break-inside: avoid;
}
p > img { page-break-before: auto; }

blockquote {
  margin: 0 0 9pt;
  padding: 6pt 10pt;
  background: #f7f9f7;
  border-left: 3px solid var(--accent);
  color: var(--muted);
}

hr { border: none; border-top: 1px solid var(--line); margin: 14pt 0; }

.toc {
  background: #f7f9f7;
  border: 1px solid var(--line);
  border-radius: 5px;
  padding: 8pt 14pt;
  margin-bottom: 14pt;
  font-size: 9.4pt;
  page-break-before: always;
}
.toc ul { padding-left: 16pt; margin: 2pt 0; }
.toc > ul > li { font-weight: 600; }
.toc > ul > li > ul > li { font-weight: 400; }

/* Keep two screenshots that follow each other on facing pages readable. */
img + img { margin-top: 0; }
"""

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def find_browser() -> Path | None:
    for candidate in EDGE_CANDIDATES:
        try:
            if candidate and candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the RescueLink report PDF")
    parser.add_argument("--source", default="report.md", help="Markdown file in docs/")
    parser.add_argument("--html", default="report.html")
    parser.add_argument("--out", default="report.pdf")
    parser.add_argument("--name", default="", help="replaces <name surname>")
    parser.add_argument("--number", default="", help="replaces <student number>")
    args = parser.parse_args()

    source = DOCS / args.source
    if not source.exists():
        print(f"Source not found: {source}")
        return 2

    text = source.read_text(encoding="utf-8")
    if args.name:
        text = text.replace("<name surname>", args.name)
    if args.number:
        text = text.replace("<student number>", args.number)

    body = markdown.markdown(
        text,
        extensions=["extra", "toc", "sane_lists", "tables", "fenced_code"],
        extension_configs={"toc": {"title": "Contents"}},
    )
    html_path = DOCS / args.html
    html_path.write_text(
        HTML.format(title="RescueLink — DBMS Report", css=CSS, body=body),
        encoding="utf-8",
    )
    print(f"HTML  -> {html_path}")

    browser = find_browser()
    if browser is None:
        print("No Edge/Chrome found. Open the HTML file and print to PDF manually.")
        return 1

    pdf_path = DOCS / args.out
    if pdf_path.exists():
        pdf_path.unlink()

    cmd = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--disable-extensions",
        "--no-pdf-header-footer",
        "--virtual-time-budget=8000",
        f"--print-to-pdf={pdf_path}",
        html_path.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if not pdf_path.exists():
        print("PDF generation failed.")
        print((result.stderr or result.stdout)[-2000:])
        return 1

    size_mb = pdf_path.stat().st_size / (1024 * 1024)
    print(f"PDF   -> {pdf_path}  ({size_mb:.1f} MB)")
    print("\nRename it to the required number_name.pdf before uploading to Sabis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
