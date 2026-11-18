# -*- coding: utf-8 -*-
"""读取 .docx：字号、字体、颜色、对齐、缩进、制表位、行距、图片锚点。

python-docx 只暴露了常用属性，这里补上两个它不管的地方：
1. 段落里嵌套的文本框（很多简历模板的照片/侧栏在文本框里）；
2. 浮动图片的页面坐标（页眉 logo、证件照），用来还原版式。
"""
import hashlib
import os
from typing import Dict, List, Optional

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Length
from docx.table import Table
from docx.text.paragraph import Paragraph as DocxParagraph
from docx.text.run import Run as DocxRun

from .model import Document, ImageBox, Paragraph, Run, clean_text

ALIGN_MAP = {
    WD_ALIGN_PARAGRAPH.LEFT: "left",
    WD_ALIGN_PARAGRAPH.CENTER: "center",
    WD_ALIGN_PARAGRAPH.RIGHT: "right",
    WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
    WD_ALIGN_PARAGRAPH.DISTRIBUTE: "justify",
}

BULLET_CHARS = "•·●○▪■▫◆◇➤►▶➢✓✔※"


def _half_points(value) -> Optional[float]:
    try:
        return float(value) / 2.0
    except (TypeError, ValueError):
        return None


def _default_size(doc) -> float:
    """Word 文档默认字号（styles.xml 的 docDefaults）。"""
    try:
        styles = doc.styles.element
        defaults = styles.find(qn("w:docDefaults"))
        if defaults is not None:
            rpr = defaults.find(qn("w:rPrDefault"))
            if rpr is not None:
                rpr = rpr.find(qn("w:rPr"))
                if rpr is not None:
                    sz = rpr.find(qn("w:sz"))
                    if sz is not None:
                        size = _half_points(sz.get(qn("w:val")))
                        if size:
                            return size
    except Exception:  # noqa: BLE001 - 版式识别不该因为读默认值失败而中断
        pass
    return 10.5


def _style_size(style) -> Optional[float]:
    seen = set()
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        try:
            if style.font is not None and style.font.size is not None:
                return style.font.size.pt
        except Exception:  # noqa: BLE001
            pass
        style = getattr(style, "base_style", None)
    return None


def _run_size(run: DocxRun, paragraph_size: Optional[float], default: float) -> float:
    if run.font is not None and run.font.size is not None:
        return run.font.size.pt
    size = _style_size(run.style)
    if size:
        return size
    return paragraph_size or default


def _run_bold(run: DocxRun) -> bool:
    if run.bold is not None:
        return bool(run.bold)
    try:
        if run.style is not None and run.style.font is not None:
            if run.style.font.bold is not None:
                return bool(run.style.font.bold)
    except Exception:  # noqa: BLE001
        pass
    return False


def _run_color(run: DocxRun) -> Optional[str]:
    try:
        color = run.font.color
        if color is not None and color.type is not None and color.rgb is not None:
            return "#" + str(color.rgb).upper()
    except Exception:  # noqa: BLE001
        pass
    return None


def _east_asia_font(run: DocxRun) -> Optional[str]:
    try:
        rpr = run._r.rPr
        if rpr is not None:
            fonts = rpr.find(qn("w:rFonts"))
            if fonts is not None:
                name = fonts.get(qn("w:eastAsia")) or fonts.get(qn("w:ascii"))
                if name:
                    return name
    except Exception:  # noqa: BLE001
        pass
    try:
        if run.font is not None and run.font.name:
            return run.font.name
    except Exception:  # noqa: BLE001
        pass
    return None


def _paragraph_line(paragraph, size: float) -> Optional[float]:
    pf = paragraph.paragraph_format
    value = pf.line_spacing
    if value is None:
        return None
    if isinstance(value, Length):
        return round(value.pt, 3)
    if isinstance(value, (int, float)):
        return round(float(value) * size, 3)
    try:
        return round(float(value.pt), 3)
    except Exception:  # noqa: BLE001
        return None


def _pt(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return round(float(value.pt), 3)
    except Exception:  # noqa: BLE001
        try:
            return round(float(value), 3)
        except Exception:  # noqa: BLE001
            return default


def _apply_alignment(p: DocxParagraph, paragraph: Paragraph) -> None:
    alignment = p.paragraph_format.alignment
    if alignment is not None:
        paragraph.align = ALIGN_MAP.get(alignment, "left")


def _tab_positions(p: DocxParagraph) -> List[float]:
    out: List[float] = []
    try:
        for stop in p.paragraph_format.tab_stops:
            out.append(round(stop.position.pt, 3))
    except Exception:  # noqa: BLE001
        pass
    return out


def _is_list_paragraph(p: DocxParagraph) -> bool:
    try:
        ppr = p._p.pPr
        return ppr is not None and ppr.numPr is not None
    except Exception:  # noqa: BLE001
        return False


def _extract_images(p: DocxParagraph, doc, assets_dir: Optional[str]) -> List[ImageBox]:
    boxes: List[ImageBox] = []
    if assets_dir is None:
        return boxes
    try:
        drawings = list(p._p.iter(qn("w:drawing")))
    except Exception:  # noqa: BLE001
        return boxes

    for drawing in drawings:
        extent = drawing.find(".//" + qn("wp:extent"))
        if extent is None:
            continue
        try:
            width = float(extent.get("cx")) / 12700.0
            height = float(extent.get("cy")) / 12700.0
        except (TypeError, ValueError):
            continue

        blips = list(drawing.iter(qn("a:blip")))
        if not blips:
            continue
        rid = blips[0].get(qn("r:embed"))
        if not rid:
            continue
        try:
            part = doc.part.related_parts[rid]
            blob = part.blob
            ext = os.path.splitext(str(part.partname))[1].lower() or ".png"
        except Exception:  # noqa: BLE001
            continue

        digest = hashlib.sha1(blob).hexdigest()[:12]
        filename = "img_%s%s" % (digest, ext)
        target = os.path.join(assets_dir, filename)
        if not os.path.exists(target):
            os.makedirs(assets_dir, exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(blob)

        x = y = 0.0
        anchor = drawing.find(".//" + qn("wp:anchor"))
        if anchor is not None:
            pos_h = anchor.find(".//" + qn("wp:positionH"))
            pos_v = anchor.find(".//" + qn("wp:positionV"))
            x = _offset_pt(pos_h)
            y = _offset_pt(pos_v)
        boxes.append(ImageBox(path=filename, x=round(x, 2), y=round(y, 2),
                              w=round(width, 2), h=round(height, 2),
                              anchored=anchor is not None))
    return boxes


def _offset_pt(position_el) -> float:
    if position_el is None:
        return 0.0
    offset = position_el.find(qn("wp:posOffset"))
    if offset is None or not offset.text:
        return 0.0
    try:
        return float(offset.text) / 12700.0
    except ValueError:
        return 0.0


def _read_paragraph(p_el, doc, default_size: float, assets_dir: Optional[str],
                    source: str = "docx") -> Paragraph:
    p = DocxParagraph(p_el, doc)
    paragraph = Paragraph(source=source)
    paragraph.tab_positions = _tab_positions(p)
    _apply_alignment(p, paragraph)

    pf = p.paragraph_format
    paragraph.space_before = _pt(pf.space_before)
    paragraph.space_after = _pt(pf.space_after)
    paragraph.left_indent = _pt(pf.left_indent)
    paragraph.right_indent = _pt(pf.right_indent)
    paragraph.first_line_indent = _pt(pf.first_line_indent)

    style_size = _style_size(p.style)
    sizes: Dict[float, int] = {}
    fonts: Dict[str, int] = {}
    colors: Dict[str, int] = {}

    for r_el in p_el.iter(qn("w:r")):
        run = DocxRun(r_el, p)
        text = run.text
        if not text:
            continue
        size = _run_size(run, style_size, default_size)
        model_run = Run(text=text, bold=_run_bold(run), size=round(size, 2),
                        font=_east_asia_font(run), color=_run_color(run))
        paragraph.runs.append(model_run)
        sizes[size] = sizes.get(size, 0) + len(text)
        if model_run.font:
            fonts[model_run.font] = fonts.get(model_run.font, 0) + len(text)
        if model_run.color:
            colors[model_run.color] = colors.get(model_run.color, 0) + len(text)

    if sizes:
        paragraph.size = round(max(sizes.items(), key=lambda kv: kv[1])[0], 2)
    else:
        paragraph.size = round(style_size or default_size, 2)
    if fonts:
        paragraph.font = max(fonts.items(), key=lambda kv: kv[1])[0]
    if colors:
        paragraph.color = max(colors.items(), key=lambda kv: kv[1])[0]

    paragraph.line_spacing = _paragraph_line(p, paragraph.size or default_size)
    paragraph.images = _extract_images(p, doc, assets_dir)

    text = clean_text(paragraph.text)
    if text[:1] and text[0] in BULLET_CHARS:
        paragraph.bullet_glyph = text[0]
    elif _is_list_paragraph(p):
        paragraph.bullet_glyph = "•"
    return paragraph


def read_docx(path: str, assets_dir: Optional[str] = None,
              filename: Optional[str] = None) -> Document:
    doc = DocxDocument(path)
    source_name = filename or os.path.basename(path)
    document = Document(filename=source_name, source="docx")
    document.page_width = round(doc.sections[0].page_width.pt, 2)
    document.page_height = round(doc.sections[0].page_height.pt, 2)
    document.margin_left = _pt(doc.sections[0].left_margin, 41.5)
    document.margin_right = _pt(doc.sections[0].right_margin, 39.8)
    document.margin_top = _pt(doc.sections[0].top_margin, 40.0)
    document.margin_bottom = _pt(doc.sections[0].bottom_margin, 36.0)
    default_size = _default_size(doc)

    body = doc.element.body
    tables = 0
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            document.paragraphs.append(
                _read_paragraph(child, doc, default_size, assets_dir))
        elif child.tag == qn("w:tbl"):
            tables += 1
            table = Table(child, doc)
            for row in table.rows:
                for cell in row.cells:
                    for p_el in cell._tc.iterchildren(qn("w:p")):
                        document.paragraphs.append(
                            _read_paragraph(p_el, doc, default_size, assets_dir))

    # 文本框里的内容（照片说明、侧栏信息）单独捞一遍
    textboxes = list(body.iter(qn("w:txbxContent")))
    if textboxes:
        document.warnings.append("模板里用了 %d 个文本框，文字已按出现顺序读出，位置关系可能丢失。"
                                 % len(textboxes))
        for box in textboxes:
            for p_el in box.iterchildren(qn("w:p")):
                paragraph = _read_paragraph(p_el, doc, default_size, assets_dir)
                if paragraph.text:
                    document.paragraphs.append(paragraph)

    if tables:
        document.has_tables = True
        document.table_count = tables
        document.warnings.append(
            "检测到 %d 个表格，已按「行 → 单元格 → 段落」顺序读取；"
            "双栏/侧栏模板会被拉平成单栏。" % tables)

    if not document.non_empty():
        document.warnings.append("这个 docx 里没有读到文字，可能是扫描件或整页都是图片。")
    return document
