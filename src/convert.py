#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
苹果字典转换器 —— 核心转换引擎

把一个词典源文件转换成 macOS 原生词典格式（.dictionary）：

  子命令
    wiktionary <xxx.bz2> <输出目录> [--name 名称]
        中文维基词典全量 dump（.bz2）-> StarDict -> AppleDict
    stardict   <xxx.ifo> <输出目录> [--name 名称]
        StarDict（.ifo/.idx/.dict） -> AppleDict
    csv        <xxx.csv> <输出目录> <书名> [--name 名称]
        dictkit 系 CSV（古代汉语词典/古汉语常用字字典等） -> StarDict -> AppleDict

  公共选项
    --name 名称    指定产出的 .dictionary 名称（默认按输入推导）
    --install      转换完成后复制到 ~/Library/Dictionaries/
    --keep-src     保留中间的 StarDict 文件

资源定位：脚本目录的上级为 Resources，内含 python3.13（含 PyGlossary）、ddk（Apple DDK）。
也可用环境变量覆盖：CONVERTER_PYTHON、DICT_BUILD_TOOL_BIN。
"""

import os
import sys
import shutil
import subprocess
import argparse
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES = os.path.dirname(SCRIPT_DIR)
DDK = os.environ.get("DICT_BUILD_TOOL_BIN") or os.path.join(RESOURCES, "ddk")
PY = os.environ.get("CONVERTER_PYTHON") or os.path.join(RESOURCES, "python3.13", "bin", "python3")
FIX = os.path.join(SCRIPT_DIR, "fix_dict_plist.sh")

sys.path.insert(0, SCRIPT_DIR)
import wiktionary_parse  # noqa: E402
import stardict_write    # noqa: E402


def log(*a):
    print(*a, flush=True)


def run_pyglossary(ifo, project):
    """调用打包内 PyGlossary，把 StarDict 转成 AppleDict 工程。"""
    if not os.path.isfile(PY):
        # 退化到系统 python3（需自备 pyglossary）
        py = "python3"
        cmd = [py, "-c", "from pyglossary.ui.main import main; main()",
               ifo, project, "--read-format", "Stardict", "--write-format", "AppleDict"]
    else:
        cmd = [PY, "-c", "from pyglossary.ui.main import main; main()",
               ifo, project, "--read-format", "Stardict", "--write-format", "AppleDict"]
    log(">>> [1] PyGlossary: %s -> %s" % (ifo, project))
    env = dict(os.environ)
    env["DICT_BUILD_TOOL_BIN"] = DDK
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        log("PyGlossary 失败：\n" + (r.stderr or r.stdout)[-1500:])
        sys.exit(1)
    log((r.stdout or "")[-300:])


def write_makefile(project, name):
    """写对接 Apple DDK 的 Makefile（覆盖 PyGlossary 默认指向 /Applications/... 的版本）。"""
    mk = (
        "DICT_NAME\t\t=\t%s\n" % name +
        "DICT_SRC_PATH\t\t=\t%s.xml\n" % name +
        "CSS_PATH\t\t=\t%s.css\n" % name +
        "PLIST_PATH\t\t=\t%s.plist\n" % name +
        "\n"
        "DICT_BUILD_OPTS\t\t=\n"
        "\n"
        "DICT_BUILD_TOOL_BIN\t=\t%s\n" % DDK +
        "export DICT_BUILD_TOOL_BIN\n"
        "\n"
        "DICT_DEV_KIT_OBJ_DIR\t=\t./objects\n"
        "export DICT_DEV_KIT_OBJ_DIR\n"
        "\n"
        "DESTINATION_FOLDER\t=\t~/Library/Dictionaries\n"
        "RM\t\t\t=\t/bin/rm\n"
        "\n"
        "all:\n"
        "\t\"$(DICT_BUILD_TOOL_BIN)/build_dict.sh\" $(DICT_BUILD_OPTS) $(DICT_NAME) $(DICT_SRC_PATH) $(CSS_PATH) $(PLIST_PATH)\n"
        "\t@echo \"Done.\"\n"
        "\n"
        "install:\n"
        "\tmkdir -p $(DESTINATION_FOLDER)\n"
        "\tditto --noextattr --norsrc $(DICT_DEV_KIT_OBJ_DIR)/$(DICT_NAME).dictionary $(DESTINATION_FOLDER)/$(DICT_NAME).dictionary\n"
        "\ttouch $(DESTINATION_FOLDER)\n"
        "\n"
        "clean:\n"
        "\t$(RM) -rf $(DICT_DEV_KIT_OBJ_DIR)\n"
    )
    with open(os.path.join(project, "Makefile"), "w", encoding="utf-8") as f:
        f.write(mk)


def compile_stardict(ifo, outdir, name, install, keep_src=False):
    """StarDict -> AppleDict 工程 -> DDK 编译 .dictionary -> 修 plist -> 交付。"""
    os.makedirs(outdir, exist_ok=True)
    project = os.path.join(outdir, name)
    if os.path.exists(project):
        shutil.rmtree(project)
    os.makedirs(project)

    run_pyglossary(ifo, project)
    write_makefile(project, name)

    log(">>> [2] make 编译（DDK=%s）" % DDK)
    if not shutil.which("make"):
        log("错误：系统缺少 make（请安装 Xcode Command Line Tools: xcode-select --install）")
        sys.exit(1)
    r = subprocess.run(["make"], cwd=project, capture_output=True, text=True)
    if r.returncode != 0:
        log("make 失败：\n" + (r.stderr or r.stdout)[-2000:])
        sys.exit(1)

    bundle = os.path.join(project, "objects", name + ".dictionary")
    if not os.path.isdir(bundle):
        log("未找到编译产物: %s" % bundle)
        sys.exit(1)

    log(">>> [3] 修正 Info.plist")
    if os.path.isfile(FIX):
        subprocess.run(["bash", FIX, bundle], check=True)
    else:
        log("警告：未找到 fix_dict_plist.sh，跳过 plist 修正")

    outbundle = os.path.join(outdir, name + ".dictionary")
    if os.path.exists(outbundle):
        shutil.rmtree(outbundle)
    shutil.copytree(bundle, outbundle)
    log(">>> 完成: %s" % outbundle)

    if install:
        dest = os.path.expanduser("~/Library/Dictionaries")
        os.makedirs(dest, exist_ok=True)
        target = os.path.join(dest, name + ".dictionary")
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(bundle, target)
        log(">>> 已安装到: %s" % target)

    # ---- 清理中间工程目录（AppleDict 编译工程，非最终产物）----
    # 最终可用产物是 outbundle（*.dictionary）；project 只是 PyGlossary+DDK 的构建中间目录，
    # 它会和最终 *.dictionary 并列出现在输出目录里，容易分不清哪个该用。
    # 默认用删除命令清掉它，只保留真正的成品；--keep-src 时保留以供调试。
    if not keep_src and os.path.isdir(project) and os.path.abspath(project) != os.path.abspath(outbundle):
        log(">>> 清理中间工程目录（构建用，非成品）: %s" % project)
        subprocess.run(["/bin/rm", "-rf", project], check=False)

    log("=== DONE: 最终产物（请使用这个 *.dictionary）: %s ===" % outbundle)
    return outbundle


def cmd_wiktionary(args):
    name = args.name or "维基文言文词典（古汉语→现代汉语）"
    stardict_dir = os.path.join(args.outdir, name + "_stardict")
    ifo = wiktionary_parse.parse_wiktionary_bz2(args.bz2, stardict_dir, name)
    out = compile_stardict(ifo, args.outdir, name, args.install, args.keep_src)
    if not args.keep_src and os.path.isdir(stardict_dir):
        shutil.rmtree(stardict_dir, ignore_errors=True)
    return out


def cmd_stardict(args):
    name = args.name or os.path.splitext(os.path.basename(args.ifo))[0]
    return compile_stardict(args.ifo, args.outdir, name, args.install, args.keep_src)


def cmd_csv(args):
    name = args.name or args.bookname
    stardict_dir = os.path.join(args.outdir, name + "_stardict")
    ifo = stardict_write.csv_to_stardict(args.csv, stardict_dir, args.bookname)
    out = compile_stardict(ifo, args.outdir, name, args.install, args.keep_src)
    if not args.keep_src and os.path.isdir(stardict_dir):
        shutil.rmtree(stardict_dir, ignore_errors=True)
    return out


def main():
    p = argparse.ArgumentParser(description="苹果字典转换器：源文件 -> macOS .dictionary")
    sub = p.add_subparsers(dest="cmd", required=True)

    pw = sub.add_parser("wiktionary", help="中文维基词典 .bz2 dump -> AppleDict")
    pw.add_argument("bz2", help="zhwiktionary-*-pages-articles.xml.bz2")
    pw.add_argument("outdir", help="输出目录")
    pw.add_argument("--name", default=None)
    pw.add_argument("--install", action="store_true")
    pw.add_argument("--keep-src", action="store_true")
    pw.set_defaults(func=cmd_wiktionary)

    ps = sub.add_parser("stardict", help="StarDict(.ifo) -> AppleDict")
    ps.add_argument("ifo", help="StarDict 的 .ifo 文件")
    ps.add_argument("outdir", help="输出目录")
    ps.add_argument("--name", default=None)
    ps.add_argument("--install", action="store_true")
    ps.add_argument("--keep-src", action="store_true")
    ps.set_defaults(func=cmd_stardict)

    pc = sub.add_parser("csv", help="dictkit CSV -> AppleDict")
    pc.add_argument("csv", help="CSV 文件（含 字词索引/释义 等列）")
    pc.add_argument("outdir", help="输出目录")
    pc.add_argument("bookname", help="词典书名（写入 .ifo）")
    pc.add_argument("--name", default=None)
    pc.add_argument("--install", action="store_true")
    pc.add_argument("--keep-src", action="store_true")
    pc.set_defaults(func=cmd_csv)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
