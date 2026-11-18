# -*- coding: utf-8 -*-
"""一条龙：文件 / 文本 → 识别结果。CLI 和 HTTP 服务共用，保证两条入口行为一致。"""
import os

from .docx_reader import read_docx
from .pdf_reader import read_pdf
from .recognize import Recognition, recognize
from .text_reader import read_text, read_text_file


def analyze_file(path: str, assets_dir: str = None, filename: str = None) -> Recognition:
    ext = os.path.splitext(path)[1].lower()
    name = filename or os.path.basename(path)
    if ext == ".docx":
        document = read_docx(path, assets_dir, name)
    elif ext == ".pdf":
        document = read_pdf(path, assets_dir, name)
    elif ext in (".txt", ".md"):
        document = read_text_file(path, name)
    else:
        raise ValueError("只支持 .docx / .pdf / .txt / .md，当前是 %s" % (ext or "未知格式"))
    return recognize(document, name)


def analyze_text(text: str, filename: str = "粘贴文本") -> Recognition:
    return recognize(read_text(text, filename), filename)
