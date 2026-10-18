#!/bin/bash
# 古汉语词典转换器 —— .app 入口
# 双击后用系统对话框选择源文件/输出目录，再调起 Terminal 跑 convert.py 并显示进度。
# 资源相对于本脚本定位：Contents/Resources/{python3.13, ddk, scripts}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTENTS="$(dirname "$SCRIPT_DIR")"
RESOURCES="$CONTENTS/Resources"
SCRIPTS="$RESOURCES/scripts"
PY="$RESOURCES/python3.13/bin/python3"
CONVERT="$SCRIPTS/convert.py"
DDK="$RESOURCES/ddk"

# ---------- Rosetta 2 前置检查（Apple Silicon 需要它来运行 x86 版 DDK）----------
if [ "$(uname -m)" = "arm64" ]; then
  if ! arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
    osascript -e 'display dialog "本机是 Apple Silicon，但缺少 Rosetta 2，无法运行词典编译器（DDK 为 x86 程序）。

请先安装 Rosetta 2：
  打开「终端」执行  softwareupdate --install-rosetta
安装完成后重新打开本应用即可。" buttons {"好"} default button "好" with title "需要 Rosetta 2"'
    exit 1
  fi
fi

# ---------- 选择输入文件 ----------
INPUT=$(osascript -e 'try
  set f to choose file with prompt "选择要转换的源文件：" of type {"bz2", "ifo", "idx", "dict", "csv", "public.data"}
  return POSIX path of f
on error
  return ""
end try')

if [ -z "$INPUT" ]; then
  osascript -e 'display dialog "未选择文件，已取消。" buttons {"好"} default button "好"'
  exit 0
fi

# ---------- 根据扩展名判断模式 ----------
EXT="${INPUT##*.}"
MODE=""
case "$EXT" in
  bz2) MODE="wiktionary" ;;
  ifo|idx|dict) MODE="stardict" ;;
  csv) MODE="csv" ;;
  *)
    osascript -e 'display dialog "不支持的文件类型（支持 .bz2 / .ifo / .csv）。" buttons {"好"} default button "好"'
    exit 1
    ;;
esac

# ---------- CSV 需要书名 ----------
BOOKNAME=""
if [ "$MODE" = "csv" ]; then
  BOOKNAME=$(osascript -e 'display dialog "请输入词典书名（写入元信息）：" default answer "" buttons {"确定"} default button 1
    text returned of result' 2>/dev/null)
  if [ -z "$BOOKNAME" ]; then BOOKNAME="自定义词典"; fi
fi

# ---------- 词典名称（输出 .dictionary 名）----------
DEFNAME=$(basename "$INPUT")
DEFNAME="${DEFNAME%.*}"
NAME=$(osascript -e "display dialog \"输出的词典名称（.dictionary 文件夹名）：\" default answer \"$DEFNAME\" buttons {\"确定\"} default button 1
  text returned of result" 2>/dev/null)
if [ -z "$NAME" ]; then NAME="$DEFNAME"; fi

# ---------- 输出目录 ----------
OUTDIR=$(osascript -e 'try
  set d to choose folder with prompt "选择输出目录："
  return POSIX path of d
on error
  return ""
end try')
if [ -z "$OUTDIR" ]; then
  osascript -e 'display dialog "未选择输出目录，已取消。" buttons {"好"} default button "好"'
  exit 0
fi

# ---------- 是否安装到系统词典目录 ----------
INSTALL_FLAG=""
ANS=$(osascript -e 'display dialog "转换完成后是否安装到系统词典目录（~/Library/Dictionaries）？" buttons {"安装", "仅导出"} default button "仅导出"
  button returned of result' 2>/dev/null)
if [ "$ANS" = "安装" ]; then INSTALL_FLAG="--install"; fi

# ---------- 组装转换参数 ----------
ARG_LINE=""
case "$MODE" in
  wiktionary) ARG_LINE="wiktionary \"$INPUT\" \"$OUTDIR\" --name \"$NAME\"" ;;
  stardict)   ARG_LINE="stardict \"$INPUT\" \"$OUTDIR\" --name \"$NAME\"" ;;
  csv)        ARG_LINE="csv \"$INPUT\" \"$OUTDIR\" \"$BOOKNAME\" --name \"$NAME\"" ;;
esac
if [ -n "$INSTALL_FLAG" ]; then ARG_LINE="$ARG_LINE --install"; fi

# ---------- 生成可执行的 .command 并交给 Terminal 运行 ----------
TMPCMD="$(mktemp -t gudai_convert.XXXXXX).command"
cat > "$TMPCMD" <<TMP
#!/bin/bash
export DICT_BUILD_TOOL_BIN="$DDK"
export CONVERTER_PYTHON="$PY"
clear
echo "古汉语词典转换器 —— 开始转换"
echo "模式: $MODE"
echo "源: $INPUT"
echo "输出: $OUTDIR/$NAME.dictionary"
echo "----------------------------------------"
"$PY" "$CONVERT" $ARG_LINE
echo "----------------------------------------"
echo "转换结束。"
OUTDICT="$OUTDIR/$NAME.dictionary"
if [ -d "\$OUTDICT" ]; then
  open -R "\$OUTDICT"
fi
osascript -e 'display notification "转换完成，已在 Finder 中定位结果" with title "古汉语词典转换器"'
TMP
chmod +x "$TMPCMD"

osascript -e "tell application \"Terminal\" to do script \"bash '$TMPCMD'\""

# 给一点提示
osascript -e 'display dialog "已在新终端窗口开始转换。" & return & return & "进度会实时显示在终端中；完成后会自动在 Finder 中定位产出的 .dictionary。" & return & "(Apple Silicon Mac 需已安装 Rosetta 2)" buttons {"好"} default button "好"' 2>/dev/null

exit 0
