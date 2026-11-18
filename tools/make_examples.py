# -*- coding: utf-8 -*-
"""生成示例文件：examples/sample_resume.docx / .pdf 与 examples/blank_template.docx。

    python tools/make_examples.py

示例里的「张三」是虚构人物，用来演示识别链路，不是真实简历。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from resume_agent.build_docx import build  # noqa: E402
from resume_agent.theme import normalize  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")
ASSETS = os.path.join(ROOT, "assets")

TEMPLATE_CONTENT = {
    "_editor": {
        "name": "【姓名】",
        "phone": "【电话】",
        "email": "【邮箱】",
        "city": "【城市】",
        "intent": "【求职意向】",
        "availability": "【可实习时长】",
        "extras": [],
        "basename": "空白模板-示例",
    },
    "sections": [
        {"key": "edu", "title": "教育经历", "blocks": [
            {"type": "entry", "title": "【学校名称】",
             "meta_segments": ["【专业】", "【学历】"], "meta_tabs": [62.0, 132.0],
             "date": "【起止时间】"},
            {"type": "para", "size": 10.5, "runs": [
                ["相关课程：", 1], ["【3—5 门与目标岗位相关的课程】", 0]]},
        ]},
        {"key": "intern", "title": "实习经历", "blocks": [
            {"type": "entry", "title": "【公司名称】", "meta": "【岗位】",
             "meta_tab": 56.0, "date": "【起止时间】"},
            {"type": "bullet", "runs": [
                ["【一句话概括这条经历】：", 1],
                ["负责什么、怎么做的、拿到什么数据结果。", 0]]},
        ]},
        {"key": "proj", "title": "项目经历", "blocks": [
            {"type": "entry", "title": "【项目名称】", "meta": "【你的角色】",
             "meta_tab": 96.0, "date": "【起止时间】"},
            {"type": "bullet", "runs": [
                ["【解决的问题】：", 1], ["做了什么、结果如何。", 0]]},
        ]},
        {"key": "skill", "title": "专业技能", "blocks": [
            {"type": "para", "size": 10.5, "runs": [
                ["【技能分类】：", 1], ["【具体工具 + 熟练程度】", 0]]},
        ]},
        {"key": "award", "title": "荣誉奖项", "blocks": [
            {"type": "para", "size": 10.5, "runs": [["【奖项名称 + 年份】", 0]]},
        ]},
    ],
}


def main() -> int:
    with open(os.path.join(EXAMPLES, "sample_content.json"), encoding="utf-8") as fh:
        content = json.load(fh)
    with open(os.path.join(EXAMPLES, "sample_theme.json"), encoding="utf-8") as fh:
        theme = normalize(json.load(fh))

    sample_docx = os.path.join(EXAMPLES, "sample_resume.docx")
    build(content, theme, sample_docx, ASSETS)
    print("saved", sample_docx)

    template_docx = os.path.join(EXAMPLES, "blank_template.docx")
    build(TEMPLATE_CONTENT, theme, template_docx, ASSETS)
    print("saved", template_docx)
    with open(os.path.join(EXAMPLES, "blank_template.json"), "w", encoding="utf-8") as fh:
        json.dump(TEMPLATE_CONTENT, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
