# -*- coding: utf-8 -*-
"""版式主题（theme.json）。

一套 theme 描述「一页简历长什么样」：纸张、边距、字体、字号、行距、颜色、
项目符号缩进、页眉图片。生成 docx 和浏览器预览都以它为唯一依据。
"""
import copy
from typing import Dict, Optional

# 默认主题 = 「海投蓝本」：单栏、居中姓名、章节标题 12.5pt 粗体、正文 9pt。
DEFAULT_THEME: Dict = {
    "name": "海投蓝本",
    "source": "builtin",
    "page": {
        "width": 595.28,
        "height": 841.89,
        "margin_left": 41.5,
        "margin_right": 39.8,
        "margin_top": 40.0,
        "margin_bottom": 36.0,
    },
    "fonts": {"cjk": "Noto Sans SC", "latin": "Arial"},
    "sizes": {
        "name": 21.5,
        "section": 12.5,
        "entry": 11.0,
        "body": 9.0,
        "body_lg": 9.5,
        "contact": 9.0,
    },
    "colors": {"ink": "#000000", "soft": "#1A1A1A", "bar": "#9A9A9A"},
    "spacing": {
        "name_line": 26.0,
        "name_sb": 0.0,
        "contact_line": 12.0,
        "c1_sb": 4.0,
        "c2_sb": 6.0,
        "section_sb": 12.0,
        "section_line": 18.0,
        "entry_sb": 2.0,
        "entry_line": 15.0,
        "para_sb": 0.0,
        "para_line": 12.0,
        "bullet_line": 12.0,
        "sub_sb": 0.0,
        "block_sb": {},
    },
    "indent": {"bullet_left": 14.0, "bullet_hanging": 9.0},
    "header": {"center": True, "logo": None, "photo": None},
    "meta_tab": 120.0,
    "date_right": True,
}


def _deep_merge(base: Dict, extra: Dict) -> Dict:
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def normalize(theme: Optional[Dict]) -> Dict:
    """把任意 theme 补全成完整结构，并且把数值转成 float（防止 JSON 里出现字符串）。"""
    out = copy.deepcopy(DEFAULT_THEME)
    if theme:
        _deep_merge(out, copy.deepcopy(theme))

    for group in ("page", "sizes", "colors", "spacing", "indent"):
        for key, value in list(out.get(group, {}).items()):
            if key == "block_sb":
                out[group][key] = {
                    str(k): _number(v, 0.0) for k, v in (value or {}).items()
                }
            elif group != "colors":
                out[group][key] = _number(value, DEFAULT_THEME[group].get(key, 0.0))

    out["meta_tab"] = _number(out.get("meta_tab"), DEFAULT_THEME["meta_tab"])
    out["date_right"] = bool(out.get("date_right", True))

    header = out.setdefault("header", {"center": True, "logo": None, "photo": None})
    header["center"] = bool(header.get("center", True))
    for role in ("logo", "photo"):
        item = header.get(role)
        if isinstance(item, dict) and item.get("file"):
            box = {
                "file": str(item["file"]),
                "x": _number(item.get("x"), 0.0),
                "y": _number(item.get("y"), 0.0),
                "w": _number(item.get("w"), 0.0),
                "h": _number(item.get("h"), 0.0),
            }
            if box["w"] <= 0 or box["h"] <= 0:
                header[role] = None
            else:
                header[role] = box
        else:
            header[role] = None
    return out


def _number(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default or 0.0)
    if number != number:  # NaN
        return float(default or 0.0)
    return number



def text_width(theme: Dict) -> float:
    page = theme["page"]
    return max(120.0, _number(page["width"], 595.28) - _number(page["margin_left"], 41.5)
               - _number(page["margin_right"], 39.8))


def summary(theme: Dict) -> Dict:
    """给上传结果页展示的版式摘要。"""
    t = normalize(theme)
    return {
        "source": t.get("source", ""),
        "fonts": "%s / %s" % (t["fonts"]["cjk"], t["fonts"]["latin"]),
        "body": t["sizes"]["body"],
        "name": t["sizes"]["name"],
        "section": t["sizes"]["section"],
        "entry": t["sizes"]["entry"],
        "margins": "%.1f / %.1f / %.1f / %.1f"
        % (t["page"]["margin_top"], t["page"]["margin_right"],
           t["page"]["margin_bottom"], t["page"]["margin_left"]),
        "page": "%.0f × %.0f pt" % (t["page"]["width"], t["page"]["height"]),
    }