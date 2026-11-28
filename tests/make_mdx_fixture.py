#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成一个最小的合法 MDX 2.0 测试词库（不加密）。

仅用于测试转换器对 .mdx 的识别与读取；不参与产品打包。
用法: python3 tests/make_mdx_fixture.py <输出.mdx>
"""

import struct
import sys
import zlib

TITLE = "测试词库"
ENTRIES = [
    ("苹果", "<b>苹果</b>：一种水果。"),
    ("香蕉", "<b>香蕉</b>：另一种水果。"),
]


def block(data):
    """MDX 块：4字节标志(压缩方式2) + 4字节 adler32 + zlib 数据。"""
    comp = zlib.compress(data)
    return struct.pack("<L", 2) + struct.pack(">L", zlib.adler32(data) & 0xFFFFFFFF) + comp


def build(path):
    # Encoding=UTF-8 时 key 块按单字节编码、\x00 结尾，构造最简单
    enc = "utf-8"
    header_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Dictionary HeaderVersion="2.0" GeneratedByEngineVersion="2.0" '
        'RequiredEngineVersion="2.0" Encrypted="No" Encoding="UTF-8" '
        'Format="Html" Title="%s"/>' % TITLE
    )
    header_bytes = header_xml.encode("utf-16-le") + b"\x00\x00"
    out = bytearray()
    out += struct.pack(">I", len(header_bytes))
    out += header_bytes
    out += struct.pack("<I", zlib.adler32(header_bytes) & 0xFFFFFFFF)

    # 记录块：全部词条释义拼一起
    record_data = bytearray()
    key_ids = []
    for word, defi in ENTRIES:
        key_ids.append(len(record_data))
        record_data += defi.encode("utf-8")
    record_block = block(bytes(record_data))

    # key 块：每条 = 8字节记录偏移(大端) + UTF-16LE 词条 + \x00\x00
    key_data = bytearray()
    for (word, _), rid in zip(ENTRIES, key_ids):
        key_data += struct.pack(">Q", rid)
        key_data += word.encode(enc) + b"\x00"
    key_block = block(bytes(key_data))

    # key block info（v2 固定 zlib 压缩块）
    kbi = bytearray()
    kbi += struct.pack(">Q", len(ENTRIES))            # 本 block 词条数
    kbi += struct.pack(">H", 0) + b"\x00"             # text head size + 终止符
    kbi += struct.pack(">H", 0) + b"\x00"             # text tail size + 终止符
    kbi += struct.pack(">Q", len(key_block))          # compressed size
    kbi += struct.pack(">Q", len(key_data))           # decompressed size
    kbi_block = struct.pack("<L", 2) + struct.pack(">L", zlib.adler32(bytes(kbi)) & 0xFFFFFFFF) + zlib.compress(bytes(kbi))

    # key index：5 x uint64 + adler32
    values = (1, len(ENTRIES), len(kbi), len(kbi_block), len(key_block))
    key_index = b"".join(struct.pack(">Q", v) for v in values)
    key_index += struct.pack(">I", zlib.adler32(key_index) & 0xFFFFFFFF)

    # record index：4 x uint64 + 每块 (compressed, decompressed)
    record_info = struct.pack(">Q", len(record_block)) + struct.pack(">Q", len(record_data))
    record_index = b"".join(struct.pack(">Q", v) for v in
                            (1, len(ENTRIES), len(record_info), len(record_block)))
    record_index += record_info

    out += key_index
    out += kbi_block
    out += key_block
    out += record_index
    out += record_block

    with open(path, "wb") as f:
        f.write(out)
    print("wrote %s (%d bytes)" % (path, len(out)))


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "/tmp/test.mdx")
