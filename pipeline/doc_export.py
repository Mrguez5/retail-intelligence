"""Export docs/data_dictionary.md to a styled HTML page and a PDF (A4 landscape).

The converter handles the Markdown subset the dictionary uses (headings, paragraphs,
lists, tables, bold, inline code, links and anchors), so no extra package is needed.
The PDF is printed by Microsoft Edge or Google Chrome in headless mode.

    python run.py docs --pdf
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path

from . import config

BROWSERS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
]

CSS = """
@page { size: A4 landscape; margin: 12mm 10mm 12mm 10mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 9pt; color: #1f2933; line-height: 1.4; margin: 0; }
h1 { font-size: 22pt; color: #1F3864; margin: 0 0 4pt; }
h2 { font-size: 15pt; color: #1F3864; border-bottom: 2px solid #1F3864; padding-bottom: 3pt; margin: 18pt 0 8pt;
     break-before: page; }
h2.first { break-before: auto; }
h3 { font-size: 11.5pt; color: #102a43; margin: 16pt 0 4pt; break-after: avoid; }
h3 code { font-size: 11.5pt; background: none; padding: 0; color: #102a43; }
p { margin: 4pt 0; }
ul { margin: 4pt 0 4pt 16pt; padding: 0; }
a { color: #2457a7; text-decoration: none; }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 8.3pt; background: #eef2f7;
       padding: 0 2pt; border-radius: 2pt; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.3pt; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { background: #1F3864; color: #fff; text-align: left; padding: 3pt 5pt; font-weight: 600; }
td { border-bottom: 1px solid #d9e2ec; padding: 3pt 5pt; vertical-align: top; }
tbody tr:nth-child(even) td { background: #f7f9fc; }
td.num, th.num { text-align: right; white-space: nowrap; }
.badge { display: inline-block; font-size: 7pt; font-weight: 700; padding: 0 4pt; border-radius: 3pt; }
.pk { background: #f0b429; color: #3e2c00; }
.fk { background: #c6dcf7; color: #0b3a6e; }
.warn { color: #b42318; font-weight: 600; }
.meta { background: #f0f4f8; border-left: 3px solid #1F3864; padding: 4pt 8pt; margin: 4pt 0; }
.cover { margin-bottom: 10pt; }
.cover .sub { color: #52606d; font-size: 10pt; }
"""


def _inline(text: str) -> str:
    """Escape HTML, then apply inline code, bold and links."""
    parts = re.split(r"(`[^`]*`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        s = html.escape(part)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = s.replace("_No description._", "<em>No description.</em>")
        out.append(s)
    joined = "".join(out)
    # links may wrap inline code: [`x`](#y)
    return re.sub(r"\[(.+?)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', joined)


def _split_row(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", s)]


def _cell(text: str) -> str:
    s = _inline(text)
    s = re.sub(r"(^|\s)PK(?=\s|$)", r'\1<span class="badge pk">PK</span>', s)
    s = s.replace("FK →", '<span class="badge fk">FK</span> →')
    s = s.replace("⚠ not documented", '<span class="warn">⚠ not documented</span>')
    return s


def md_to_html(md: str) -> str:
    lines = md.splitlines()
    out, i, first_h2 = [], 0, True
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("<a id="):
            out.append(line)
            i += 1
        elif line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            cls = ""
            if level == 2:
                cls = ' class="first"' if first_h2 else ""
                first_h2 = False
            out.append(f"<h{level}{cls}>{_inline(line[level:].strip())}</h{level}>")
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            head, align, body = rows[0], rows[1], rows[2:]
            right = [a.endswith(":") for a in align]
            th = "".join(f'<th{" class=num" if right[k] else ""}>{_inline(c)}</th>' for k, c in enumerate(head))
            trs = "".join("<tr>" + "".join(f'<td{" class=num" if right[k] else ""}>{_cell(c)}</td>'
                                           for k, c in enumerate(r)) + "</tr>" for r in body)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", "- ", "<a id=")):
                para.append(lines[i])
                i += 1
            text = _inline(" ".join(para))
            cls = ' class="meta"' if text.startswith("<strong>Grain:") else ""
            out.append(f"<p{cls}>{text}</p>")
    return "\n".join(out)


def export(md_path: Path) -> tuple[Path, Path | None]:
    md = md_path.read_text(encoding="utf-8")
    body = md_to_html(md)
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Data dictionary · Retail Price & '
            f'Procurement Intelligence</title><style>{CSS}</style></head><body>'
            f'<div class="cover"><div class="sub">Retail Price &amp; Procurement Intelligence</div></div>'
            f"{body}</body></html>")
    html_path = md_path.with_suffix(".html")
    html_path.write_text(page, encoding="utf-8")

    browser = next((b for b in BROWSERS if b.exists()), None) or (shutil.which("msedge") and Path(shutil.which("msedge")))
    if not browser:
        print("No Edge or Chrome found: open the HTML and print it to PDF from the browser.")
        return html_path, None
    pdf_path = md_path.with_suffix(".pdf")
    subprocess.run([str(browser), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                   check=True, capture_output=True, timeout=120)
    return html_path, pdf_path
