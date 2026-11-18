# -*- coding: utf-8 -*-
"""统一的文档中间模型。

三种来源（docx / pdf / 纯文本）先被读成同一套 ``Document``，后面的
「结构识别」和「版式识别」就只需要处理一种数据形状。

单位约定：所有长度、字号一律用 **pt**（1pt = 1/72 inch），颜色用 ``#RRGGBB``。
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Run:
    """一段同样式文字。"""

    text: str
    bold: bool = False
    size: Optional[float] = None
    font: Optional[str] = None
    color: Optional[str] = None

    def clone(self) -> "Run":
        return Run(self.text, self.bold, self.size, self.font, self.color)


@dataclass
class ImageBox:
    """文档里的图片：path 是抽出来的本地文件，x/y/w/h 是页面坐标（pt）。"""

    path: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    role: Optional[str] = None  # logo / photo / other
    anchored: bool = True  # 浮动图片才有可信的页面坐标

    @property
    def aspect(self) -> float:
        return (self.h / self.w) if self.w else 0.0


@dataclass
class Paragraph:
    runs: List[Run] = field(default_factory=list)
    align: str = "left"  # left / center / right / justify
    size: Optional[float] = None  # 段落代表字号（按字数加权）
    font: Optional[str] = None
    color: Optional[str] = None
    space_before: float = 0.0
    space_after: float = 0.0
    line_spacing: Optional[float] = None  # 固定行距（pt）；None 表示按字号推算
    left_indent: float = 0.0
    right_indent: float = 0.0
    first_line_indent: float = 0.0
    tab_positions: List[float] = field(default_factory=list)
    bullet_glyph: Optional[str] = None
    images: List[ImageBox] = field(default_factory=list)
    source: str = "docx"

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs).replace("\u00a0", " ").strip()

    @property
    def raw_text(self) -> str:
        return "".join(r.text for r in self.runs).replace("\u00a0", " ")

    @property
    def bold(self) -> bool:
        """段落是否整体加粗（多数 run 加粗且文字够多）。"""
        total = sum(len(r.text) for r in self.runs) or 1
        bold = sum(len(r.text) for r in self.runs if r.bold)
        return bold / total >= 0.6 and len(self.text) > 1

    @property
    def char_count(self) -> int:
        return len(self.text.replace("\t", ""))

    def plain_runs(self) -> List[List]:
        """转成编辑器/JSON 用的 [[text, bold], ...]，顺手合并相邻同样式片段。"""
        out: List[List] = []
        for run in self.runs:
            text = run.text.replace("\u00a0", " ")
            if not text:
                continue
            if out and bool(out[-1][1]) == bool(run.bold):
                out[-1][0] += text
            else:
                out.append([text, 1 if run.bold else 0])
        if out:
            out[0][0] = out[0][0].lstrip()
            out[-1][0] = out[-1][0].rstrip()
        return [pair for pair in out if pair[0]] or [["", 0]]


@dataclass
class Document:
    paragraphs: List[Paragraph] = field(default_factory=list)
    page_width: float = 595.28
    page_height: float = 841.89
    margin_left: float = 41.5
    margin_right: float = 39.8
    margin_top: float = 40.0
    margin_bottom: float = 36.0
    source: str = "docx"
    filename: str = ""
    warnings: List[str] = field(default_factory=list)
    has_tables: bool = False
    table_count: int = 0
    columns: int = 1

    @property
    def text_width(self) -> float:
        return max(100.0, self.page_width - self.margin_left - self.margin_right)

    def non_empty(self) -> List[Paragraph]:
        return [p for p in self.paragraphs if p.text or p.images]

    def full_text(self) -> str:
        return "\n".join(p.text for p in self.non_empty())


def clean_text(text: str) -> str:
    """统一空白：去掉全角空格、压缩连续空格、去首尾。"""
    text = (text or "").replace("\u3000", " ").replace("\u00a0", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()
