#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基於 中文維基詞典 (zh.wiktionary.org, CC BY-SA) 全量 dump，
抽取「漢語」詞條中帶文言標記（literary / archaic / obsolete / 文言文 / |CL）的
===釋義=== 段落，生成「古漢語 → 現代漢語」StarDict，供 PyGlossary 再转 Apple 格式。

每條目內容：
  - 字/詞頭
  - 現代拼音
  - 文言義項（義項說明 + 文言例句 ⇨ 現代白話譯文）

資料來源：中文維基詞典 dump（CC BY-SA），僅作個人學習用途。
"""

import bz2
import re
import struct
import os
import html
import sys

# ---------- 標籤映射 ----------
LABELMAP = {
    'literary': '文言', 'archaic': '古文', 'obsolete': '廢語',
    'dialect': '方言', 'figurative': '比喻', 'or': '或', 'formal': '書面',
    'colloquial': '口語', 'vulgar': '俗', 'derogatory': '貶義',
    'honorific': '敬語', 'technical': '術語', 'rare': '罕用',
    'historical': '歷史', 'mythology': '神話', 'figuratively': '比喻',
    'literally': '字面', 'slang': '俚語', 'idiomatic': '習語',
    'classical': '文言', 'written': '書面', 'literary|or': '文言/或',
}
CLASSICAL_MARK = re.compile(r'\b(?:literary|archaic|obsolete|classical)\b|文言文|\|CL\b|CL-')

LANGCODES = {'zh', 'cmn', 'yue', 'hak', 'nan', 'wuu', 'gan', 'hsn', 'mn', 'md', 'c', 'g', 'h', 'j', 'mb', 'w', 'x', 'dg', 'cantonese', 'mandarin'}


def convert_labels(s: str) -> str:
    def repl(m):
        parts = [p.strip() for p in m.group(1).split('|')]
        labels = []
        for p in parts:
            if p in LANGCODES:
                continue
            labels.append(LABELMAP.get(p, p))
        return '【' + '、'.join(labels) + '】' if labels else ''
    s = re.sub(r'\{\{lb\|([^}]*)\}\}', repl, s)
    s = re.sub(r'\{\{qual\|([^}]*)\}\}', lambda m: '【' + m.group(1).strip() + '】', s)
    s = re.sub(r'\{\{qualifier\|([^}]*)\}\}', lambda m: '【' + m.group(1).strip() + '】', s)
    return s


def clean_inline(s: str) -> str:
    if not s:
        return ""
    s = convert_labels(s)
    s = re.sub(r'<ref[^>]*>.*?</ref>', '', s, flags=re.S)
    s = re.sub(r'<ref[^>]*/>', '', s)
    s = re.sub(r'<br\s*/?>', ' ', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'\{\{n-g\|([^{}]*)\}\}', r'\1', s)
    s = re.sub(r'\{\{zh-l\|([^}|]*)(?:\|[^}]*)?\}\}', r'\1', s)
    s = re.sub(r'\{\{zh-m\|([^}|]*)(?:\|[^}]*)?\}\}', r'\1', s)
    s = re.sub(r'\{\{zh-x\|([^}|]*)(?:\|[^}]*)?\}\}', r'\1', s)
    s = re.sub(r'\{\{lang\|zh\|([^}]*)\}\}', r'\1', s)
    for _ in range(4):
        s = re.sub(r'\{\{[^{}]*\}\}', '', s)
    s = re.sub(r'\[\[(?:[^\[\]|]*\|)?([^\[\]]+)\]\]', r'\1', s)
    s = re.sub(r"'''?([^']+?)'''?", r'\1', s)
    s = re.sub(r'\[\[?([^\]]*?)\]?\]', r'\1', s)
    s = re.sub(r'[ \t]+', ' ', s)
    return s.strip()


def parse_zhx(s: str):
    m = re.search(r'\{\{zh-x\|([^|]*)\|([^|]*)(?:\|[^}]*)?\}\}', s)
    if not m:
        return None
    return (clean_inline(m.group(1)), clean_inline(m.group(2)))


def get_section(text, l2, l3=None):
    m = re.search(r'==\s*' + re.escape(l2) + r'\s*==(.*?)(?=\n==[^=]|\Z)', text, re.S)
    if not m:
        return None
    body = m.group(1)
    if l3:
        m3 = re.search(r'===\s*' + re.escape(l3) + r'\s*===(.*?)(?=\n===|\Z)', body, re.S)
        return m3.group(1) if m3 else None
    return body


def get_pinyin(text):
    m = re.search(r'\{\{zh-pron\b.*?\|m=([^|\n]+)', text, re.S)
    return m.group(1).strip() if m else ''


def is_classical(sec):
    return bool(CLASSICAL_MARK.search(sec))


def render_geshi(sec):
    lines = sec.splitlines()
    groups = []
    cur_def = None
    cur_ex = []
    seen = set()

    def flush():
        if cur_def is not None or cur_ex:
            groups.append((cur_def, cur_ex))

    for ln in lines:
        s = ln.rstrip()
        if not s.strip():
            continue
        m = re.match(r'^(#+)(\*|:)?\s*(.*)$', s)
        if not m:
            t = clean_inline(s)
            if t and set(t) <= set('='):
                continue
            if t:
                cur_ex.append(('note', t))
            continue
        level, sub, content = m.group(1), m.group(2), m.group(3)
        if sub in ('*', ':'):
            raw = content
            is_cl_ex = ('|CL' in raw) or ('CL|' in raw) or ('CL-' in raw) or ('文言文' in raw)
            zx = parse_zhx(raw)
            if zx and zx[1] and is_cl_ex:
                cur_ex.append(('ex', zx[0], zx[1]))
            elif zx and zx[1]:
                pass
            elif is_cl_ex:
                t = clean_inline(raw)
                if t:
                    cur_ex.append(('note', t))
            continue
        else:
            if level == '#':
                flush()
                cur_def = None
                cur_ex = []
            is_cl = bool(CLASSICAL_MARK.search(content))
            t = clean_inline(content)
            cur_def = (t, is_cl) if t else (None, is_cl)

    flush()

    parts = []
    for defi, exs in groups:
        def_text, def_cl = defi if defi else (None, False)
        cl_examples = [e for e in exs if e[0] == 'ex']
        if not (def_cl or cl_examples):
            continue
        if def_text:
            cls = ' class="cldef"' if def_cl else ''
            parts.append('<li%s>%s</li>' % (cls, def_text))
        for e in exs:
            if e[0] == 'ex':
                key = (e[1], e[2])
                if key in seen:
                    continue
                seen.add(key)
                parts.append('<div class="ex"><span class="cl">%s</span> <span class="arr">⇨</span> <span class="md">%s</span></div>'
                            % (html.escape(e[1]), html.escape(e[2])))
            elif e[0] == 'note':
                if e[1] not in seen:
                    seen.add(e[1])
                    parts.append('<div class="ex">%s</div>' % e[1])
    return parts


def build_article(title, text):
    sec = get_section(text, '漢語', '釋義')
    if not sec or not is_classical(sec):
        return None
    parts = ['<div class="entry">', '<h1>%s</h1>' % html.escape(title)]
    pin = get_pinyin(text)
    if pin:
        parts.append('<div class="pron">拼音：%s</div>' % html.escape(pin))
    parts.append('<div class="tag">古漢語 → 現代漢語</div>')
    rendered = render_geshi(sec)
    if not rendered:
        return None
    parts.append('<ul>')
    parts.extend(rendered)
    parts.append('</ul>')
    parts.append('<div class="src">資料來源：中文維基詞典（CC BY-SA）</div>')
    parts.append('</div>')
    return "\n".join(parts)


def parse_wiktionary_bz2(src, outdir, name="wiktionary_classical"):
    os.makedirs(outdir, exist_ok=True)
    entries = []
    total = 0
    kept = 0
    title_re = re.compile(r"<title>(.*?)</title>", re.S)
    text_re = re.compile(r"<text[^>]*>(.*?)</text>", re.S)
    ns_re = re.compile(r"<ns>(\d+)</ns>")

    print("開始流式解析 dump: %s" % src, flush=True)
    with bz2.open(src, "rt", encoding="utf-8", errors="replace") as f:
        buf = []
        in_page = False
        for line in f:
            if not in_page:
                if "<page>" in line:
                    in_page = True
                    buf = [line]
                continue
            buf.append(line)
            if "</page>" in line:
                in_page = False
                block = "".join(buf)
                total += 1
                if total % 50000 == 0:
                    print("  已掃 %d 頁，保留文言條目 %d" % (total, kept), flush=True)
                mns = ns_re.search(block)
                if not mns or mns.group(1) != "0":
                    continue
                mt = title_re.search(block)
                if not mt:
                    continue
                title = mt.group(1).strip()
                if not title or ":" in title:
                    continue
                mtx = text_re.search(block)
                if not mtx:
                    continue
                art = build_article(title, mtx.group(1))
                if art:
                    entries.append((title, art))
                    kept += 1

    print("掃描完成：共 %d 頁，保留文言條目 %d 條" % (total, len(entries)), flush=True)
    entries.sort(key=lambda e: e[0].encode("utf-8"))

    dict_path = os.path.join(outdir, "dict.dict")
    idx_path = os.path.join(outdir, "dict.idx")
    ifo_path = os.path.join(outdir, "dict.ifo")
    offsets = []
    with open(dict_path, "wb") as df:
        for title, art in entries:
            data = art.encode("utf-8")
            offsets.append((title, df.tell(), len(data)))
            df.write(data)
    with open(idx_path, "wb") as ix:
        for title, off, size in offsets:
            ix.write(title.encode("utf-8"))
            ix.write(b"\x00")
            ix.write(struct.pack(">II", off, size))
    dict_size = os.path.getsize(dict_path)
    idx_size = os.path.getsize(idx_path)
    ifo = [
        "StarDict's dict ifo file",
        "version=2.4.2",
        "bookname=%s" % name,
        "wordcount=%d" % len(entries),
        "idxfilesize=%d" % idx_size,
        "sametypesequence=h",
        "author=中文維基詞典貢獻者（CC BY-SA）",
        "description=來自中文維基詞典「漢語」詞條中帶文言標記的釋義，含文言例句與現代白話譯文，用於查閱古漢語字/詞的現代文含義。",
        "date=2026-03-01",
        "website=https://zh.wiktionary.org/",
    ]
    with open(ifo_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ifo) + "\n")
    print("已寫出：%s (%d 條, dict %d bytes, idx %d bytes)" %
          (outdir, len(entries), dict_size, idx_size), flush=True)
    return ifo_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: wiktionary_parse.py <输入.bz2> <输出目录> [词典名]")
        sys.exit(1)
    nm = sys.argv[3] if len(sys.argv) > 3 else "wiktionary_classical"
    parse_wiktionary_bz2(sys.argv[1], sys.argv[2], nm)
