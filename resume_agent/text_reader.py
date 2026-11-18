# -*- coding: utf-8 -*-
"""纯文本入口：粘贴文本，或上传 .txt / .md。

没有版式信息，只能按行猜结构：项目符号开头 → 要点；带日期 → 条目；
「标签：」→ 加粗标签。字号行距由默认主题补齐。
"""
import os
import re
from typing import List

from .model import Document, Paragraph, Run, clean_text

BULLET_RE = re.compile(r"^\s*[•·●○▪■▫◆◇\-*—–➤►▶]\s+")


def read_text(text: str, filename: str = "粘贴文本") -> Document:
    document = Document(filename=filename, source="text")
    for raw_line in (text or "").splitlines():
        line = clean_text(raw_line)
        if not line:
            continue
        match = BULLET_RE.match(line)
        body = line[match.end():] if match else line
        paragraph = Paragraph(runs=[Run(text=body, bold=False)], source="text")
        if match:
            paragraph.bullet_glyph = line[:match.end()].strip()[:1] or "•"
        document.paragraphs.append(paragraph)
    if not document.non_empty():
        raise ValueError("粘贴的内容是空的。")
    document.warnings.append("纯文本没有版式，字号 / 行距用的是默认主题，可在编辑页微调。")
    return document


def read_text_file(path: str, filename: str = None) -> Document:
    name = filename or os.path.basename(path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return read_text(fh.read(), name)
