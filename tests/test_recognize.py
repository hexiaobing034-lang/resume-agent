# -*- coding: utf-8 -*-
"""识别链路的回归测试。

    python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from resume_agent.build_docx import build  # noqa: E402
from resume_agent.docx_reader import read_docx  # noqa: E402
from resume_agent.layout import estimate_height  # noqa: E402
from resume_agent.pipeline import analyze_text  # noqa: E402
from resume_agent.recognize import recognize  # noqa: E402
from resume_agent.text_reader import read_text  # noqa: E402
from resume_agent import theme as theme_mod  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")


def load_json(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        return json.load(fh)


def has_pymupdf() -> bool:
    try:
        import pymupdf  # noqa: F401
        return True
    except ImportError:
        return False


def make_pdf(pdf_path: str) -> str:
    """造一个「只有 PDF、没有段落信息」的简历，坐标对齐真实排版。"""
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page(width=595.28, height=841.89)

    def put(x, y, text, size=9.5):
        page.insert_text((x, y), text, fontsize=size, fontname="china-s")

    put(255, 62, "张三", 21.5)
    put(150, 88, "13800000000 | zhangsan@example.com | 深圳", 9)
    put(200, 106, "求职意向：产品运营实习生", 9)

    put(41.5, 142, "教育经历", 12.5)
    put(41.5, 166, "深圳大学", 11)
    put(105, 168, "网络与新媒体", 9)
    put(495, 168, "2022.09—2026.06", 9)
    put(41.5, 184, "相关课程：传播学、数据分析、视觉设计。", 9)

    put(41.5, 220, "实习经历", 12.5)
    put(41.5, 244, "星野科技", 11)
    put(100, 246, "广告设计实习生", 9)
    put(495, 246, "2025.06—2025.11", 9)
    put(46, 264, "• 数据复盘：整理素材点击率与转化数据，输出周报。", 9)
    put(46, 278, "• 素材生产：完成 120 条信息流素材，配合 A/B 测试。", 9)

    put(41.5, 314, "专业技能", 12.5)
    put(41.5, 338, "数据分析：熟悉 Excel 透视表与基础 SQL。", 9)

    pdf.save(pdf_path)
    pdf.close()
    return pdf_path


class RoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="resume-agent-test-")
        cls.content = load_json("sample_content.json")
        cls.theme = theme_mod.normalize(load_json("sample_theme.json"))
        cls.docx = os.path.join(cls.tmp, "sample.docx")
        cls.assets = os.path.join(cls.tmp, "assets")
        build(cls.content, cls.theme, cls.docx, cls.assets)
        cls.document = read_docx(cls.docx, cls.assets)
        cls.result = recognize(cls.document, "sample.docx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_sections_and_entries_survive(self):
        self.assertEqual([s["key"] for s in self.result.content["sections"]],
                         ["edu", "intern", "proj", "skill", "award"])
        counts = self.result.report["counts"]
        self.assertEqual(counts["sections"], 5)
        self.assertEqual(counts["entries"], 5)
        self.assertEqual(counts["bullets"], 8)

    def test_entry_date_and_meta(self):
        intern = self.result.content["sections"][1]
        first = intern["blocks"][0]
        self.assertEqual(first["title"], "星野科技")
        self.assertEqual(first["meta"], "广告设计实习生")
        self.assertEqual(first["date"], "2025.06—2025.11")
        self.assertAlmostEqual(first["meta_tab"], 56.0, places=1)

    def test_edu_two_meta_columns(self):
        edu = self.result.content["sections"][0]["blocks"][0]
        self.assertEqual(edu["meta_segments"], ["网络与新媒体", "本科"])
        self.assertEqual(edu["meta_tabs"], [62.0, 132.0])

    def test_bold_label_split(self):
        bullet = self.result.content["sections"][1]["blocks"][1]
        self.assertEqual(bullet["runs"][0], ["数据复盘：", 1])
        self.assertEqual(bullet["runs"][1][1], 0)

    def test_theme_recovered(self):
        theme = self.result.theme
        self.assertEqual(theme["fonts"]["cjk"], "Microsoft YaHei")
        self.assertAlmostEqual(theme["sizes"]["body"], 10.5, places=1)
        self.assertAlmostEqual(theme["sizes"]["name"], 24.0, places=1)
        self.assertAlmostEqual(theme["sizes"]["section"], 14.0, places=1)
        self.assertAlmostEqual(theme["page"]["margin_left"], 48.0, places=1)
        self.assertAlmostEqual(theme["spacing"]["para_line"], 15.0, places=1)

    def test_header_fields(self):
        editor = self.result.content["_editor"]
        self.assertEqual(editor["name"], "张三")
        self.assertEqual(editor["phone"], "13800000000")
        self.assertEqual(editor["email"], "zhangsan@example.com")
        self.assertEqual(editor["intent"], "产品运营实习生")
        self.assertIn("可实习", editor["availability"])

    def test_one_page_estimate(self):
        fit = estimate_height(self.result.content, self.result.theme)
        self.assertTrue(fit["fits"])
        self.assertLess(fit["pages"], 1.0)


class TemplateTest(unittest.TestCase):
    def test_blank_template_gives_empty_fields_and_warning(self):
        tmp = tempfile.mkdtemp(prefix="resume-agent-template-")
        try:
            path = os.path.join(tmp, "blank.docx")
            theme = theme_mod.normalize(load_json("sample_theme.json"))
            build(load_json("blank_template.json"), theme, path, os.path.join(tmp, "assets"))
            result = recognize(read_docx(path, os.path.join(tmp, "assets")), "blank.docx")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        editor = result.content["_editor"]
        self.assertEqual(editor["name"], "")
        self.assertEqual(editor["phone"], "")
        self.assertEqual(editor["intent"], "")
        self.assertEqual(editor["availability"], "")
        self.assertEqual(len(result.content["sections"]), 5)
        self.assertTrue(any("占位符" in warning for warning in result.warnings))


class TextTest(unittest.TestCase):
    def test_paste_text_structure(self):
        text = """张三｜产品运营实习生
13800000000 | zhangsan@example.com | 深圳
教育经历
深圳大学 网络与新媒体 本科
2022.09—2026.06
• 相关课程：传播学、数据分析
实习经历
星野科技｜广告设计实习生
2025.06—2025.11
- 数据复盘：整理素材点击率，输出周报
- 素材生产：完成 120 条素材
"""
        result = analyze_text(text)
        titles = [section["title"] for section in result.content["sections"]]
        self.assertIn("教育经历", titles)
        self.assertIn("实习经历", titles)
        counts = result.report["counts"]
        self.assertGreaterEqual(counts["entries"], 2)
        self.assertEqual(counts["bullets"], 3)
        self.assertEqual(result.content["_editor"]["name"], "张三")

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            read_text("   ")


class ThemeTest(unittest.TestCase):
    def test_normalize_fills_defaults(self):
        theme = theme_mod.normalize({"sizes": {"body": 11.0}})
        self.assertEqual(theme["sizes"]["body"], 11.0)
        self.assertEqual(theme["fonts"]["cjk"], "Noto Sans SC")
        self.assertEqual(theme["page"]["margin_left"], 41.5)
        self.assertIsNone(theme["header"]["logo"])

    def test_string_numbers_are_coerced(self):
        theme = theme_mod.normalize({"sizes": {"body": "10"}, "meta_tab": "88"})
        self.assertEqual(theme["sizes"]["body"], 10.0)
        self.assertEqual(theme["meta_tab"], 88.0)


class PdfTest(unittest.TestCase):
    @unittest.skipUnless(has_pymupdf(), "PyMuPDF 未安装，跳过 PDF 测试")
    def test_synthetic_pdf(self):
        from resume_agent.pdf_reader import read_pdf

        tmp = tempfile.mkdtemp(prefix="resume-agent-pdf-")
        try:
            pdf_path = make_pdf(os.path.join(tmp, "sample.pdf"))
            document = read_pdf(pdf_path, os.path.join(tmp, "assets"))
            result = recognize(document, "sample.pdf")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(result.report["source"], "pdf")
        titles = [section["title"] for section in result.content["sections"]]
        self.assertIn("教育经历", titles)
        self.assertIn("实习经历", titles)
        counts = result.report["counts"]
        self.assertGreaterEqual(counts["entries"], 2)
        self.assertGreaterEqual(counts["bullets"], 2)
        self.assertEqual(result.content["_editor"]["name"], "张三")


if __name__ == "__main__":
    unittest.main()
