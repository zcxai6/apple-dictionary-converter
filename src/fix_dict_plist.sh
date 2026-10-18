#!/bin/bash
# 把编译好的 .dictionary 里 Info.plist 的 IDXIndexWritable 翻成 false（索引只读固定）
# 用法: fix_dict_plist.sh /path/to/XXX.dictionary
set -e
DICT="$1"
PLIST="$DICT/Contents/Info.plist"
if [ ! -f "$PLIST" ]; then echo "找不到 $PLIST"; exit 1; fi
TMP=$(mktemp /tmp/dictplist.XXXXXX.xml)
plutil -convert xml1 -o "$TMP" "$PLIST"
# 只翻转 IDXIndexWritable 对应的 <true/>（保留其它 true）
perl -0777 -i -pe 's{(<key>IDXIndexWritable</key>\s*)<true/>}{$1<false/>}g' "$TMP"
plutil -convert binary1 -o "$PLIST" "$TMP"
rm -f "$TMP"
echo "已修正: $DICT (IDXIndexWritable -> false)"
