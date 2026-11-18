# -*- coding: utf-8 -*-
"""按 theme 生成 docx。

旧版把排版常量写死在代码里；这一版所有几何量都来自 theme.json，所以
「上传模板 → 识别出版式 → 生成同款 docx」走的是同一条代码路径。
"""
import os
from typing import Dict, List, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from . import theme as theme_mod
from .model import clean_text

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def _rgb(hex_color: str, fallback=(0, 0, 0)) -> RGBColor:
    try:
        value = hex_color.lstrip("#")
        return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except Exception:  # noqa: BLE001
        return RGBColor(*fallback)


def _set_run_font(run, size: float, bold: bool, color: RGBColor,
                  cjk: str, latin: str) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = latin
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    fonts.set(qn("w:ascii"), latin)
    fonts.set(qn("w:hAnsi"), latin)
    fonts.set(qn("w:eastAsia"), cjk)
    fonts.set(qn("w:cs"), latin)


def _fmt_par(p, line: Optional[float] = None, before: float = 0.0, after: float = 0.0,
             align=None, left: float = 0.0, right: float = 0.0, hanging: float = 0.0,
             keep_with_next: bool = False) -> None:
    pf = p.paragraph_format
    if line and line > 0:
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        pf.line_spacing = Pt(line)
    pf.space_before = Pt(max(0.0, before))
    pf.space_after = Pt(max(0.0, after))
    if align is not None:
        p.alignment = align
    pf.left_indent = Pt(max(0.0, left))
    pf.right_indent = Pt(max(0.0, right))
    if hanging:
        pf.first_line_indent = Pt(-hanging)
    pf.keep_with_next = keep_with_next
    pf.widow_control = False

    ppr = p._p.get_or_add_pPr()
    for tag in ("w:autoSpaceDE", "w:autoSpaceDN"):
        el = ppr.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            ppr.insert_element_before(el, "w:spacing", "w:ind", "w:contextualSpacing",
                                      "w:jc", "w:rPr", "w:sectPr", "w:pPrChange")
        el.set(qn("w:val"), "0")


def _add_anchor_picture(paragraph, path: str, box: Dict, shape_id: int) -> None:
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    run = paragraph.add_run()
    run.add_picture(path, width=Pt(w), height=Pt(h))
    drawing = run._r.find(qn("w:drawing"))
    if drawing is None:
        return
    inline = drawing[0]
    graphic = inline.find(qn("a:graphic"))
    if graphic is None:
        return

    anchor = OxmlElement("wp:anchor")
    for key, val in (("distT", "0"), ("distB", "0"), ("distL", "0"), ("distR", "0"),
                     ("simplePos", "0"), ("relativeHeight", str(251658240 + shape_id)),
                     ("behindDoc", "0"), ("locked", "0"), ("layoutInCell", "1"),
                     ("allowOverlap", "1")):
        anchor.set(key, val)

    simple_pos = OxmlElement("wp:simplePos")
    simple_pos.set("x", "0")
    simple_pos.set("y", "0")
    anchor.append(simple_pos)

    for tag, offset in (("wp:positionH", x), ("wp:positionV", y)):
        pos = OxmlElement(tag)
        pos.set("relativeFrom", "page")
        off = OxmlElement("wp:posOffset")
        off.text = str(int(round(offset * 12700)))
        pos.append(off)
        anchor.append(pos)

    extent = OxmlElement("wp:extent")
    extent.set("cx", str(int(round(w * 12700))))
    extent.set("cy", str(int(round(h * 12700))))
    anchor.append(extent)

    eff = OxmlElement("wp:effectExtent")
    for key in ("l", "t", "r", "b"):
        eff.set(key, "0")
    anchor.append(eff)
    anchor.append(OxmlElement("wp:wrapNone"))

    doc_pr = OxmlElement("wp:docPr")
    doc_pr.set("id", str(shape_id))
    doc_pr.set("name", "Picture %d" % shape_id)
    anchor.append(doc_pr)
    anchor.append(OxmlElement("wp:cNvGraphicFramePr"))
    anchor.append(graphic)

    drawing.remove(inline)
    drawing.append(anchor)


def _header_parts(editor: Dict) -> List[List[Dict]]:
    """把 _editor 字段拼成两行：联系方式行 + 求职意向行。"""
    line1: List[Dict] = []

    def add(text: str) -> None:
        text = clean_text(text)
        if not text:
            return
        if line1:
            line1.append({"bar": True, "t": "|"})
        line1.append({"t": text})

    add(editor.get("phone", ""))
    add(editor.get("email", ""))
    add(editor.get("city", ""))
    for extra in (editor.get("extras") or [])[:4]:
        add(extra)

    intent = clean_text(editor.get("intent", ""))
    availability = clean_text(editor.get("availability", ""))
    line2: List[Dict] = []
    if intent:
        line2.append({"t": "求职意向：%s " % intent})
    if availability:
        if line2:
            line2.append({"bar": True, "t": "|"})
        line2.append({"t": " %s" % availability})
    return [line1, line2]


def build(content: Dict, theme: Dict, out_path: str,
          assets_dir: Optional[str] = None) -> str:
    t = theme_mod.normalize(theme)
    assets_dir = assets_dir or ASSETS_DIR
    editor = content.get("_editor") or {}
    sizes, spacing, indent = t["sizes"], t["spacing"], t["indent"]
    colors = t["colors"]
    cjk, latin = t["fonts"]["cjk"], t["fonts"]["latin"]
    ink, soft, bar = _rgb(colors["ink"]), _rgb(colors["soft"]), _rgb(colors["bar"])

    doc = Document()
    doc.core_properties.title = editor.get("basename") or "简历"
    doc.core_properties.author = editor.get("name") or ""
    doc.core_properties.comments = "Generated by resume-agent"

    style = doc.styles["Normal"]
    style.font.name = latin
    style.font.size = Pt(sizes["body"])
    style_rpr = style.element.get_or_add_rPr()
    fonts = style_rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        style_rpr.insert(0, fonts)
    fonts.set(qn("w:ascii"), latin)
    fonts.set(qn("w:hAnsi"), latin)
    fonts.set(qn("w:eastAsia"), cjk)

    # 关掉 Word 的中西文自动间距，让密度和设计稿一致
    styles_el = doc.styles.element
    defaults = styles_el.find(qn("w:docDefaults"))
    if defaults is not None:
        ppr_default = defaults.find(qn("w:pPrDefault"))
        if ppr_default is None:
            ppr_default = OxmlElement("w:pPrDefault")
            defaults.append(ppr_default)
        ppr = ppr_default.find(qn("w:pPr"))
        if ppr is None:
            ppr = OxmlElement("w:pPr")
            ppr_default.append(ppr)
        for tag in ("w:autoSpaceDE", "w:autoSpaceDN"):
            el = OxmlElement(tag)
            el.set(qn("w:val"), "0")
            ppr.append(el)

    page = t["page"]
    section = doc.sections[0]
    section.page_width = Pt(page["width"])
    section.page_height = Pt(page["height"])
    section.left_margin = Pt(page["margin_left"])
    section.right_margin = Pt(page["margin_right"])
    section.top_margin = Pt(page["margin_top"])
    section.bottom_margin = Pt(page["margin_bottom"])
    section.header_distance = Pt(20)
    section.footer_distance = Pt(20)

    text_w = theme_mod.text_width(t)
    logo = t["header"].get("logo")
    photo = t["header"].get("photo")

    def sb_for(key: str, default: float) -> float:
        return float(spacing.get("block_sb", {}).get(key, default))

    # ---- 页眉：姓名 + 两行联系方式 ----
    name_para = doc.add_paragraph()
    right_indent = (photo["w"] + 8.0) if photo else 0.0
    _fmt_par(name_para, line=spacing["name_line"], before=sb_for("name", spacing["name_sb"]),
             align=WD_ALIGN_PARAGRAPH.CENTER, right=right_indent)
    name_run = name_para.add_run(editor.get("name", ""))
    _set_run_font(name_run, sizes["name"], False, ink, cjk, latin)

    if logo:
        path = os.path.join(assets_dir, logo["file"])
        if os.path.exists(path):
            _add_anchor_picture(name_para, path, logo, 101)
    if photo:
        path = os.path.join(assets_dir, photo["file"])
        if os.path.exists(path):
            _add_anchor_picture(name_para, path, photo, 102)

    line1, line2 = _header_parts(editor)
    for key, parts, default_sb in (("contact1", line1, spacing["c1_sb"]),
                                   ("contact2", line2, spacing["c2_sb"])):
        if not parts:
            continue
        contact = doc.add_paragraph()
        _fmt_par(contact, line=spacing["contact_line"], before=sb_for(key, default_sb),
                 align=WD_ALIGN_PARAGRAPH.CENTER, right=right_indent)
        for part in parts:
            if part.get("bar"):
                run = contact.add_run(part.get("t", "|"))
                _set_run_font(run, sizes["contact"], False, bar, cjk, latin)
            else:
                run = contact.add_run(part.get("t", ""))
                _set_run_font(run, sizes["contact"], False, ink, cjk, latin)

    # ---- 正文 ----
    for index, sec in enumerate(content.get("sections", [])):
        key = sec.get("key") or "s%d" % index
        heading = doc.add_paragraph()
        _fmt_par(heading, line=spacing["section_line"],
                 before=sb_for(key + "_h", spacing["section_sb"]),
                 keep_with_next=True)
        run = heading.add_run(sec.get("title", ""))
        _set_run_font(run, sizes["section"], True, ink, cjk, latin)

        for block_index, block in enumerate(sec.get("blocks", [])):
            bkey = "%s_b%d" % (key, block_index)
            kind = block.get("type")
            if kind == "entry":
                entry = doc.add_paragraph()
                _fmt_par(entry, line=spacing["entry_line"],
                         before=sb_for(bkey, spacing["entry_sb"]), keep_with_next=True)
                tabs = entry.paragraph_format.tab_stops
                meta_segments = block.get("meta_segments")
                if meta_segments:
                    for pos in block.get("meta_tabs") or []:
                        if 0 < pos < text_w:
                            tabs.add_tab_stop(Pt(pos), WD_TAB_ALIGNMENT.LEFT)
                elif block.get("meta"):
                    pos = block.get("meta_tab") or t["meta_tab"]
                    tabs.add_tab_stop(Pt(min(pos, text_w - 2)), WD_TAB_ALIGNMENT.LEFT)
                if block.get("date") and t.get("date_right", True):
                    tabs.add_tab_stop(Pt(text_w), WD_TAB_ALIGNMENT.RIGHT)

                run = entry.add_run(block.get("title", ""))
                _set_run_font(run, sizes["entry"], True, ink, cjk, latin)
                if meta_segments:
                    for segment in meta_segments:
                        run = entry.add_run("\t" + segment)
                        _set_run_font(run, sizes["body"], False, soft, cjk, latin)
                elif block.get("meta"):
                    run = entry.add_run("\t" + block["meta"])
                    _set_run_font(run, sizes["body"], False, soft, cjk, latin)
                if block.get("date"):
                    run = entry.add_run("\t" + block["date"])
                    _set_run_font(run, sizes["body"], False, soft, cjk, latin)

            elif kind == "bullet":
                bullet = doc.add_paragraph()
                _fmt_par(bullet, line=spacing["bullet_line"], before=sb_for(bkey, 0.0),
                         left=indent["bullet_left"], hanging=indent["bullet_hanging"])
                run = bullet.add_run("\u2022\t")
                _set_run_font(run, sizes["body"], False, ink, cjk, latin)
                for text, bold in block.get("runs", []):
                    run = bullet.add_run(text)
                    _set_run_font(run, sizes["body"], bool(bold), ink, cjk, latin)

            elif kind == "subline":
                subline = doc.add_paragraph()
                _fmt_par(subline, line=spacing["para_line"],
                         before=sb_for(bkey, spacing["sub_sb"]), keep_with_next=True)
                run = subline.add_run(block.get("text", ""))
                _set_run_font(run, sizes["body"], False, soft, cjk, latin)

            else:
                size = float(block.get("size") or sizes["body"])
                line = spacing["para_line"] * max(1.0, size / sizes["body"])
                para = doc.add_paragraph()
                _fmt_par(para, line=line, before=sb_for(bkey, spacing["para_sb"]))
                for text, bold in block.get("runs", []):
                    run = para.add_run(text)
                    _set_run_font(run, size, bool(bold), ink, cjk, latin)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    doc.save(out_path)
    return out_path
