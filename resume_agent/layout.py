# -*- coding: utf-8 -*-
"""一页估算：不打开 Word 也能知道排出来大概多高、会不会超页。

这是估算，不是排版引擎：编辑器里的 DOM 测量才是最终标准。上传页先用它给
一个「约 0.93 页」的初判，省得用户点进编辑页才发现超页。
"""
import math
from typing import Dict, List

from . import theme as theme_mod


def text_width(text: str, size: float) -> float:
    width = 0.0
    for ch in text:
        if ch == " ":
            width += size * 0.3
        elif ord(ch) > 0x2E80:
            width += size
        else:
            width += size * 0.52
    return width


def wrap_lines(text: str, size: float, available: float) -> int:
    if not text:
        return 1
    width = text_width(text, size)
    if available <= 1:
        return max(1, int(math.ceil(width / max(size, 1))))
    return max(1, int(math.ceil(width / available)))


def block_text(block: Dict) -> str:
    if block.get("type") == "entry":
        parts = [block.get("title", ""), block.get("meta", "")]
        parts.extend(block.get("meta_segments") or [])
        if block.get("date"):
            parts.append(block["date"])
        return " ".join(parts)
    if block.get("type") == "subline":
        return block.get("text", "")
    return "".join(text for text, _ in block.get("runs", []))


def estimate_height(content: Dict, theme: Dict) -> Dict:
    t = theme_mod.normalize(theme)
    sizes, spacing = t["sizes"], t["spacing"]
    page, margins = t["page"], t["page"]
    body = float(sizes["body"])
    width = theme_mod.text_width(t)

    used = float(page["margin_top"]) + float(spacing["name_sb"]) + float(spacing["name_line"])
    used += float(spacing["c1_sb"]) + float(spacing["contact_line"])
    used += float(spacing["c2_sb"]) + float(spacing["contact_line"])

    for section in content.get("sections", []):
        used += float(spacing["section_sb"]) + float(spacing["section_line"])
        for block in section.get("blocks", []):
            kind = block.get("type")
            if kind == "entry":
                used += float(spacing["entry_sb"]) + float(spacing["entry_line"])
            elif kind == "bullet":
                indent = float(t["indent"]["bullet_left"])
                lines = wrap_lines(block_text(block), body, width - indent)
                used += lines * float(spacing["bullet_line"])
            elif kind == "subline":
                used += float(spacing["sub_sb"]) + float(spacing["para_line"])
            else:
                size = float(block.get("size") or body)
                line_h = float(spacing["para_line"]) * max(1.0, size / body)
                lines = wrap_lines(block_text(block), size, width)
                used += float(spacing["para_sb"]) + lines * line_h

    usable = float(page["height"]) - float(page["margin_top"]) - float(page["margin_bottom"])
    pages = used / usable if usable > 0 else 0
    return {
        "used": round(used, 1),
        "usable": round(usable, 1),
        "pages": round(pages, 2),
        "remaining": round(usable - used, 1),
        "fits": used <= usable,
    }
