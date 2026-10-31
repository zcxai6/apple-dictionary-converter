#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
苹果字典转换器 —— 核心转换引擎

把一个词典源文件转换成 macOS 原生词典格式（.dictionary）：

  子命令
    auto      <输入> <输出目录> [--name 名称]
        自动识别：压缩包先解到临时目录再看里面是什么；支持 .tar.bz2/.tar.gz/.zip/
        目录/.ifo/.csv/.txt/.tsv/.xml/维基 .bz2，自动选择对应路径
    wiktionary <xxx.bz2> <输出目录> [--name 名称]
        中文维基词典全量 dump（.bz2）-> StarDict -> AppleDict
    stardict  <xxx.ifo> <输出目录> [--name 名称]
        StarDict（.ifo/.idx/.dict，支持压缩版 .dict.dz/.idx.gz） -> AppleDict
    csv       <xxx.csv> <输出目录> <书名> [--name 名称]
        dictkit 系 CSV（古代汉语词典/古汉语常用字字典等） -> StarDict -> AppleDict
    tabfile   <xxx.txt|tsv> <输出目录> [--name 名称]
        通用「词条<TAB>释义」两列文本 -> StarDict -> AppleDict
    appledict <工程目录> <输出目录> [--name 名称]
        已有的 AppleDict 工程（xxx.xml + plist + css） -> 直接用 DDK 编译
    detect    <输入>
        只探测输入类型并输出 JSON（供图形界面预检，不做转换）

  公共选项
    --name 名称    指定产出的 .dictionary 名称（默认按输入推导）
    --install      转换完成后复制到 ~/Library/Dictionaries/
    --keep-src     保留中间的 StarDict 文件 / 编译工程

资源定位：脚本目录的上级为 Resources，内含 python3.13（含 PyGlossary）、ddk（Apple DDK）。
也可用环境变量覆盖：CONVERTER_PYTHON、DICT_BUILD_TOOL_BIN。
"""

import os
import sys
import bz2
import gzip
import json
import lzma
import html
import shutil
import hashlib
import subprocess
import argparse
import tarfile
import zipfile
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES = os.path.dirname(SCRIPT_DIR)


def _resolve_ddk():
    """定位 Apple DDK：① 环境变量 ② 应用内 Resources/ddk ③ 源码树同级的 ddk_bin。"""
    cands = [
        os.environ.get("DICT_BUILD_TOOL_BIN"),
        os.path.join(RESOURCES, "ddk"),
        os.path.join(os.path.dirname(RESOURCES), "gudai-hanyu-dicts", "ddk_bin"),
    ]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "build_dict.sh")):
            return c
    return cands[0] or os.path.join(RESOURCES, "ddk")


DDK = _resolve_ddk()
PY = os.environ.get("CONVERTER_PYTHON") or os.path.join(RESOURCES, "python3.13", "bin", "python3")
FIX = os.path.join(SCRIPT_DIR, "fix_dict_plist.sh")

sys.path.insert(0, SCRIPT_DIR)
import wiktionary_parse  # noqa: E402
import stardict_write    # noqa: E402

# 名称会原样写进 Makefile 并被 /bin/sh 执行，除路径字符外还必须挡住
# make 的 $(...) 与 shell 的双引号内替换（$ / 反引号）、以及 Makefile 注释符 #。
_NAME_BAD_CHARS = set("/\\:*?\"<>|\0\n\r\t$`#")


def log(*a):
    print(*a, flush=True)


def sanitize_name(name):
    """校验并规范化词典名，避免路径穿越与 DDK 调用被截断。"""
    n = (name or "").strip()
    if not n:
        raise ValueError("词典名称不能为空。")
    bad = sorted({c for c in n if c in _NAME_BAD_CHARS})
    if bad:
        raise ValueError("词典名称不能包含这些字符：%s" % " ".join(repr(c) for c in bad))
    if n in (".", ".."):
        raise ValueError("词典名称不能是 . 或 ..")
    if len(n) > 120:
        raise ValueError("词典名称过长（%d 字符，上限 120）。" % len(n))
    return n


def run_pyglossary(ifo, project):
    """调用打包内 PyGlossary，把 StarDict 转成 AppleDict 工程。"""
    py = PY if os.path.isfile(PY) else "python3"
    cmd = [py, "-c", "from pyglossary.ui.main import main; main()",
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
        "\t\"$(DICT_BUILD_TOOL_BIN)/build_dict.sh\" $(DICT_BUILD_OPTS) \"$(DICT_NAME)\" \"$(DICT_SRC_PATH)\" \"$(CSS_PATH)\" \"$(PLIST_PATH)\"\n"
        "\t@echo \"Done.\"\n"
        "\n"
        "install:\n"
        "\tmkdir -p $(DESTINATION_FOLDER)\n"
        "\tditto --noextattr --norsrc \"$(DICT_DEV_KIT_OBJ_DIR)/$(DICT_NAME).dictionary\" \"$(DESTINATION_FOLDER)/$(DICT_NAME).dictionary\"\n"
        "\ttouch $(DESTINATION_FOLDER)\n"
        "\n"
        "clean:\n"
        "\t$(RM) -rf $(DICT_DEV_KIT_OBJ_DIR)\n"
    )
    with open(os.path.join(project, "Makefile"), "w", encoding="utf-8") as f:
        f.write(mk)


def _fix_plist(bundle):
    if os.path.isfile(FIX):
        subprocess.run(["bash", FIX, bundle], check=True)
    else:
        log("警告：未找到 fix_dict_plist.sh，跳过 plist 修正")


def _install(bundle, name):
    dest = os.path.expanduser("~/Library/Dictionaries")
    os.makedirs(dest, exist_ok=True)
    target = os.path.join(dest, name + ".dictionary")
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(bundle, target)
    log(">>> 已安装到: %s" % target)


def compile_project(project, outdir, name, install, keep_src=False):
    """在已就绪的工程目录（含 name.xml/.css/.plist）里跑 DDK，产出并交付 .dictionary。"""
    os.makedirs(outdir, exist_ok=True)
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
    _fix_plist(bundle)

    outbundle = os.path.join(outdir, name + ".dictionary")
    if os.path.exists(outbundle):
        shutil.rmtree(outbundle)
    shutil.copytree(bundle, outbundle)
    log(">>> 完成: %s" % outbundle)

    if install:
        _install(bundle, name)

    # 中间工程是构建用的，和成品 *.dictionary 并列出现会让人分不清；默认清掉。
    if not keep_src and os.path.isdir(project) and os.path.abspath(project) != os.path.abspath(outbundle):
        log(">>> 清理中间工程目录（构建用，非成品）: %s" % project)
        subprocess.run(["/bin/rm", "-rf", project], check=False)

    log("=== DONE: 最终产物（请使用这个 *.dictionary）: %s ===" % outbundle)
    return outbundle


def compile_stardict(ifo, outdir, name, install, keep_src=False):
    """StarDict -> AppleDict 工程 -> DDK 编译 .dictionary -> 修 plist -> 交付。"""
    name = sanitize_name(name)
    os.makedirs(outdir, exist_ok=True)
    # 防御：源 StarDict 词条数为 0 时提前报错，避免后续 DDK 崩出看不懂的 perl 错
    wc = _read_wordcount(ifo)
    if wc == 0:
        log("错误：源词典解析出 0 个词条（wordcount=0）。")
        log("常见原因：① 把 StarDict 压缩包（.tar.bz2）误当维基 dump 用了 wiktionary 模式；"
            "② 输入文件不是预期格式。")
        log("建议：用「自动」模式（会自行解包并识别），或确认文件格式后选对应模式。")
        sys.exit(2)
    project = os.path.join(outdir, name)
    if os.path.exists(project):
        shutil.rmtree(project)
    os.makedirs(project)

    run_pyglossary(ifo, project)
    return compile_project(project, outdir, name, install, keep_src)


# ---------------------------------------------------------------- AppleDict 工程

_DEFAULT_CSS = """@charset "UTF-8";
body { font-family: -apple-system, "Songti SC", "STSong", serif; font-size: 14px; line-height: 1.6; }
h1 { font-size: 24px; margin: 0 0 6px 0; }
.entry { padding: 10px 14px; }
.py, .meta, .pron { color: #666; font-size: 12px; }
.sense-h { font-weight: 600; margin-top: 8px; }
.spy { font-weight: normal; color: #777; }
.sense-d { margin: 2px 0 8px 0; }
.citian { margin-top: 10px; font-size: 12px; color: #444; }
.fan { font-size: 12px; color: #555; }
.ex { margin: 4px 0; }
.cl { color: #333; }
.md { color: #666; }
.arr { color: #999; }
.src { margin-top: 10px; font-size: 11px; color: #999; }
"""


def _default_plist(name):
    ident = "com.dictconv.d" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]
    esc = lambda s: html.escape(s, quote=False)
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>CFBundleDevelopmentRegion</key>
\t<string>zh_CN</string>
\t<key>CFBundleIdentifier</key>
\t<string>%s</string>
\t<key>CFBundleDisplayName</key>
\t<string>%s</string>
\t<key>CFBundleName</key>
\t<string>%s</string>
\t<key>CFBundleShortVersionString</key>
\t<string>1.0</string>
\t<key>DCSDictionaryCopyright</key>
\t<string>仅供个人学习使用.</string>
\t<key>DCSDictionaryManufacturerName</key>
\t<string>苹果字典转换器.</string>
\t<key>DCSDictionaryXSL</key>
\t<string>Dictionary.xsl</string>
\t<key>DCSDictionaryDefaultPrefs</key>
\t<dict>
\t\t<key>DefaultLanguage</key>
\t\t<string>zh</string>
\t</dict>
</dict>
</plist>
""" % (ident, esc(name), esc(name))


def _looks_like_appledict_xml(path):
    try:
        with open(path, "rb") as f:
            head = f.read(16384).decode("utf-8", "replace")
    except OSError:
        return False
    return "<d:dictionary" in head or "DictionaryService" in head


def compile_appledict(src, outdir, name, install, keep_src=False):
    """已有的 AppleDict 工程（xml + plist + css）直接编译。工程先复制到临时目录，
    这样既不会污染用户目录，也允许 --name 与源工程文件名不同。

    src 可以是工程目录，也可以直接是那个 .xml —— 直接给文件时严格用该文件，
    不再扫目录（避免目录里有多个工程时选错）。
    """
    name = sanitize_name(name)
    if os.path.isfile(src):
        if not _looks_like_appledict_xml(src):
            raise ValueError("「%s」不是 AppleDict 源文件（缺少 <d:dictionary> 根节点）。"
                             % os.path.basename(src))
        xml = src
    else:
        xmls = [p for p in _find_all(src, (".xml",))
                if not _is_macos_meta(p) and _looks_like_appledict_xml(p)]
        if not xmls:
            raise ValueError("在 %s 里没找到 AppleDict 源文件（缺少含 <d:dictionary> 的 .xml）。" % src)
        # 优先用与目标名同名的 xml；否则用第一个
        xml = next((p for p in xmls if os.path.splitext(os.path.basename(p))[0] == name), xmls[0])
        if len(xmls) > 1:
            log("警告：目录内有 %d 个 AppleDict 源文件，使用 %s" % (len(xmls), os.path.basename(xml)))
    base = os.path.splitext(os.path.basename(xml))[0]
    srcdir = os.path.dirname(xml)

    work = tempfile.mkdtemp(prefix="dictconv_ad_")
    try:
        shutil.copy2(xml, os.path.join(work, name + ".xml"))
        css_src = os.path.join(srcdir, base + ".css")
        if os.path.isfile(css_src):
            shutil.copy2(css_src, os.path.join(work, name + ".css"))
        else:
            with open(os.path.join(work, name + ".css"), "w", encoding="utf-8") as f:
                f.write(_DEFAULT_CSS)
        plist_src = os.path.join(srcdir, base + ".plist")
        if os.path.isfile(plist_src):
            shutil.copy2(plist_src, os.path.join(work, name + ".plist"))
        else:
            log("提示：工程里没有 %s.plist，已生成默认 plist。" % base)
            with open(os.path.join(work, name + ".plist"), "w", encoding="utf-8") as f:
                f.write(_default_plist(name))
        log(">>> [0] 使用现有 AppleDict 工程：%s" % xml)
        return compile_project(work, outdir, name, install, keep_src)
    finally:
        if keep_src and os.path.isdir(work):
            log(">>> 已保留编译工程（--keep-src）: %s" % work)
        elif os.path.isdir(work):
            shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- 命令实现

def cmd_wiktionary(args):
    name = sanitize_name(args.name or "维基文言文词典（古汉语→现代汉语）")
    stardict_dir = os.path.join(args.outdir, name + "_stardict")
    ifo = wiktionary_parse.parse_wiktionary_bz2(args.bz2, stardict_dir, name)
    out = compile_stardict(ifo, args.outdir, name, args.install, args.keep_src)
    if not args.keep_src and os.path.isdir(stardict_dir):
        shutil.rmtree(stardict_dir, ignore_errors=True)
    return out


def _convert_stardict(ifo, outdir, name, install, keep_src):
    """统一入口：必要时先把 .dict.dz/.idx.gz 解成明文，再编译。"""
    real_ifo, tmp = _materialize_stardict(ifo)
    try:
        return compile_stardict(real_ifo, outdir, name, install, keep_src)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def cmd_stardict(args):
    name = args.name or os.path.splitext(os.path.basename(args.ifo))[0]
    return _convert_stardict(args.ifo, args.outdir, name, args.install, args.keep_src)


def cmd_csv(args):
    name = sanitize_name(args.name or args.bookname)
    # bookname 会写进 .ifo 的多行头部，必须去掉换行，否则头部被截断
    bookname = (args.bookname or "").replace("\r", " ").replace("\n", " ").strip()
    stardict_dir = os.path.join(args.outdir, name + "_stardict")
    ifo = stardict_write.csv_to_stardict(args.csv, stardict_dir, bookname)
    out = compile_stardict(ifo, args.outdir, name, args.install, args.keep_src)
    if not args.keep_src and os.path.isdir(stardict_dir):
        shutil.rmtree(stardict_dir, ignore_errors=True)
    return out


def cmd_tabfile(args):
    name = sanitize_name(args.name or os.path.splitext(os.path.basename(args.txt))[0])
    stardict_dir = os.path.join(args.outdir, name + "_stardict")
    ifo = stardict_write.tabfile_to_stardict(args.txt, stardict_dir, name)
    out = compile_stardict(ifo, args.outdir, name, args.install, args.keep_src)
    if not args.keep_src and os.path.isdir(stardict_dir):
        shutil.rmtree(stardict_dir, ignore_errors=True)
    return out


def cmd_appledict(args):
    if args.name:
        name = args.name
    elif os.path.isfile(args.src):
        # 直接给了 .xml，名称要剥掉扩展名，否则会产出 foo.xml.dictionary
        name = os.path.splitext(os.path.basename(args.src))[0]
    else:
        name = os.path.basename(os.path.normpath(args.src))
    return compile_appledict(args.src, args.outdir, name, args.install, args.keep_src)


def _read_wordcount(ifo):
    """从 StarDict .ifo 读出 wordcount 字段；读不到返回 -1。"""
    try:
        with open(ifo, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("wordcount="):
                    return int((line.split("=", 1)[1].strip() or "0"))
    except Exception:
        return -1
    return -1


def _materialize_stardict(ifo):
    """StarDict 常把 .dict 压成 .dict.dz、.idx 压成 .idx.gz。
    这里在临时目录里解出明文副本（不动用户原文件），返回 (可用 ifo, 临时目录或 None)。"""
    if not os.path.isfile(ifo):
        raise ValueError("找不到 .ifo 文件：%s" % ifo)
    d = os.path.dirname(os.path.abspath(ifo))
    base = os.path.splitext(os.path.basename(ifo))[0]
    plain = {"dict": os.path.join(d, base + ".dict"), "idx": os.path.join(d, base + ".idx")}
    packed = {"dict": os.path.join(d, base + ".dict.dz"), "idx": os.path.join(d, base + ".idx.gz")}
    need = {k: (not os.path.exists(plain[k])) and os.path.exists(packed[k]) for k in plain}
    if not any(need.values()):
        return ifo, None

    tmp = tempfile.mkdtemp(prefix="dictconv_sd_")
    try:
        shutil.copy2(ifo, os.path.join(tmp, os.path.basename(ifo)))
        for k in ("dict", "idx"):
            dst = os.path.join(tmp, base + "." + k)
            if need[k]:
                log(">>> [stardict] 解压 %s" % packed[k])
                with gzip.open(packed[k], "rb") as fin, open(dst, "wb") as fout:
                    shutil.copyfileobj(fin, fout, 1024 * 1024)
            elif os.path.exists(plain[k]):
                shutil.copy2(plain[k], dst)
        syn = os.path.join(d, base + ".syn")
        if os.path.exists(syn):
            shutil.copy2(syn, os.path.join(tmp, base + ".syn"))
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    if not (os.path.exists(os.path.join(tmp, base + ".idx")) and
            os.path.exists(os.path.join(tmp, base + ".dict"))):
        shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError("StarDict 不完整：缺少 .idx/.dict（或压缩版 .idx.gz/.dict.dz）：%s" % ifo)
    return os.path.join(tmp, os.path.basename(ifo)), tmp


ARCHIVE_EXTS = (".tar.bz2", ".tar.gz", ".tgz", ".tar.xz", ".tar", ".zip")


def _is_archive(path):
    p = path.lower()
    return any(p.endswith(e) for e in ARCHIVE_EXTS)


def _unpack(archive, dest):
    p = archive.lower()
    if p.endswith((".tar.bz2", ".tar.gz", ".tgz", ".tar.xz", ".tar")):
        with tarfile.open(archive, "r:*") as t:
            t.extractall(dest, filter="data")
    elif p.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        raise ValueError("不支持的压缩包格式：%s" % archive)


def _find_all(root, exts):
    """在 root（含子目录）里找所有匹配扩展名的文件（顺序确定）。"""
    hits = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames.sort()
        for fn in sorted(files):
            if any(fn.lower().endswith(e) for e in exts):
                hits.append(os.path.join(dirpath, fn))
    return hits


def _is_macos_meta(path):
    """过滤 macOS 压缩包里的 __MACOSX / ._xxx 资源分支垃圾文件。"""
    return os.path.basename(path).startswith("._") or "__MACOSX" in path.split(os.sep)


def _looks_like_wiktionary(bz2_path):
    """快速嗅探：解压开头一小段，看是否像维基 dump（XML）。"""
    try:
        with bz2.open(bz2_path, "rt", encoding="utf-8", errors="replace") as f:
            head = f.read(65536)
    except (OSError, EOFError, ValueError) as e:
        log("警告：嗅探 %s 失败（%s），按非维基 dump 处理。" % (bz2_path, e))
        return False
    return ("<mediawiki" in head) or ("<page>" in head) or ("<text" in head)


TEXT_EXTS = (".txt", ".tsv", ".tab")


def _looks_like_tabfile(path):
    """判断文本是否像「词条<TAB>释义」两列：抽样 200 行，含分隔符的比例 > 60%。"""
    good = seen = 0
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 200:
                    break
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                seen += 1
                if any(sep in line for sep in ("\t", "\u3000")):
                    good += 1
    except OSError:
        return False
    return seen > 0 and good / seen > 0.6


def _classify(path, input_path):
    """判断（解压后的）目录或文件到底是什么，返回 (mode, resolved_path)。"""
    if os.path.isdir(path):
        ifos = [p for p in _find_all(path, (".ifo",)) if not _is_macos_meta(p)]
        if ifos:
            if len(ifos) > 1:
                log("警告：目录内发现 %d 个 .ifo，仅使用第一个：%s" % (len(ifos), ifos[0]))
            return "stardict", ifos[0]
        xmls = [p for p in _find_all(path, (".xml",))
                if not _is_macos_meta(p) and _looks_like_appledict_xml(p)]
        if xmls:
            if len(xmls) > 1:
                log("警告：目录内发现 %d 个 AppleDict 源文件，仅使用第一个：%s" % (len(xmls), xmls[0]))
            return "appledict", xmls[0]
        csvs = [p for p in _find_all(path, (".csv",)) if not _is_macos_meta(p)]
        if csvs:
            if len(csvs) > 1:
                log("警告：目录内发现 %d 个 .csv，仅使用第一个：%s" % (len(csvs), csvs[0]))
            return "csv", csvs[0]
        for p in [p for p in _find_all(path, TEXT_EXTS) if not _is_macos_meta(p)]:
            if _looks_like_tabfile(p):
                return "tabfile", p
        # 可能存在多个 .bz2（无关文件 / 分卷 dump），逐个嗅探，取第一个像维基 dump 的
        dumps = [p for p in _find_all(path, (".bz2",)) if _looks_like_wiktionary(p)]
        if dumps:
            if len(dumps) > 1:
                log("警告：目录内发现 %d 个维基 dump，仅使用第一个：%s" % (len(dumps), dumps[0]))
            return "wiktionary", dumps[0]
        raise ValueError("输入 %s（解包后目录 %s）内未找到 .ifo / .xml / .csv / 两列文本 / 维基 dump，无法识别。"
                         % (input_path, path))

    low = path.lower()
    if low.endswith(".ifo"):
        return "stardict", path
    if low.endswith(".csv"):
        return "csv", path
    if low.endswith(TEXT_EXTS):
        if not _looks_like_tabfile(path):
            raise ValueError("「%s」不像两列文本（每行需要「词条<TAB>释义」），无法识别。"
                             % os.path.basename(path))
        return "tabfile", path
    if low.endswith(".xml"):
        if _looks_like_appledict_xml(path):
            return "appledict", path
        raise ValueError("「%s」不是 AppleDict 源文件（缺少 <d:dictionary> 根节点）。"
                         % os.path.basename(path))
    if low.endswith(".bz2") and not _is_archive(path):
        if _looks_like_wiktionary(path):
            return "wiktionary", path
        raise ValueError("这是压缩的词库数据（很可能是 .dict.dz），缺少配套的 .ifo 索引，无法转换。"
                         "请用完整的 StarDict 包（含 .ifo/.idx/.dict）。")
    raise ValueError("无法识别的文件类型：%s" % path)


def _detect(input_path):
    """判断输入到底是什么，返回 (mode, resolved_path, temp_dirs)。

    - 压缩包：先解到临时目录，再看里面内容
    - 目录：直接看里面的 .ifo / .xml / .csv / 文本 / dump
    - 单文件：按扩展名 + 内容嗅探
    """
    temp_dirs = []
    path = input_path
    if _is_archive(path):
        tmp = tempfile.mkdtemp(prefix="dictconv_")
        temp_dirs.append(tmp)
        log(">>> [auto] 解压压缩包：%s -> %s" % (path, tmp))
        try:
            _unpack(path, tmp)
        except Exception:
            # 解包失败：清掉已建的临时目录，避免残留半个大词库
            shutil.rmtree(tmp, ignore_errors=True)
            temp_dirs.remove(tmp)
            raise
        path = tmp
    try:
        mode, resolved = _classify(path, input_path)
    except Exception:
        # 检测阶段失败：临时目录已交不到调用方，这里自行清理，避免残留整个词库
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        raise
    return mode, resolved, temp_dirs


def cmd_auto(args):
    try:
        mode, resolved, temp_dirs = _detect(args.input)
    except (ValueError, OSError, EOFError, tarfile.TarError, zipfile.BadZipFile,
            lzma.LZMAError, RuntimeError, NotImplementedError) as e:
        log("错误：无法识别/解包输入文件：%s" % e)
        sys.exit(2)
    log(">>> [auto] 识别为：%s（实际使用 %s）" % (mode, resolved))
    try:
        if mode == "stardict":
            name = args.name or os.path.splitext(os.path.basename(resolved))[0]
            return _convert_stardict(resolved, args.outdir, name, args.install, args.keep_src)
        if mode == "csv":
            bookname = args.name or os.path.splitext(os.path.basename(resolved))[0]
            sub = argparse.Namespace(csv=resolved, outdir=args.outdir, bookname=bookname,
                                     name=args.name, install=args.install, keep_src=args.keep_src)
            return cmd_csv(sub)
        if mode == "tabfile":
            sub = argparse.Namespace(txt=resolved, outdir=args.outdir,
                                     name=args.name, install=args.install, keep_src=args.keep_src)
            return cmd_tabfile(sub)
        if mode == "appledict":
            # 直接传已识别出的那个 xml，避免再扫目录时选到别的工程
            return compile_appledict(resolved, args.outdir,
                                     args.name or os.path.splitext(os.path.basename(resolved))[0],
                                     args.install, args.keep_src)
        if mode == "wiktionary":
            sub = argparse.Namespace(bz2=resolved, outdir=args.outdir, name=args.name,
                                     install=args.install, keep_src=args.keep_src)
            return cmd_wiktionary(sub)
    finally:
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)


def cmd_detect(args):
    """只探测类型，输出 JSON（图形界面预检用）。不做解包，保证秒回。"""
    info = {"ok": True, "input": os.path.basename(args.input)}
    labels = {
        "stardict": "StarDict 词典",
        "csv": "dictkit 系 CSV",
        "tabfile": "词条<TAB>释义 文本",
        "appledict": "AppleDict 工程",
        "wiktionary": "中文维基词典 dump",
    }
    try:
        if _is_archive(args.input):
            info.update(mode="archive", label="压缩包（转换时自动解包识别）", wordcount=None)
        else:
            mode, resolved = _classify(args.input, args.input)
            info["mode"] = mode
            info["label"] = labels.get(mode, mode)
            info["resolved"] = os.path.basename(resolved)
            info["wordcount"] = _read_wordcount(resolved) if mode == "stardict" else None
            if info["wordcount"] is not None and info["wordcount"] < 0:
                info["wordcount"] = None
    except Exception as e:
        info.update(ok=False, error=str(e))
    print(json.dumps(info, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description="苹果字典转换器：源文件 -> macOS .dictionary")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--name", default=None)
        sp.add_argument("--install", action="store_true")
        sp.add_argument("--keep-src", action="store_true")

    pw = sub.add_parser("wiktionary", help="中文维基词典 .bz2 dump -> AppleDict")
    pw.add_argument("bz2", help="zhwiktionary-*-pages-articles.xml.bz2")
    pw.add_argument("outdir", help="输出目录")
    common(pw)
    pw.set_defaults(func=cmd_wiktionary)

    ps = sub.add_parser("stardict", help="StarDict(.ifo) -> AppleDict")
    ps.add_argument("ifo", help="StarDict 的 .ifo 文件")
    ps.add_argument("outdir", help="输出目录")
    common(ps)
    ps.set_defaults(func=cmd_stardict)

    pc = sub.add_parser("csv", help="dictkit CSV -> AppleDict")
    pc.add_argument("csv", help="CSV 文件（含 字词索引/释义 等列）")
    pc.add_argument("outdir", help="输出目录")
    pc.add_argument("bookname", help="词典书名（写入 .ifo）")
    common(pc)
    pc.set_defaults(func=cmd_csv)

    pt = sub.add_parser("tabfile", help="「词条<TAB>释义」文本 -> AppleDict")
    pt.add_argument("txt", help=".txt/.tsv/.tab 两列文本")
    pt.add_argument("outdir", help="输出目录")
    common(pt)
    pt.set_defaults(func=cmd_tabfile)

    pd = sub.add_parser("appledict", help="已有 AppleDict 工程 -> 直接编译")
    pd.add_argument("src", help="含 xxx.xml（+ .plist/.css）的工程目录")
    pd.add_argument("outdir", help="输出目录")
    common(pd)
    pd.set_defaults(func=cmd_appledict)

    pa = sub.add_parser("auto", help="自动识别源文件（支持压缩包/目录）并转换")
    pa.add_argument("input", help="源文件或目录（.tar.bz2/.bz2/.csv/.txt/.ifo/.xml/目录）")
    pa.add_argument("outdir", help="输出目录")
    common(pa)
    pa.set_defaults(func=cmd_auto)

    pde = sub.add_parser("detect", help="探测输入类型并输出 JSON（不转换）")
    pde.add_argument("input")
    pde.set_defaults(func=cmd_detect)

    args = p.parse_args()
    try:
        args.func(args)
    except ValueError as e:
        log("错误：%s" % e)
        sys.exit(2)


if __name__ == "__main__":
    main()
