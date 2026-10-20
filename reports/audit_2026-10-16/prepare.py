"""Materialize only tracked HEAD bytes; inventory all repository documents."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
TARGET = OUT / "_sandbox" / "futures_portfolio"
BASE = "44db0bb9d1b1675868ef351de24422ed6c7ab817"
files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "-z", BASE], cwd=ROOT).decode().split("\0")
inventory = []
review = []
for name in filter(None, files):
    body = subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
    dest = TARGET / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    if Path(name).suffix.lower() not in (".md", ".pdf"):
        continue
    if name.endswith(".pdf"):
        reader = PdfReader(dest)
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        pages = len(reader.pages)
    else:
        text = body.decode("utf-8-sig")
        pages = None
    path = OUT / "document_text" / (name.replace("/", "__") + ".txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    lines = text.splitlines()
    headings = [f"{i}: {line}" for i, line in enumerate(lines, 1) if line.startswith("#")]
    pattern = re.compile(r"spot|фьючер|futures|плеч|leverage|аванс|вознаграж|HWM|liability|Equity|15 секунд|24 месяц|6 месяц|Profit Factor|unit|passed|coverage|исправ|backtest|бэктест|readonly|read.only|emergency|PM2|sqlite|Decimal|float|устар|2026-07-22", re.I)
    selected = [f"{i}: {line}" for i, line in enumerate(lines, 1) if pattern.search(line)]
    inventory.append(dict(path=name, sha256=hashlib.sha256(body).hexdigest(), bytes=len(body),
                          lines=len(lines), pages=pages, extracted_characters=len(text), headings=headings))
    review.append("\n===== " + name + " =====\n" + "\n".join(headings[:12]) + "\n" +
                  "\n".join(selected[:18]) + "\n" + "\n".join(selected[-5:]))
(OUT / "documents.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
(OUT / "document_review_extract.txt").write_text("\n".join(review), encoding="utf-8")
print(json.dumps(dict(tracked_files=len(files)-1, documents=len(inventory),
                     document_bytes=sum(i["bytes"] for i in inventory), sandbox=str(TARGET))))
