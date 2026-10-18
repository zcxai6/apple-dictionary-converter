#!/bin/bash
# 古汉语词典转换器 —— .app 入口
# 设计：做完 Rosetta 前置检查后，直接用「应用内置的 Python」拉起图形界面服务，
# 本进程即为服务进程（exec），会一直存活到用户在界面点「退出」，
# 所有转换工作都在应用内部完成，绝不弹出终端。

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTENTS="$(dirname "$SCRIPT_DIR")"
RESOURCES="$CONTENTS/Resources"
SCRIPTS="$RESOURCES/scripts"
PY="$RESOURCES/python3.13/bin/python3"
GUI="$SCRIPTS/gui_server.py"

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

if [ ! -x "$PY" ]; then
  osascript -e 'display dialog "未找到内置 Python 运行时，应用可能不完整。请重新构建 .app。" buttons {"好"} default button "好" with title "启动失败"'
  exit 1
fi

# ---------- 直接以内置 Python 运行图形界面（进程不退出，直到用户退出）----------
exec "$PY" "$GUI"
