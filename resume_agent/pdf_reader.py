# -*- coding: utf-8 -*-
"""读取 .pdf（可选依赖 PyMuPDF）。

PDF 里没有「段落」和「行距」这种东西，只有一行行文字和坐标。这里的做法是：
按 y 坐标聚成行 → 按 x 坐标猜左/居中/右对齐和缩进 → 交给后面的结构识别。
所以 PDF 的还原度天生低于 docx，报告里会明确写出来。
"""
import os
import re
from typing import Dict, List, Optional, Tuple

from .model import Document, ImageBox, Paragraph, Run, clean_text

BULLET_CHARS = "•·●○▪■▫◆◇➤►▶➢✓✔※"
DATE_PART = r"((?:19|20)\d{2}\s*[./年\-]\s*\d{1,2}\s*月?)"
DATE_RANGE = re.compile(
    DATE_PART + r"\s*(?:[-–—~～至到]|－)\s*"
    r"((?:19|20)\d{2}\s*[./年\-]\s*\d{1,2}\s*月?|至今|现在|今)"
)
BOLD_KEYWORDS = ("bold", "black", "heavy", "semibold", "demi", "medium", "粗")


def _import_fitz():
    try:
        import pymupdf  # type: ignore
        return pymupdf
    except ImportError:
        try:
            import fitz  # type: ignore
            return fitz
        except ImportError:
            raise ImportError(
                "读取 PDF 需要 PyMuPDF：pip install pymupdf。"
                "或者把简历另存为 .docx 再上传，版式识别会更准。"
            )


def _font_name(raw: str) -> str:
    raw = (raw or "").split("+")[-1].strip()
    key = raw.lower().replace(" ", "").replace("-", "").replace("_", "")
    if "notosanssc" in key or "notosanscjk" in key or "notosans" in key:
        return "Noto Sans SC"
    if "sourcehansans" in key:
        return "Source Han Sans SC"
    if "microsoftyahei" in key or "msyh" in key:
        return "Microsoft YaHei"
    if "simhei" in key:
        return "SimHei"
    if "simsun" in key:
        return "SimSun"
    if "pingfang" in key:
        return "PingFang SC"
    if "arial" in key:
        return "Arial"
    name = re.sub(r"[-_ ]?(regular|bold|italic|light|medium|semibold|black|heavy)$", "",
                  raw, flags=re.I)
    return name.strip()


def _is_bold(flags: int, font: str) -> bool:
    if flags & 16:  # PyMuPDF 的 bold 位
        return True
    lowered = (font or "").lower()
    return any(word in lowered for word in BOLD_KEYWORDS)


def _hex_color(value: int) -> str:
    return "#%06X" % (int(value) & 0xFFFFFF)


def _span_runs(spans: List[Dict]) -> Tuple[List[Run], List[float]]:
    runs: List[Run] = []
    tab_offsets: List[float] = []
    origin = min(span["bbox"][0] for span in spans)
    previous_x1 = None
    previous_size = 10.0
    for span in sorted(spans, key=lambda s: s["bbox"][0]):
        text = span.get("text", "")
        if not text:
            continue
        size = round(float(span.get("size") or 0), 2)
        # 同一视觉行里相隔较远的两个文字块（标题 ←→ 日期/岗位）用制表符隔开
        if previous_x1 is not None and span["bbox"][0] - previous_x1 > max(4.0, previous_size * 0.6):
            runs.append(Run(text="\t", size=size))
            tab_offsets.append(round(span["bbox"][0] - origin, 1))
        runs.append(Run(text=text,
                        bold=_is_bold(span.get("flags", 0), span.get("font", "")),
                        size=size,
                        font=_font_name(span.get("font", "")),
                        color=_hex_color(span.get("color", 0))))
        previous_x1 = span["bbox"][2]
        previous_size = size or previous_size
    return runs, tab_offsets


def _page_lines(page) -> List[Dict]:
    data = page.get_text("dict")
    lines: List[Dict] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            x0 = min(s["bbox"][0] for s in spans)
            x1 = max(s["bbox"][2] for s in spans)
            y0 = min(s["bbox"][1] for s in spans)
            y1 = max(s["bbox"][3] for s in spans)
            text = "".join(s["text"] for s in spans).replace("\u00a0", " ")
            weight = sum(len(s.get("text", "")) for s in spans)
            size = sum(float(s.get("size") or 0) * len(s.get("text", "")) for s in spans) / max(1, weight)
            lines.append({
                "text": text,
                "spans": spans,
                "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                "size": size,
            })
    lines.sort(key=lambda line: (round(line["y0"], 1), line["x0"]))
    return lines


def _extract_images(page, doc, assets_dir: Optional[str]) -> List[ImageBox]:
    boxes: List[ImageBox] = []
    if not assets_dir:
        return boxes
    for info in page.get_images(full=True):
        xref = info[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:  # noqa: BLE001
            rects = []
        if not rects:
            continue
        try:
            raw = doc.extract_image(xref)
        except Exception:  # noqa: BLE001
            continue
        data = raw.get("image")
        if not data:
            continue
        ext = "." + (raw.get("ext") or "png")
        name = "img_pdf_%d%s" % (xref, ext)
        target = os.path.join(assets_dir, name)
        if not os.path.exists(target):
            os.makedirs(assets_dir, exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)
        rect = rects[0]
        width = rect.width
        height = rect.height
        if width < 8 or height < 8:
            continue
        boxes.append(ImageBox(path=name, x=round(rect.x0, 2), y=round(rect.y0, 2),
                              w=round(width, 2), h=round(height, 2), anchored=True))
    return boxes


def _detect_columns(lines: List[Dict], page_width: float) -> int:
    """粗判双栏：正文行的起点集中在两条互不重叠的竖带上。"""
    starts = [line["x0"] for line in lines if len(line["text"]) > 8]
    if len(starts) < 12:
        return 1
    left = [x for x in starts if x < page_width * 0.42]
    right = [x for x in starts if x > page_width * 0.48]
    if len(left) >= 6 and len(right) >= 6:
        # 两栏都要有「齐头」的行，且不是右对齐日期
        left_std = _std(left)
        right_std = _std(right)
        if left_std < 8 and right_std < 8:
            return 2
    return 1


def _std(values: List[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _cluster_lines(lines: List[Dict]) -> List[Dict]:
    """把同一视觉行里被拆散的片段聚成一行。

    PDF 里「迅雷（11pt 粗体）　广告设计实习生（9pt）　日期（9pt 右对齐）」常常是
    三个独立的行对象，y 坐标差 1-2pt。不聚起来的话，条目会被拆成三段普通文字。
    """
    clusters: List[Dict] = []
    for line in sorted(lines, key=lambda item: (item.get("page", 0), item["y0"], item["x0"])):
        center = (line["y0"] + line["y1"]) / 2.0
        target = None
        for cluster in reversed(clusters[-8:]):
            if cluster.get("page") != line.get("page"):
                continue
            cluster_center = (cluster["y0"] + cluster["y1"]) / 2.0
            if abs(center - cluster_center) <= max(line["size"], cluster["size"]) * 0.55:
                target = cluster
                break
        if target is None:
            clusters.append({
                "spans": list(line["spans"]),
                "y0": line["y0"], "y1": line["y1"],
                "x0": line["x0"], "x1": line["x1"],
                "size": line["size"], "page": line.get("page", 0),
                "text": line["text"],
            })
        else:
            target["spans"].extend(line["spans"])
            target["y0"] = min(target["y0"], line["y0"])
            target["y1"] = max(target["y1"], line["y1"])
            target["x0"] = min(target["x0"], line["x0"])
            target["x1"] = max(target["x1"], line["x1"])
            target["size"] = max(target["size"], line["size"])

    for cluster in clusters:
        cluster["spans"].sort(key=lambda span: span["bbox"][0])
        cluster["text"] = "".join(span["text"] for span in cluster["spans"])
    clusters.sort(key=lambda item: (item.get("page", 0), item["y0"], item["x0"]))
    return clusters


def read_pdf(path: str, assets_dir: Optional[str] = None,
             filename: Optional[str] = None) -> Document:
    fitz = _import_fitz()
    pdf = fitz.open(path)
    document = Document(filename=filename or os.path.basename(path), source="pdf")
    page_width = float(pdf[0].rect.width)
    page_height = float(pdf[0].rect.height)
    document.page_width = round(page_width, 2)
    document.page_height = round(page_height, 2)

    all_lines: List[Dict] = []
    images: List[ImageBox] = []
    for page_index, page in enumerate(pdf):
        lines = _page_lines(page)
        for line in lines:
            line["page"] = page_index
        all_lines.extend(lines)
        if page_index == 0:
            images = _extract_images(page, pdf, assets_dir)

    if not all_lines:
        document.warnings.append("这份 PDF 没有文字层，可能是扫描件或图片导出的；"
                                 "需要 OCR，或直接上传 docx。")
        pdf.close()
        return document

    if len(pdf) > 1:
        document.warnings.append("PDF 有 %d 页，内容已按页顺序合并；一页版式只参考第 1 页。"
                                 % len(pdf))

    document.columns = _detect_columns(all_lines, page_width)
    if document.columns == 1:
        # 同一视觉行的标题/岗位/日期常被 PDF 拆成多个 y 略有差异的“行”，先按纵向重叠聚回去
        all_lines = _cluster_lines(all_lines)
    else:
        document.warnings.append("检测到双栏/侧栏结构，已按「先左后右」的阅读顺序拉平；"
                                 "复杂版式建议直接传 docx。")

    left = _percentile([line["x0"] for line in all_lines], 2, 0.0)
    right_raw = page_width - _percentile([line["x1"] for line in all_lines], 98, page_width)
    top = _percentile([line["y0"] for line in all_lines], 0, 30.0)
    bottom_raw = page_height - _percentile([line["y1"] for line in all_lines], 98, page_height)
    # PDF 常常没写满整页，右边/下边留白会被误读成边距，超过阈值就用常规值
    right = right_raw if right_raw <= 90 else 39.8
    bottom = bottom_raw if bottom_raw <= 72 else 40.0
    document.margin_left = round(_clamp(left, 10, 120, 41.5), 1)
    document.margin_right = round(_clamp(right, 10, 120, 39.8), 1)
    document.margin_top = round(_clamp(top, 8, 120, 40.0), 1)
    document.margin_bottom = round(_clamp(bottom, 8, 120, 36.0), 1)

    page_center = page_width / 2.0
    text_left = document.margin_left
    text_right = page_width - document.margin_right

    previous: Optional[Paragraph] = None
    previous_line: Optional[Dict] = None
    for line in all_lines:
        runs, tab_offsets = _span_runs(line["spans"])
        if not runs:
            continue
        paragraph = Paragraph(runs=runs, source="pdf")
        paragraph.tab_positions = tab_offsets
        paragraph.size = round(line["size"], 2)
        paragraph.font = max((run.font for run in runs if run.font),
                             key=lambda f: sum(len(r.text) for r in runs if r.font == f),
                             default=None)
        paragraph.color = max((run.color for run in runs if run.color),
                              key=lambda c: sum(len(r.text) for r in runs if r.color == c),
                              default=None)
        paragraph.left_indent = round(max(0.0, line["x0"] - text_left), 2)
        paragraph.first_line_indent = 0.0
        paragraph.line_spacing = round(float(line["size"]) * 1.33, 2)

        width = line["x1"] - line["x0"]
        if abs((line["x0"] + line["x1"]) / 2 - page_center) < 8 and width < page_width * 0.72:
            paragraph.align = "center"
        elif line["x1"] > text_right - 6 and width < page_width * 0.5:
            paragraph.align = "right"
        else:
            paragraph.align = "left"

        text = clean_text(line["text"])
        if text[:1] and text[0] in BULLET_CHARS:
            paragraph.bullet_glyph = text[0]

        if previous_line is not None:
            gap = line["y0"] - previous_line["y1"]
            if gap > 1:
                paragraph.space_before = round(max(0.0, gap - (line["size"] - previous_line["size"])), 2)

        # 换行续行：同一栏、同字号、上一行顶到右边 → 认为是一句话被折行
        if previous is not None and paragraph.align == "left" \
                and not paragraph.bullet_glyph and not DATE_RANGE.search(text) \
                and previous.align == "left":
            same_size = abs(paragraph.size - (previous.size or 0)) < 0.6
            gap = (line["y0"] - previous_line["y1"]) if previous_line else 99.0
            gap_ok = 0 <= gap < max(3.0, paragraph.size * 0.9)
            reaches_right = bool(previous_line) and previous_line["x1"] >= text_right - 30
            starts_bold = bool(paragraph.runs and paragraph.runs[0].bold)
            terminal = previous.raw_text.rstrip()[-1:] in "。；;！？!?"
            if same_size and gap_ok and reaches_right and not starts_bold and not terminal:
                previous.runs.append(Run(text=_join_suffix(previous.raw_text, paragraph.raw_text),
                                         size=previous.size, bold=False,
                                         font=previous.font, color=previous.color))
                previous_line = line
                continue

        document.paragraphs.append(paragraph)
        previous = paragraph
        previous_line = line

    # 图片挂到文档上（没有对应段落，识别时会单独取）
    if images:
        document.paragraphs.append(Paragraph(images=images, source="pdf"))

    pdf.close()
    return document


def _clamp(value: float, low: float, high: float, fallback: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, value))


def _is_cjk(char: str) -> bool:
    return bool(char) and ord(char) > 0x2E80


def _join_suffix(left: str, right: str) -> str:
    """折行拼接：中文之间不加空格，英文之间加一个空格。"""
    left = (left or "").rstrip()
    right = (right or "").lstrip()
    if not left:
        return right
    if not right:
        return ""
    if _is_cjk(left[-1]) or _is_cjk(right[0]):
        return right
    return " " + right


def _percentile(values: List[float], percent: float, fallback: float) -> float:
    if not values:
        return fallback
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percent / 100.0))
    return ordered[max(0, min(len(ordered) - 1, index))]
