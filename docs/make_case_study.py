"""Print docs/case_study.md as a styled A4 PDF (headless Edge or Chrome, no extra package).

    python docs/make_case_study.py                 # -> docs/case_study.pdf
    python docs/make_case_study.py --no-links out.pdf   # without the repository link (Upwork)

Handles the Markdown the case study uses: headings, paragraphs, lists, tables, images, bold,
inline code, links, a rule, and the mermaid flowchart (drawn as HTML boxes).
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
BROWSERS = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")]

CSS = """
@page { size: A4; margin: 14mm 14mm 14mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 9.6pt; color: #1e293b; line-height: 1.45; margin: 0; }
header { background: #0b1220; color: #fff; border-radius: 6pt; padding: 16pt 18pt 14pt; margin-bottom: 12pt;
         border-top: 4pt solid #38bdf8; }
header .kicker { color: #38bdf8; font-weight: 700; font-size: 8.5pt; letter-spacing: .08em; }
header h1 { font-size: 21pt; margin: 3pt 0 6pt; color: #fff; }
header p { margin: 0; color: #cbd5e1; font-size: 10pt; }
h2 { font-size: 13.5pt; color: #0b1220; margin: 16pt 0 6pt; padding-bottom: 3pt; border-bottom: 2px solid #38bdf8;
     break-after: avoid; }
p { margin: 4pt 0 6pt; }
ul { margin: 4pt 0 6pt 14pt; padding: 0; }
li { margin: 2pt 0; }
a { color: #0369a1; text-decoration: none; }
code { font-family: Consolas, monospace; font-size: 8.6pt; background: #eef2f7; padding: 0 2pt; border-radius: 2pt; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.9pt; break-inside: avoid; }
th { background: #0b1220; color: #fff; text-align: left; padding: 4pt 6pt; font-weight: 600; }
td { border-bottom: 1px solid #e2e8f0; padding: 4pt 6pt; vertical-align: top; }
tbody tr:nth-child(even) td { background: #f8fafc; }
td.num, th.num { text-align: right; white-space: nowrap; }
table.glance td:first-child { width: 24%; font-weight: 600; color: #0b1220; }
table.glance th { display: none; }
table.glance { border-left: 3pt solid #38bdf8; background: #f8fafc; }
figure { margin: 8pt 0 10pt; break-inside: avoid; }
figure img { width: 100%; border-radius: 4pt; border: 1px solid #cbd5e1; display: block; }
figcaption { font-size: 8.3pt; color: #64748b; margin-top: 3pt; }
.flow { display: flex; align-items: center; gap: 0; margin: 8pt 0 10pt; break-inside: avoid; }
.flow .box { flex: 1; background: #0b1220; color: #fff; border-radius: 5pt; padding: 7pt 6pt; text-align: center;
             font-size: 8.4pt; line-height: 1.3; border-top: 3pt solid #38bdf8; }
.flow .box b { display: block; font-size: 10pt; margin-bottom: 2pt; }
.flow .box.bronze { border-top-color: #c2843a; } .flow .box.silver { border-top-color: #94a3b8; }
.flow .box.gold { border-top-color: #eab308; }
.flow .arrow { color: #38bdf8; font-size: 15pt; padding: 0 4pt; }
hr { border: 0; border-top: 1px solid #cbd5e1; margin: 14pt 0 6pt; }
.foot { font-size: 8.3pt; color: #64748b; }
"""


def inline(text: str) -> str:
    out = []
    for part in re.split(r"(`[^`]*`)", text):
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
        else:
            s = html.escape(part, quote=False)
            s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
            out.append(s)
    return re.sub(r"\[(.+?)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', "".join(out))


def flow(block: list[str]) -> str:
    """mermaid `A["x<br/>y"] --> B[...]` lines -> boxes with arrows"""
    boxes = []
    for line in block:
        for label in re.findall(r'\["(.+?)"\]', line):
            if label not in boxes:
                boxes.append(label)
    classes = ["", "bronze", "silver", "gold", ""]
    parts = []
    for k, label in enumerate(boxes):
        title, *rest = label.split("<br/>")
        parts.append(f'<div class="box {classes[k] if k < len(classes) else ""}"><b>{html.escape(title)}</b>'
                     f'{"<br>".join(html.escape(r) for r in rest)}</div>')
    return '<div class="flow">' + '<span class="arrow">→</span>'.join(parts) + "</div>"


def md_to_html(md: str, no_links: bool) -> str:
    lines, out, i = md.splitlines(), [], 0
    if no_links:
        lines = [x for x in lines if "github.com" not in x]
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("# "):                       # the title becomes the header band
            i += 1
            para = []
            while i < len(lines) and lines[i].strip():
                para.append(lines[i])
                i += 1
            out.append(f'<header><div class="kicker">CASE STUDY · DATA ENGINEERING</div>'
                       f'<h1>{inline(line[2:].replace("Case study: a", "A"))}</h1><p>{inline(" ".join(para))}</p></header>')
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
            i += 1
        elif line.startswith("```mermaid"):
            i += 1
            block = []
            while not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            out.append(flow(block))
            i += 1
        elif line.startswith("!["):
            alt, src = re.match(r"!\[(.*?)\]\((.*?)\)", line).groups()
            uri = (DOCS / src).resolve().as_uri()
            out.append(f'<figure><img src="{uri}" alt="{html.escape(alt)}"><figcaption>{inline(alt)}</figcaption></figure>')
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, align, body = rows[0], rows[1], rows[2:]
            right = [a.endswith(":") for a in align]
            cls = ' class="glance"' if head[0] == "At a glance" or not any(head) else ""
            th = "".join(f'<th{" class=num" if right[k] else ""}>{inline(c)}</th>' for k, c in enumerate(head))
            trs = "".join("<tr>" + "".join(f'<td{" class=num" if right[k] else ""}>{inline(c)}</td>'
                                           for k, c in enumerate(r)) + "</tr>" for r in body)
            out.append(f"<table{cls}><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")
        elif line.startswith("- "):
            items = []
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("  ")):
                if lines[i].startswith("- "):
                    items.append(lines[i][2:])
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
        elif line.strip() == "---":
            out.append("<hr>")
            i += 1
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", "- ", "![", "```")):
                para.append(lines[i])
                i += 1
            text = inline(" ".join(para))
            cls = ' class="foot"' if "fictional" in text and "people" in text else ""
            out.append(f"<p{cls}>{text}</p>")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(DOCS / "case_study.pdf"))
    ap.add_argument("--no-links", action="store_true", help="leave out the repository link")
    args = ap.parse_args()
    body = md_to_html((DOCS / "case_study.md").read_text(encoding="utf-8"), args.no_links)
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Case study · Retail Price &amp; '
            f'Procurement Intelligence</title><style>{CSS}</style></head><body>{body}</body></html>')
    browser = next((b for b in BROWSERS if b.exists()), None) or (shutil.which("msedge") and Path(shutil.which("msedge")))
    if not browser:
        sys.exit("No Edge or Chrome found")
    with tempfile.TemporaryDirectory() as tmp:
        page_path = Path(tmp) / "case_study.html"
        page_path.write_text(page, encoding="utf-8")
        out = Path(args.out).resolve()
        subprocess.run([str(browser), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={Path(tmp) / 'profile'}", "--allow-file-access-from-files",
                        f"--print-to-pdf={out}", page_path.as_uri()], check=True, capture_output=True, timeout=120)
    print(out)


if __name__ == "__main__":
    main()
