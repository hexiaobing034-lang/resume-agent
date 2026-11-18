# -*- coding: utf-8 -*-
"""命令行入口：python -m resume_agent.cli parse / build / serve。"""
import argparse
import json
import os
import sys

from . import __version__, theme as theme_mod
from .build_docx import build
from .pipeline import analyze_file
from .server import serve


def cmd_parse(args) -> int:
    out_dir = args.out or os.path.join("workspace", "parse")
    os.makedirs(out_dir, exist_ok=True)
    assets = os.path.join(out_dir, "assets")
    os.makedirs(assets, exist_ok=True)
    result = analyze_file(args.file, assets, os.path.basename(args.file))
    for name, payload in (("content.json", result.content),
                          ("theme.json", result.theme),
                          ("report.json", result.report)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    report = result.report
    print("识别完成：%s" % args.file)
    print("  来源: %s ｜ 可信度: %s (%s)" % (report["source"], report["confidence"],
                                             report["confidence_label"]))
    print("  板块 %d ｜ 条目 %d ｜ 要点 %d ｜ 段落 %d ｜ 约 %d 字"
          % (report["counts"]["sections"], report["counts"]["entries"],
             report["counts"]["bullets"], report["counts"]["paras"],
             report["counts"]["chars"]))
    print("  版式: %s ｜ 正文 %.1fpt ｜ 姓名 %.1fpt ｜ 边距 %s"
          % (report["theme"]["fonts"], report["theme"]["body"],
             report["theme"]["name"], report["theme"]["margins"]))
    print("  估算: %.2f 页（余量 %.0fpt）" % (report["fit"]["pages"],
                                              report["fit"]["remaining"]))
    for warning in report["warnings"]:
        print("  ! %s" % warning)
    print("  输出: %s" % os.path.abspath(out_dir))
    return 0


def cmd_build(args) -> int:
    with open(args.content, encoding="utf-8") as fh:
        content = json.load(fh)
    theme = {}
    if args.theme:
        with open(args.theme, encoding="utf-8") as fh:
            theme = json.load(fh)
    elif os.path.exists(os.path.join(os.path.dirname(args.content), "theme.json")):
        with open(os.path.join(os.path.dirname(args.content), "theme.json"),
                  encoding="utf-8") as fh:
            theme = json.load(fh)
    out = args.out
    assets = args.assets
    if not assets:
        candidate = os.path.join(os.path.dirname(os.path.abspath(args.content)), "assets")
        if os.path.isdir(candidate):
            assets = candidate
    build(content, theme_mod.normalize(theme), out, assets or None)
    print("已生成:", out)
    return 0


def cmd_serve(args) -> int:
    serve(args.port, open_browser=not args.no_browser)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="resume-agent",
                                     description="简历识别 / 编辑 / 导出")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command")

    parse = sub.add_parser("parse", help="解析一个 docx/pdf/txt，输出 content/theme/report")
    parse.add_argument("file")
    parse.add_argument("-o", "--out", help="输出目录（默认 workspace/parse）")
    parse.set_defaults(func=cmd_parse)

    build_cmd = sub.add_parser("build", help="按 content.json + theme.json 生成 docx")
    build_cmd.add_argument("content")
    build_cmd.add_argument("out")
    build_cmd.add_argument("--theme")
    build_cmd.add_argument("--assets")
    build_cmd.set_defaults(func=cmd_build)

    serve_cmd = sub.add_parser("serve", help="启动本地服务（默认命令）")
    serve_cmd.add_argument("--port", type=int, default=8765)
    serve_cmd.add_argument("--no-browser", action="store_true")
    serve_cmd.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
