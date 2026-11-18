# -*- coding: utf-8 -*-
"""识别 Agent：把 Document 变成「可编辑内容 JSON + 版式 theme + 识别报告」。

流程（也就是 README 里说的三步）：
1. 认角色：哪行是姓名、联系方式、章节标题、条目、要点、段落；
2. 认版式：字号 / 字体 / 颜色 / 行距 / 边距 / 页眉图片；
3. 出稿：按编辑器的数据结构组装，缺什么就报什么，不猜内容。
"""
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import theme as theme_mod
from .layout import estimate_height
from .model import Document, Paragraph, clean_text

# ---------------------------------------------------------------- 文本规则

PHONE_RE = re.compile(r"(?:\+?86[\s-]?)?1[3-9]\d{9}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DATE_PART = r"((?:19|20)\d{2}\s*[./年\-]\s*\d{1,2}\s*月?)"
DATE_RANGE = re.compile(
    DATE_PART + r"\s*(?:[-–—~～至到]|－)\s*"
    r"((?:19|20)\d{2}\s*[./年\-]\s*\d{1,2}\s*月?|至今|现在|今)"
)
DATE_ONLY = re.compile(r"^\s*(?:" + DATE_PART +
                       r"\s*(?:[-–—~～至到]|－)\s*"
                       r"(?:(?:19|20)\d{2}\s*[./年\-]\s*\d{1,2}\s*月?|至今|现在|今)"
                       r"|(?:19|20)\d{2}(?:\s*[./年]\s*\d{1,2})?)\s*$")

HEADING_WORDS = [
    "教育经历", "教育背景", "学习经历", "学历背景",
    "实习经历", "实习经验", "工作经历", "工作经验", "职业经历", "工作履历",
    "项目经历", "项目经验", "科研项目",
    "校园经历", "校园活动", "实践经历", "社会实践", "社团经历", "在校经历",
    "专业技能", "技能特长", "个人技能", "技能证书", "能力标签", "核心能力",
    "荣誉奖项", "获奖情况", "获奖经历", "荣誉证书", "所获荣誉",
    "自我评价", "个人评价", "自我介绍", "个人优势", "个人总结", "关于我",
    "研究经历", "科研成果", "发表论文", "学术成果", "作品展示", "作品集",
    "基本信息", "个人信息", "联系方式", "求职意向",
]

SECTION_KEYS = [
    ("edu", ("教育", "学历", "学习经历")),
    ("intern", ("实习", "工作经历", "工作经验", "职业经历", "工作履历")),
    ("proj", ("项目", "作品", "科研")),
    ("campus", ("校园", "社团", "实践", "在校", "社会活动")),
    ("skill", ("技能", "能力", "证书")),
    ("award", ("荣誉", "奖项", "获奖")),
    ("summary", ("自我", "个人评价", "自我介绍", "优势", "总结", "关于我")),
    ("research", ("研究", "论文", "学术")),
]

PLACEHOLDER_RE = re.compile(
    r"【[^】]{1,20}】|\[[^\]\n]{1,20}\]|\{\{[^}\n]{1,20}\}\}|"
    r"(?:X{2,}|x{2,}|某某|待填写|请填写|你的名字|请在此填写)"
)

BULLET_CHARS = "•·●○▪■▫◆◇➤►▶➢✓✔※"


# ---------------------------------------------------------------- 小工具

def normalize_date(raw: str) -> str:
    """"2020/9 - 2024/6" → "2020.09—2024.06"；"至今" 保留。"""
    raw = (raw or "").replace("年", ".").replace("月", "")
    pairs = re.findall(r"((?:19|20)\d{2})\s*[./\-]\s*(\d{1,2})", raw)
    if not pairs:
        year = re.search(r"((?:19|20)\d{2})", raw)
        return year.group(1) if year else clean_text(raw)
    text = "—".join("%s.%02d" % (y, int(m)) for y, m in pairs)
    if re.search(r"至今|现在|今", raw):
        text += "—至今"
    return text


def extract_date(text: str) -> Tuple[Optional[str], str]:
    match = DATE_RANGE.search(text)
    if not match:
        only = DATE_ONLY.match(text)
        if only:
            return normalize_date(text), ""
        return None, text
    date = normalize_date(match.group(0))
    rest = (text[:match.start()] + " " + text[match.end():]).strip()
    return date, rest


def weighted_mode(items: List[Tuple[float, int]]):
    """按文字长度加权的众数（字号、颜色都用）。数字四舍五入到 0.1 再统计。"""
    if not items:
        return None
    counter: Dict = {}
    representatives: Dict = {}
    for value, weight in items:
        if value is None:
            continue
        try:
            key = round(float(value), 1)
        except (TypeError, ValueError):
            key = str(value)
        counter[key] = counter.get(key, 0) + max(1, weight)
        representatives[key] = value
    best = max(counter.items(), key=lambda kv: kv[1])[0]
    return representatives.get(best, best)


def median(values: List[float], default: float = 0.0) -> float:
    values = sorted(v for v in values if v is not None)
    if not values:
        return default
    middle = len(values) // 2
    if len(values) % 2:
        return round(float(values[middle]), 2)
    return round((values[middle - 1] + values[middle]) / 2.0, 2)


def text_width_pt(text: str, size: float) -> float:
    width = 0.0
    for ch in text:
        if ch == " ":
            width += size * 0.3
        elif ord(ch) > 0x2E80:
            width += size
        else:
            width += size * 0.52
    return width


def is_bullet(p: Paragraph) -> bool:
    if p.bullet_glyph:
        return True
    return p.left_indent >= 5 and p.first_line_indent < -1


def is_heading(p: Paragraph, body: float) -> bool:
    text = clean_text(p.text)
    if not text or len(text) > 24:
        return False
    if DATE_RANGE.search(text) or EMAIL_RE.search(text) or PHONE_RE.search(text):
        return False
    plain = text.rstrip("：: 　")
    for word in HEADING_WORDS:
        if plain == word or plain.startswith(word):
            return len(plain) <= len(word) + 4
    if p.bold and (p.size or body) >= body + 2.4 and len(plain) <= 12 \
            and not re.search(r"[，。；;,.]", plain):
        return True
    return False


def section_key(title: str) -> str:
    for key, words in SECTION_KEYS:
        if any(word in title for word in words):
            return key
    return "custom"


def clean_heading(title: str) -> str:
    title = clean_text(title)
    title = title.lstrip("▍■●○◆◇|｜·•-— ")
    title = title.rstrip("：: 　·•")
    return title.strip()


# ---------------------------------------------------------------- 识别主体

@dataclass
class Recognition:
    content: Dict
    theme: Dict
    report: Dict
    warnings: List[str] = field(default_factory=list)


def recognize(document: Document, filename: Optional[str] = None) -> Recognition:
    start = time.perf_counter()
    warnings = list(document.warnings)

    paragraphs = document.non_empty()
    if not paragraphs:
        raise ValueError("没有读到任何文字，无法识别。")

    # ---- 1. 认角色 ----
    name_para, contact_paras, rest = _split_header(paragraphs)
    body_size = _body_size(rest, name_para)
    headings, section_list = _split_sections(rest, body_size, warnings)

    # ---- 2. 抽内容 + 记录每类段落的样式样本 ----
    content, samples = _build_content(document, name_para, contact_paras, headings,
                                      section_list, body_size, warnings)
    structure_ms = (time.perf_counter() - start) * 1000

    # ---- 3. 认版式 ----
    theme_start = time.perf_counter()
    theme = _build_theme(document, samples, body_size, filename, content)
    theme_ms = (time.perf_counter() - theme_start) * 1000

    # ---- 4. 一页估算 + 报告 ----
    fit = estimate_height(content, theme)
    confidence, conf_notes = _confidence(document, content, samples, theme, warnings)
    report = {
        "file": document.filename or filename or "",
        "source": document.source,
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "notes": conf_notes,
        "counts": _counts(content),
        "theme": theme_mod.summary(theme),
        "fit": fit,
        "warnings": warnings,
        "timings": {
            "structure": round(structure_ms, 1),
            "theme": round(theme_ms, 1),
            "total": round((time.perf_counter() - start) * 1000, 1),
        },
    }
    return Recognition(content=content, theme=theme, report=report, warnings=warnings)


def _split_header(paragraphs: List[Paragraph]) -> Tuple[Optional[Paragraph], List[Paragraph], List[Paragraph]]:
    """前几行里找姓名和联系方式；剩下的才开始分章节。"""
    name_para = None
    best_size = -1.0
    for para in paragraphs[:4]:
        text = clean_text(para.text)
        if not text or len(text) > 20:
            continue
        if PHONE_RE.search(text) or EMAIL_RE.search(text) or "@" in text:
            continue
        size = para.size or 0
        if size > best_size:
            best_size = size
            name_para = para
    if name_para is None:
        for para in paragraphs:
            if clean_text(para.text):
                name_para = para
                break

    contact_paras: List[Paragraph] = []
    rest: List[Paragraph] = []
    if name_para is None:
        return None, contact_paras, paragraphs

    seen_name = False
    for para in paragraphs:
        if para is name_para:
            seen_name = True
            continue
        if not seen_name:
            continue
        # 联系方式只可能紧跟在姓名后面；一旦出现别的段落就停止收集，
        # 否则正文里的「微信群」「电话沟通」会被误当成联系方式。
        if not rest and len(contact_paras) < 3 and _is_contact(para):
            contact_paras.append(para)
            continue
        rest.append(para)
    return name_para, contact_paras, rest


def _is_contact(p: Paragraph) -> bool:
    text = clean_text(p.text)
    if not text or len(text) > 120:
        return False
    if PHONE_RE.search(text) or EMAIL_RE.search(text):
        return True
    if any(word in text for word in ("求职意向", "可实习", "期望岗位", "意向岗位", "求职目标")):
        return True
    if re.search(r"(微信|电话|手机|邮箱|E-?mail|现居|所在地|籍贯|年龄|出生)", text, re.I):
        return True
    return False


def _body_size(rest: List[Paragraph], name_para: Optional[Paragraph]) -> float:
    items = [(p.size, p.char_count) for p in rest if p.size and not is_heading(p, p.size)]
    mode = weighted_mode(items)
    if mode:
        return max(7.0, min(14.0, mode))
    return 9.0


def _split_sections(rest: List[Paragraph], body: float,
                    warnings: List[str]) -> Tuple[List[Paragraph], List[Tuple[str, List[Paragraph]]]]:
    candidates = [p for p in rest if is_heading(p, body)]
    keyword_hits = [p for p in candidates
                    if any(clean_text(p.text).rstrip("：: ").startswith(w) for w in HEADING_WORDS)]
    if len(keyword_hits) >= 1:
        headings = keyword_hits
    elif len(candidates) >= 2:
        headings = candidates
    else:
        headings = []

    sections: List[Tuple[str, List[Paragraph]]] = []
    if not headings:
        if rest:
            warnings.append("没认出章节标题，整篇先放进一个「简历内容」板块；"
                            "可以在编辑页把标题改成真实章节名。")
            sections.append(("简历内容", list(rest)))
        return headings, sections

    heading_set = {id(para) for para in headings}
    current_title = None
    current: List[Paragraph] = []
    for para in rest:
        if id(para) in heading_set:
            if current_title is not None:
                sections.append((current_title, current))
            current_title = clean_heading(para.text) or "其他"
            current = []
        elif current_title is not None:
            current.append(para)
        else:
            # 标题之前的内容（很少见），并进第一个板块
            current.append(para)
    if current_title is not None:
        sections.append((current_title, current))
    return headings, sections


def _build_content(document: Document, name_para: Optional[Paragraph],
                   contact_paras: List[Paragraph], headings: List[Paragraph],
                   section_list: List[Tuple[str, List[Paragraph]]],
                   body: float, warnings: List[str]) -> Tuple[Dict, Dict]:
    samples = {"name": [], "contact": [], "heading": [], "entry": [],
               "bullet": [], "para": [], "subline": []}

    editor = _editor_fields(name_para, contact_paras, warnings)
    sections = []
    used_keys: Dict[str, int] = {}

    for index, (title, paragraphs) in enumerate(section_list):
        key = section_key(title)
        if key == "custom":
            key = "sec%d" % (index + 1)
        if key in used_keys:
            used_keys[key] += 1
            key = "%s%d" % (key, used_keys[key])
        else:
            used_keys[key] = 1

        blocks: List[Dict] = []
        prev_block: Optional[Dict] = None
        for para in paragraphs:
            text = clean_text(para.text)
            if not text:
                continue

            if DATE_ONLY.match(text) and prev_block and prev_block.get("type") == "entry" \
                    and not prev_block.get("date"):
                prev_block["date"] = normalize_date(text)
                samples.setdefault("entry_date", []).append(para)
                continue

            if is_bullet(para):
                runs = _runs_for(para, strip_glyph=True)
                block = {"type": "bullet", "runs": runs}
                samples["bullet"].append(para)
            else:
                entry = _as_entry(para, body, document.text_width, title)
                if entry:
                    block = entry
                    samples["entry"].append(para)
                elif prev_block and prev_block.get("type") == "entry" \
                        and _looks_subline(para, body):
                    block = {"type": "subline", "text": text}
                    samples["subline"].append(para)
                else:
                    block = {"type": "para", "size": round(para.size or body, 1),
                             "runs": _runs_for(para)}
                    samples["para"].append(para)
            blocks.append(block)
            prev_block = block

        if blocks:
            sections.append({"key": key, "title": title, "blocks": blocks})
        else:
            # 只有标题的空模板也保留，方便用户直接填
            sections.append({"key": key, "title": title, "blocks": []})

    content = {"_editor": editor, "sections": sections}
    samples["name"].append(name_para)
    samples["contact"].extend(contact_paras)
    samples["heading"].extend(headings)
    return content, samples


def _editor_fields(name_para: Optional[Paragraph], contacts: List[Paragraph],
                   warnings: List[str]) -> Dict:
    raw = "\n".join(clean_text(p.text) for p in contacts)
    name_line = clean_text(name_para.text) if name_para else ""
    name = name_line
    if re.search(r"[｜|]", name_line):
        head = re.split(r"[｜|]", name_line)[0].strip()
        if 1 <= len(head) <= 20:
            name = head
    # 占位符（【电话】、{{姓名}}）不能当成真实字段，先摘掉再解析
    parsed = PLACEHOLDER_RE.sub("", raw)
    phone = PHONE_RE.search(parsed)
    email = EMAIL_RE.search(parsed)
    intent = re.search(
        r"(?:求职意向|意向岗位|期望岗位|求职目标)\s*[:：]?\s*"
        r"([^\s|｜·\t\n:：][^|｜·\t\n]{1,23})", parsed)
    availability = re.search(
        r"(?:可实习|实习时长|到岗时间)\s*[:：]?\s*"
        r"([^\s|｜·\t\n:：][^|｜·\t\n]{1,23})", parsed)
    city_match = re.search(r"(?:现居|所在地|城市)\s*[:：]?\s*([\u4e00-\u9fa5]{2,6})", parsed)
    city_text = clean_text(city_match.group(1)) if city_match else ""
    if not city_text:
        for known in ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京",
                      "西安", "苏州", "长沙", "厦门", "合肥", "郑州", "天津", "重庆"):
            if known in parsed:
                city_text = known
                break

    extras: List[str] = []
    for segment in re.split(r"[|｜·•\t]", parsed):
        segment = clean_text(segment)
        if not segment or len(segment) > 40:
            continue
        if PHONE_RE.search(segment) or EMAIL_RE.search(segment):
            continue
        if re.search(r"求职意向|意向岗位|期望岗位|求职目标|可实习|实习时长|到岗时间|现居|所在地", segment):
            continue
        if any(segment == known for known in ("北京", "上海", "广州", "深圳", "杭州", "成都",
                                              "武汉", "南京", "西安", "苏州", "长沙", "厦门")):
            continue
        extras.append(segment)
    extras = extras[:4]

    placeholders = PLACEHOLDER_RE.findall("\n".join([name, raw]))
    if placeholders:
        warnings.append("识别到 %d 处占位符（如 %s），对应字段已留空，直接填就行。"
                        % (len(placeholders), placeholders[0]))
        name = "" if PLACEHOLDER_RE.search(name) else name

    intent_text = clean_text(intent.group(1)).strip("：: 　") if intent else ""
    if not intent_text and re.search(r"[｜|]", name_line):
        tail = clean_text(re.split(r"[｜|]", name_line)[-1]).strip("：: 　")
        if 2 <= len(tail) <= 24 and not re.search(r"\d", tail):
            intent_text = tail
    availability_text = ""
    if availability:
        whole = clean_text(availability.group(0)).strip("：: 　")
        availability_text = clean_text(availability.group(1)).strip("：: 　") \
            if ("：" in whole or ":" in whole) else whole

    basename = name or "我的简历"
    if intent_text:
        basename = "%s-%s" % (basename, intent_text)
    return {
        "name": name,
        "phone": phone.group(0) if phone else "",
        "email": email.group(0) if email else "",
        "city": city_text,
        "intent": intent_text,
        "availability": availability_text,
        "extras": extras,
        "basename": basename,
    }


def _looks_subline(p: Paragraph, body: float) -> bool:
    text = clean_text(p.text)
    if not text or len(text) > 50:
        return False
    if DATE_RANGE.search(text) or is_bullet(p):
        return False
    size = p.size or body
    return size < body - 0.2 or (p.color and p.color.upper() in ("#666666", "#777777", "#888888",
                                                                "#999999", "#1A1A1A", "#333333"))


def _as_entry(p: Paragraph, body: float, text_width: float,
              section_title: str = "") -> Optional[Dict]:
    raw = p.raw_text.replace("\u00a0", " ").strip()
    if not raw or len(raw) > 120:
        return None

    segments = [clean_text(part) for part in raw.split("\t")]
    segments = [part for part in segments if part]
    date = None
    if segments and DATE_ONLY.match(segments[-1]):
        date = normalize_date(segments.pop())
    if date is None:
        date, rest = extract_date(raw)
        if date:
            segments = [clean_text(part) for part in rest.split("\t")]
            segments = [part for part in segments if part]
    if not segments:
        return None

    if len(segments) == 1:
        text = segments[0]
        # 没有制表符时，用「两个以上空格 / 竖线」当分隔
        pieces = [clean_text(x) for x in re.split(r"\s{2,}|[｜|]", text) if clean_text(x)]
        if len(pieces) >= 2:
            segments = pieces
        else:
            segments = [text]

    # 纯文本或 PDF 里的「学校 专业 学历」常常只有一个空格，按板块再试一次
    if len(segments) == 1 and not date and section_title:
        if any(word in section_title for word in ("教育", "实习", "工作", "项目", "校园", "实践")):
            text = segments[0]
            if len(text) <= 42 and not re.search(r"[，。；;、!！?？]", text):
                pieces = [clean_text(x) for x in re.split(r"\s+", text) if clean_text(x)]
                if 2 <= len(pieces) <= 4 and all(len(piece) <= 16 for piece in pieces):
                    segments = pieces

    has_date = date is not None
    multi = len(segments) >= 2
    bold_title = p.bold or (p.runs and p.runs[0].bold)
    if not has_date and not multi:
        return None
    if not has_date and multi and not bold_title and (p.size or body) < body - 0.5:
        return None
    if len(raw) > 100 and not has_date:
        return None

    title = segments[0]
    metas = segments[1:]
    block: Dict = {"type": "entry", "title": title, "date": date}

    tab_positions = [round(pos, 1) for pos in (p.tab_positions or [])
                     if 5 < pos < text_width - 8]
    tab_positions = sorted(set(tab_positions))

    if len(metas) >= 2 and len(tab_positions) >= len(metas):
        block["meta_segments"] = metas
        block["meta_tabs"] = tab_positions[:len(metas)]
    elif metas:
        block["meta"] = " / ".join(metas)
        block["meta_tab"] = tab_positions[0] if tab_positions else \
            _guess_meta_tab(title, p.size or body)

    if not has_date and not metas:
        # 只有一行加粗标题，按条目处理（常见于项目名）
        block["meta"] = ""
    return block


def _guess_meta_tab(title: str, size: float) -> float:
    width = text_width_pt(title, size)
    return round(min(180.0, max(36.0, width + 14.0)), 1)


def _runs_for(p: Paragraph, strip_glyph: bool = False) -> List[List]:
    runs = []
    for run in p.runs:
        text = run.text
        if strip_glyph and not runs:
            text = text.lstrip("\ufeff")
            stripped = text.lstrip(BULLET_CHARS + " \t")
            if stripped != text:
                text = stripped.lstrip(" \t")
        if not text:
            continue
        runs.append([text.replace("\u00a0", " "), 1 if run.bold else 0])

    merged: List[List] = []
    for text, bold in runs:
        if merged and merged[-1][1] == bold:
            merged[-1][0] += text
        else:
            merged.append([text, bold])
    if merged:
        merged[0][0] = merged[0][0].lstrip()
        merged[-1][0] = merged[-1][0].rstrip()
    merged = [pair for pair in merged if pair[0]]

    if not merged:
        return [["", 0]]

    # 全靠纯文本时，把「标签：」这一段补成加粗
    if all(not bold for _, bold in merged) and len(merged) == 1:
        text = merged[0][0]
        colon = text.find("：")
        if 0 < colon <= 14:
            return [[text[:colon + 1], 1], [text[colon + 1:].lstrip(), 0]]
    return merged


def _counts(content: Dict) -> Dict:
    counts = {"sections": len(content.get("sections", [])), "entries": 0, "bullets": 0,
              "paras": 0, "sublines": 0, "chars": 0}
    for section in content.get("sections", []):
        for block in section.get("blocks", []):
            kind = block.get("type")
            if kind == "entry":
                counts["entries"] += 1
                counts["chars"] += len(block.get("title", ""))
                counts["chars"] += len(block.get("meta", "") or "")
                counts["chars"] += sum(len(seg) for seg in (block.get("meta_segments") or []))
            elif kind == "bullet":
                counts["bullets"] += 1
                counts["chars"] += sum(len(t) for t, _ in block.get("runs", []))
            elif kind == "para":
                counts["paras"] += 1
                counts["chars"] += sum(len(t) for t, _ in block.get("runs", []))
            elif kind == "subline":
                counts["sublines"] += 1
                counts["chars"] += len(block.get("text", ""))
    return counts


# ---------------------------------------------------------------- 版式

def _build_theme(document: Document, samples: Dict, body: float,
                 filename: Optional[str], content: Dict) -> Dict:
    theme: Dict = {}

    name_paras = [p for p in samples.get("name", []) if p]
    contact_paras = samples.get("contact", [])
    headings = samples.get("heading", [])
    entries = samples.get("entry", [])
    bullets = samples.get("bullet", [])
    paras = samples.get("para", [])

    name_size = max([p.size for p in name_paras if p.size] or [21.5])
    section_size = median([p.size for p in headings if p.size], 12.5)
    entry_sizes = []
    for para in entries:
        bold_sizes = [run.size for run in para.runs if run.bold and run.size]
        if bold_sizes:
            entry_sizes.append(median(bold_sizes, para.size or body))
    entry_size = median(entry_sizes, body + 2.0)
    contact_size = median([p.size for p in contact_paras if p.size], body)
    body_lg = body
    for para in paras:
        if para.size and body + 0.2 < para.size <= body + 0.9:
            body_lg = max(body_lg, round(para.size, 1))
    if body_lg <= body:
        body_lg = round(body + 0.5, 1)

    ink = weighted_mode([(p.color, p.char_count) for p in paras if p.color]) or "#000000"
    soft = _soft_color([p.color for p in entries + paras if p.color], ink)
    bar = _bar_color([p.color for p in contact_paras if p.color]) or "#9A9A9A"

    fonts = [p.font for p in (name_paras + contact_paras + entries + bullets + paras) if p.font]
    cjk = _most_common(fonts) or "Noto Sans SC"
    latin = "Arial"

    margins = _margins(document)
    spacing = _spacing(name_paras, contact_paras, headings, entries, bullets, paras,
                       name_size, section_size, entry_size, body)
    logo, photo = _pick_images(document)

    meta_tabs = [block["meta_tab"] for section in content.get("sections", [])
                 for block in section.get("blocks", [])
                 if block.get("type") == "entry" and block.get("meta_tab")]
    theme.update({
        "name": "识别版式 · %s" % (filename or document.filename or document.source),
        "source": document.source,
        "page": {
            "width": round(document.page_width, 2),
            "height": round(document.page_height, 2),
            "margin_left": margins[0],
            "margin_right": margins[1],
            "margin_top": margins[2],
            "margin_bottom": margins[3],
        },
        "fonts": {"cjk": cjk, "latin": latin},
        "sizes": {
            "name": round(name_size, 1),
            "section": round(section_size, 1),
            "entry": round(entry_size, 1),
            "body": round(body, 1),
            "body_lg": round(body_lg, 1),
            "contact": round(contact_size, 1),
        },
        "colors": {"ink": ink, "soft": soft, "bar": bar},
        "spacing": spacing,
        "indent": {
            "bullet_left": median([p.left_indent for p in bullets if p.left_indent >= 2], 14.0),
            "bullet_hanging": median([-p.first_line_indent for p in bullets
                                      if p.first_line_indent < -1], 9.0),
        },
        "header": {"center": True, "logo": logo, "photo": photo},
        "meta_tab": median(meta_tabs, 120.0),
    })
    return theme_mod.normalize(theme)


def _spacing(name_paras, contact_paras, headings, entries, bullets, paras,
             name_size, section_size, entry_size, body) -> Dict:
    def line_of(items, fallback):
        values = [p.line_spacing for p in items if p.line_spacing]
        return median(values, fallback)

    first_contact = contact_paras[0] if contact_paras else None
    second_contact = contact_paras[1] if len(contact_paras) > 1 else None
    return {
        "name_line": line_of(name_paras, round(name_size * 1.2, 2)),
        "name_sb": median([p.space_before for p in name_paras], 0.0),
        "contact_line": line_of(contact_paras, round(body * 1.33, 2)),
        "c1_sb": round(first_contact.space_before, 2) if first_contact else 4.0,
        "c2_sb": round(second_contact.space_before, 2) if second_contact else 6.0,
        "section_sb": median([p.space_before for p in headings], 12.0),
        "section_line": line_of(headings, round(section_size * 1.45, 2)),
        "entry_sb": median([p.space_before for p in entries], 2.0),
        "entry_line": line_of(entries, round(entry_size * 1.36, 2)),
        "para_sb": median([p.space_before for p in paras], 0.0),
        "para_line": line_of(paras, round(body * 1.33, 2)),
        "bullet_line": line_of(bullets, round(body * 1.33, 2)),
        "sub_sb": 0.0,
    }


def _margins(document: Document) -> Tuple[float, float, float, float]:
    left = _clamp(document.margin_left, 15.0, 120.0, 41.5)
    right = _clamp(document.margin_right, 15.0, 120.0, 39.8)
    top = _clamp(document.margin_top, 12.0, 120.0, 40.0)
    bottom = _clamp(document.margin_bottom, 12.0, 120.0, 36.0)
    return round(left, 1), round(right, 1), round(top, 1), round(bottom, 1)


def _clamp(value, low, high, fallback):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    if value <= 1:
        return fallback
    return max(low, min(high, value))


def _soft_color(colors: List[str], ink: str) -> str:
    counts: Dict[str, int] = {}
    for color in colors:
        if not color or color.upper() == ink.upper():
            continue
        counts[color.upper()] = counts.get(color.upper(), 0) + 1
    if not counts:
        return "#1A1A1A"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _bar_color(colors: List[str]) -> Optional[str]:
    counts: Dict[str, int] = {}
    for color in colors:
        if not color:
            continue
        try:
            r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        except (ValueError, IndexError):
            continue
        if abs(r - g) < 8 and abs(g - b) < 8 and 0x60 <= r <= 0xC8:
            counts[color.upper()] = counts.get(color.upper(), 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _most_common(values: List[str]) -> Optional[str]:
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _pick_images(document: Document) -> Tuple[Optional[Dict], Optional[Dict]]:
    logo = None
    photo = None
    logo_score = 0.0
    photo_score = 0.0
    for para in document.paragraphs:
        for image in para.images:
            if not image.anchored:
                continue
            item = {"file": image.path, "x": image.x, "y": image.y,
                    "w": image.w, "h": image.h}
            is_photo = (image.w <= 130 and image.aspect >= 1.05
                        and image.x > document.page_width * 0.55)
            is_logo = (image.w >= 30 and image.aspect < 1.1
                       and image.y <= document.page_height * 0.25
                       and image.x < document.page_width * 0.45)
            if is_photo and image.w * image.h > photo_score:
                photo, photo_score = item, image.w * image.h
            elif is_logo and image.w * image.h > logo_score:
                logo, logo_score = item, image.w * image.h
    return logo, photo


# ---------------------------------------------------------------- 可信度

def _confidence(document: Document, content: Dict, samples: Dict,
                theme: Dict, warnings: List[str]) -> Tuple[float, List[str]]:
    score = 0.95
    notes: List[str] = []
    editor = content.get("_editor") or {}
    sections = content.get("sections") or []
    counts = _counts(content)

    if document.source == "docx":
        notes.append("docx 带完整排版信息，版式按原文件还原。")
    elif document.source == "pdf":
        score -= 0.12
        notes.append("PDF 没有段落和行距信息，按文字坐标归并；建议同时上传 docx 版。")
    else:
        score -= 0.3
        notes.append("纯文本没有版式信息，先套用默认排版，字号行距可在编辑页改。")

    if not editor.get("name"):
        score -= 0.2
        notes.append("没认到姓名，已留空。")
    if len(sections) < 2:
        score -= 0.12
    if counts["entries"] == 0:
        score -= 0.15
        notes.append("没认到带日期的条目，时间线可能需要手动补。")
    for _ in warnings:
        score -= 0.02
    if document.has_tables:
        score -= 0.08
    if theme["sizes"]["body"] <= 0:
        score -= 0.1
    return round(max(0.25, min(0.97, score)), 2), notes


def _confidence_label(score: float) -> str:
    if score >= 0.8:
        return "高"
    if score >= 0.6:
        return "中"
    return "低"
