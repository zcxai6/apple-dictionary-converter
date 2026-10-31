#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把《古代汉语词典（第2版）》/《古汉语常用字字典（第5版）》等 CSV、以及通用
制表符分隔的 TSV/TXT，转换成可被 PyGlossary 直接读取的 StarDict（.ifo/.idx/.dict）。

- CSV：按「字词索引」合并同一字的多个读音/义项
- TSV/TXT：每行「词条<TAB>释义」，同一词条多行自动合并为多个义项
- 释义中的 // 视为换行；❶❷❸/①②③ 等义项序号保留
- 生成 HTML 释义（CSV 含拼音、部首、笔画、结构、字级、异体/繁体、相关词表）

数据来源：https://github.com/dictkit/data-dicts （版权归原词典编者/出版机构）
"""

import csv
import os
import re
import html
import struct
import sys


def col_map(header):
    return {name: i for i, name in enumerate(header)}


def esc(t):
    return html.escape(t or "", quote=False)


def render_def(t):
    # // -> 换行；其余原样（已 escape）
    return esc(t).replace("//", "<br/>")


def cell(row, cm, name, default=""):
    """安全取列：列不存在或该行缺列时返回 default，避免短行 IndexError。"""
    idx = cm.get(name)
    if idx is None or idx < 0 or idx >= len(row):
        return default
    v = row[idx]
    return v.strip() if isinstance(v, str) else default


def build_entry(key, rows, cm):
    subs = []
    meta_pinyin = []
    fan = set()
    bu = set()
    jiegou = set()
    ziji = set()
    citongs = []
    for row in rows:
        title = cell(row, cm, "词条", key)
        py = cell(row, cm, "拼音")
        more = cell(row, cm, "更多拼音")
        defin = cell(row, cm, "释义")
        subs.append((title, py, more, defin))
        if py:
            meta_pinyin.append(py)
        for fld in ("异体", "繁体", "繁体异体"):
            v = cell(row, cm, fld)
            if v:
                fan.add(v)
        for fld in ("部首", "笔画", "结构", "字级"):
            v = cell(row, cm, fld)
            if v:
                if fld == "部首":
                    bu.add(v)
                elif fld == "结构":
                    jiegou.add(v)
                elif fld == "字级":
                    ziji.add(v)
        for fld in ("前缀词表", "后缀词表", "其他词表"):
            v = cell(row, cm, fld)
            if v:
                for w in v.split():
                    if w.strip():
                        citongs.append(w.strip())

    parts = []
    parts.append('<div class="entry">')
    parts.append('<h1>%s</h1>' % esc(key))
    if meta_pinyin:
        parts.append('<div class="py">%s</div>' % esc("　".join(dict.fromkeys(meta_pinyin))))
    meta_bits = []
    if bu:
        meta_bits.append("部首：" + " ".join(sorted(bu)))
    if jiegou:
        meta_bits.append("结构：" + " ".join(sorted(jiegou)))
    if ziji:
        meta_bits.append(" ".join(sorted(ziji)))
    if meta_bits:
        parts.append('<div class="meta">%s</div>' % "　·　".join(meta_bits))
    if fan:
        parts.append('<div class="fan">异体/繁体：%s</div>' % esc("　".join(sorted(fan))))
    parts.append('<div class="body">')
    for title, py, more, defin in subs:
        label = title if title else key
        pys = py + ((" / " + more) if more else "")
        parts.append('<div class="sense">')
        parts.append('<div class="sense-h">%s　<span class="spy">%s</span></div>' % (esc(label), esc(pys)))
        parts.append('<div class="sense-d">%s</div>' % render_def(defin))
        parts.append('</div>')
    parts.append('</div>')
    if citongs:
        parts.append('<div class="citian">相关词语：%s</div>' % esc("　".join(dict.fromkeys(citongs))))
    parts.append('</div>')
    return "".join(parts)


def _write_stardict(entries, outdir, bookname, author, description):
    """把 (key, html_body) 序列写成 StarDict 三件套，返回 .ifo 路径。

    entries 可以是生成器（大词典不必把全部 HTML 同时留在内存里）；
    调用方需保证按 UTF-8 字节序有序，StarDict 要求索引有序。
    """
    os.makedirs(outdir, exist_ok=True)
    dict_path = os.path.join(outdir, "dict.dict")
    idx_path = os.path.join(outdir, "dict.idx")
    ifo_path = os.path.join(outdir, "dict.ifo")

    dict_data = bytearray()
    idx_data = bytearray()
    count = 0
    for k, body in entries:
        b = body.encode("utf-8")
        off = len(dict_data)
        dict_data += b
        idx_data += k.encode("utf-8") + b"\x00" + struct.pack(">II", off, len(b))
        count += 1

    with open(dict_path, "wb") as f:
        f.write(dict_data)
    with open(idx_path, "wb") as f:
        f.write(idx_data)

    ifo = (
        "StarDict's dict ifo file\n"
        "version=2.4.2\n"
        "bookname=%s\n" % bookname +
        "wordcount=%d\n" % count +
        "idxfilesize=%d\n" % len(idx_data) +
        "sametypesequence=h\n"
        "author=%s\n" % author +
        "description=%s\n" % description +
        "date=2026-10-31\n"
    )
    with open(ifo_path, "w", encoding="utf-8") as f:
        f.write(ifo)

    print("完成: %s" % outdir, flush=True)
    print("  .ifo wordcount=%d" % count, flush=True)
    print("  .dict size=%d bytes" % len(dict_data), flush=True)
    print("  .idx size=%d bytes" % len(idx_data), flush=True)
    return ifo_path


def csv_to_stardict(src, outdir, bookname):
    groups = {}
    with open(src, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        try:
            header = next(r)
        except StopIteration:
            raise ValueError("CSV 是空文件：%s" % src)
        cm = col_map([h.strip() for h in header])
        if "释义" in cm:
            defin_col = cm["释义"]
        else:
            defin_col = len(header) - 1
        cm["释义"] = defin_col
        key_col = cm.get("字词索引", cm.get("词条", 0))
        n = 0
        for row in r:
            if not row:
                continue
            key = row[key_col].strip() if key_col < len(row) else ""
            if not key:
                continue
            groups.setdefault(key, []).append(row)
            n += 1
    print("读取行数: %d, 合并后词条数: %d" % (n, len(groups)), flush=True)
    if not groups:
        raise ValueError("CSV 里没有解析出任何词条（检查「字词索引」列是否为空）：%s" % src)

    keys = sorted(groups.keys(), key=lambda k: k.encode("utf-8"))
    entries = ((k, build_entry(k, groups[k], cm)) for k in keys)
    return _write_stardict(
        entries, outdir, bookname,
        author="整理自《%s》（版权归原编者/出版机构）" % bookname,
        description="古汉语→现代汉语 词典，由 CSV 转换，供个人学习使用",
    )


# 只认真正的分隔符：制表符或全角空格。四空格缩进在散文里太常见，误判代价高。
_TAB_SPLITTERS = ("\t", "\u3000")


def _split_tab_line(line):
    """按出现位置最早的分隔符切分，保证与类型检测的判定一致。"""
    best = None
    for sep in _TAB_SPLITTERS:
        i = line.find(sep)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, sep)
    if best is None:
        return None
    i, sep = best
    return line[:i].strip(), line[i + len(sep):].strip()


def tabfile_to_stardict(src, outdir, bookname):
    """通用「词条<TAB>释义」文本 -> StarDict。

    - 每行一条；同一词条出现多次即合并为多个义项
    - 以 # 开头的行、空行忽略
    - 释义含 HTML 标签时按 HTML 原样保留（想换行就写 <br/>）；否则当纯文本转义。
      不做 "//" → <br/> 之类的猜测替换，避免把 http:// 这类内容改坏。
    """
    groups = {}
    total = 0
    bad = 0
    with open(src, encoding="utf-8-sig", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            total += 1
            pair = _split_tab_line(line)
            if not pair or not pair[0] or not pair[1]:
                # 空释义（「词条<TAB>」或「词条　」）也算坏行，否则会产出空白词条页
                bad += 1
                continue
            head, body = pair
            groups.setdefault(head, []).append(body)

    if not groups:
        raise ValueError(
            "文本里没有解析出词条。要求每行形如「词条<TAB>释义」，"
            "共读到 %d 行、其中 %d 行不含分隔符。" % (total, bad))
    if bad and bad > total * 0.5:
        raise ValueError(
            "超过一半的行（%d/%d）没有找到分隔符，请确认是制表符分隔的两列文本。"
            % (bad, total))

    print("读取行数: %d, 合并后词条数: %d, 跳过 %d 行" % (total, len(groups), bad), flush=True)

    # 只有真像标签（<b>、<br/>、</div> 之类）才当 HTML 放行；
    # 像 "1 < 2 > 0" 这种文本必须转义，否则会产出非法 XML 让 DDK 编译失败。
    markup_re = re.compile(r"</?[a-zA-Z][^>]*>")

    def render(body):
        return body if markup_re.search(body) else esc(body)

    def gen():
        for k in sorted(groups, key=lambda s: s.encode("utf-8")):
            senses = [render(b) for b in groups[k] if b]
            body = '<div class="entry"><h1>%s</h1><div class="body">%s</div></div>' % (
                esc(k), "".join('<div class="sense-d">%s</div>' % s for s in senses))
            yield k, body

    return _write_stardict(
        gen(), outdir, bookname,
        author="由制表符文本转换（版权归原编者/出版机构）",
        description="由 TSV/TXT 文本转换的词典，供个人学习使用",
    )


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: stardict_write.py <输入CSV|TSV> <输出目录> <词典书名>")
        sys.exit(1)
    path = sys.argv[1]
    if path.lower().endswith((".txt", ".tsv", ".tab")):
        tabfile_to_stardict(path, sys.argv[2], sys.argv[3])
    else:
        csv_to_stardict(path, sys.argv[2], sys.argv[3])
