#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把《古代汉语词典（第2版）》/《古汉语常用字字典（第5版）》等 CSV
转换成可被 PyGlossary 直接读取的 StarDict（.ifo/.idx/.dict）。

- 按「字词索引」合并同一字的多个读音/义项
- 释义中的 // 视为换行；❶❷❸/①②③ 等义项序号保留
- 生成 HTML 释义（含拼音、部首、笔画、结构、字级、异体/繁体、相关词表）

数据来源：https://github.com/dictkit/data-dicts （版权归原词典编者/出版机构）
"""

import csv
import os
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


def build_entry(key, rows, cm):
    subs = []
    meta_pinyin = []
    fan = set()
    bu = set()
    jiegou = set()
    ziji = set()
    citongs = []
    for row in rows:
        title = row[cm.get("词条", 2)].strip()
        py = row[cm.get("拼音", 4)].strip()
        more = row[cm.get("更多拼音", 6)].strip() if "更多拼音" in cm else ""
        defin = row[cm["释义"]].strip()
        subs.append((title, py, more, defin))
        if py:
            meta_pinyin.append(py)
        for fld in ("异体", "繁体", "繁体异体"):
            if fld in cm and row[cm[fld]].strip():
                fan.add(row[cm[fld]].strip())
        for fld in ("部首", "笔画", "结构", "字级"):
            if fld in cm and row[cm[fld]].strip():
                if fld == "部首":
                    bu.add(row[cm[fld]].strip())
                elif fld == "结构":
                    jiegou.add(row[cm[fld]].strip())
                elif fld == "字级":
                    ziji.add(row[cm[fld]].strip())
        for fld in ("前缀词表", "后缀词表", "其他词表"):
            if fld in cm and row[cm[fld]].strip():
                for w in row[cm[fld]].split():
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


def csv_to_stardict(src, outdir, bookname):
    os.makedirs(outdir, exist_ok=True)
    groups = {}
    order = []
    with open(src, encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        header = next(r)
        cm = col_map(header)
        if "释义" in cm:
            defin_col = cm["释义"]
        else:
            defin_col = len(header) - 1
        cm["释义"] = defin_col
        n = 0
        for row in r:
            if len(row) <= max(cm.values()):
                if len(row) <= cm.get("字词索引", 0):
                    continue
            key = row[cm.get("字词索引", 0)].strip()
            if not key:
                continue
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
            n += 1
    print("读取行数: %d, 合并后词条数: %d" % (n, len(groups)), flush=True)

    keys = sorted(groups.keys())

    dict_path = os.path.join(outdir, "dict.dict")
    idx_path = os.path.join(outdir, "dict.idx")
    ifo_path = os.path.join(outdir, "dict.ifo")

    dict_data = bytearray()
    idx_data = bytearray()
    for k in keys:
        body = build_entry(k, groups[k], cm).encode("utf-8")
        off = len(dict_data)
        dict_data += body
        idx_data += k.encode("utf-8") + b"\x00" + struct.pack(">II", off, len(body))

    open(dict_path, "wb").write(dict_data)
    open(idx_path, "wb").write(idx_data)

    ifo = (
        "StarDict's dict ifo file\n"
        "version=2.4.2\n"
        "bookname=%s\n" % bookname +
        "wordcount=%d\n" % len(keys) +
        "idxfilesize=%d\n" % len(idx_data) +
        "sametypesequence=h\n"
        "author=整理自《%s》（版权归原编者/出版机构）\n" % bookname +
        "description=古汉语→现代汉语 词典，由 CSV 转换，供个人学习使用\n"
        "date=2026-10-18\n"
    )
    open(ifo_path, "w", encoding="utf-8").write(ifo)

    print("完成: %s" % outdir, flush=True)
    print("  .ifo wordcount=%d" % len(keys), flush=True)
    print("  .dict size=%d bytes" % len(dict_data), flush=True)
    print("  .idx size=%d bytes" % len(idx_data), flush=True)
    return ifo_path


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: stardict_write.py <输入CSV> <输出目录> <词典书名>")
        sys.exit(1)
    csv_to_stardict(sys.argv[1], sys.argv[2], sys.argv[3])
