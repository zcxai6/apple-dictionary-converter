#!/bin/bash
# 组装自包含的「苹果字典转换器.app」（原生 Aqua GUI，tkinter，无浏览器/无 web 服务）
# 本机已具备依赖；他人使用需先设置 PYTHON_SRC / VENV_SP / VENV_SHARE / DDK_SRC，
# 或自行安装 pyglossary 并准备 Apple Dictionary Development Kit (DDK)。
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/src"
APP="$HERE/苹果字典转换器.app"

# ---- 依赖来源（可用环境变量覆盖）----
PYTHON_SRC="${PYTHON_SRC:-/Users/zhouchenxi/.workbuddy/binaries/python/versions/3.13.12}"
VENV_SP="${VENV_SP:-/Users/zhouchenxi/.workbuddy/binaries/python/envs/default/lib/python3.13/site-packages}"
VENV_SHARE="${VENV_SHARE:-/Users/zhouchenxi/.workbuddy/binaries/python/envs/default/share/pyglossary}"
# DDK 默认取仓库同级目录（gudai-hanyu-dicts/ddk_bin），换机时可用 DDK_SRC 覆盖
DDK_SRC="${DDK_SRC:-$HERE/../gudai-hanyu-dicts/ddk_bin}"

for v in PYTHON_SRC VENV_SP VENV_SHARE DDK_SRC; do
  if [ ! -d "${!v}" ]; then
    echo "错误：$v 指向的目录不存在：${!v}"
    echo "请用环境变量指定正确路径后重试（例：DDK_SRC=/path/to/ddk/bin bash build_app.sh）。"
    exit 1
  fi
done
# 目录存在还不够，缺关键文件的话会在拷完半个 bundle 之后才失败
if [ ! -x "$PYTHON_SRC/bin/python3" ]; then
  echo "错误：PYTHON_SRC 中缺少可执行的 bin/python3：$PYTHON_SRC"; exit 1
fi
if [ ! -f "$DDK_SRC/build_dict.sh" ]; then
  echo "错误：DDK_SRC 中缺少 build_dict.sh：$DDK_SRC"; exit 1
fi
if [ ! -d "$VENV_SP/pyglossary" ]; then
  echo "错误：VENV_SP 中未找到 pyglossary：$VENV_SP"; exit 1
fi

echo "构建 .app: $APP"
# 清理旧构建：用 python 删除，避开 shell rm -rf 限制
PYRM="${PYTHON_SRC}/bin/python3"
"$PYRM" -c "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" "$APP" || true
# 兼容重命名前构建出的旧 bundle：一并清理，避免新旧两份 .app 并存
"$PYRM" -c "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" "$HERE/古汉语词典转换器.app" || true
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/scripts" "$APP/Contents/Resources/ddk" "$APP/Contents/Resources/python3.13"

echo "[1/5] 复制 Python 运行时 ..."
cp -R "$PYTHON_SRC/." "$APP/Contents/Resources/python3.13/"

echo "[2/5] 复制 PyGlossary 及其依赖 ..."
SP="$APP/Contents/Resources/python3.13/lib/python3.13/site-packages"
mkdir -p "$SP"
for d in pyglossary pyglossary-5.4.2.dist-info lxml lxml-6.1.3.dist-info bs4 beautifulsoup4-4.15.0.dist-info soupsieve soupsieve-3.0.3.dist-info html5lib html5lib-1.1.dist-info webencodings webencodings-0.6.1.dist-info six.py six-1.17.0.dist-info openpyxl openpyxl-3.1.5.dist-info et_xmlfile et_xmlfile-2.0.0.dist-info charset_normalizer charset_normalizer-3.5.2.dist-info idna idna-3.20.dist-info certifi certifi-2026.7.22.dist-info requests requests-2.34.2.dist-info urllib3 urllib3-2.8.0.dist-info typing_extensions.py typing_extensions-4.16.0.dist-info biplist; do
  if [ -e "$VENV_SP/$d" ]; then
    cp -R "$VENV_SP/$d" "$SP/" 2>/dev/null || true
  fi
done

echo "[3/5] 复制 PyGlossary 数据 (share/pyglossary) ..."
mkdir -p "$APP/Contents/Resources/python3.13/share"
cp -R "$VENV_SHARE" "$APP/Contents/Resources/python3.13/share/pyglossary"

echo "[4/5] 复制 DDK 编译工具 ..."
cp -R "$DDK_SRC/." "$APP/Contents/Resources/ddk/"

echo "[4.5/5] 去掉 DDK 二进制中的 i386 切片（仅留 x86_64，Rosetta 2 仅需 64 位）..."
for f in "$APP/Contents/Resources/ddk"/*; do
  if [ -f "$f" ] && lipo -info "$f" >/dev/null 2>&1; then
    if lipo -info "$f" 2>/dev/null | grep -q "i386"; then
      if lipo -thin x86_64 -output "$f.tmp" "$f" 2>/dev/null; then
        mv "$f.tmp" "$f"
      else
        rm -f "$f.tmp"
      fi
    fi
  fi
done

echo "[5/5] 复制脚本 / Info.plist / launcher / 图标 ..."
cp "$SRC/convert.py" "$SRC/wiktionary_parse.py" "$SRC/stardict_write.py" "$SRC/fix_dict_plist.sh" "$SRC/gui_app.py" "$APP/Contents/Resources/scripts/"
# 窗口/Dock 图标（gui_app.py 从脚本目录读取 app_icon_1024.png）
if [ -f "$SRC/app_icon_1024.png" ]; then
  cp "$SRC/app_icon_1024.png" "$APP/Contents/Resources/scripts/"
else
  echo "提示：未找到 src/app_icon_1024.png，窗口图标将不可用"
fi
# 构建时校验：打包的 Python 必须带 tkinter / Tcl-Tk，否则 GUI 一启动就崩
if ! "$PYRM" -c "import tkinter, tkinter.ttk" >/dev/null 2>&1; then
  echo "错误：PYTHON_SRC 中的 Python 不含 tkinter/Tcl-Tk，无法构建原生 GUI 应用。"
  echo "请使用完整 CPython（含 _tkinter 与 libtcl/libtk）。"
  exit 1
fi
cp "$SRC/launcher.sh" "$APP/Contents/MacOS/launcher.sh"
chmod +x "$APP/Contents/MacOS/launcher.sh"
cp "$SRC/Info.plist" "$APP/Contents/Info.plist"
# App 图标（由 src/make_icon.py 生成：词 → 词典）
if [ -f "$SRC/app.icns" ]; then
  cp "$SRC/app.icns" "$APP/Contents/Resources/app.icns"
else
  echo "提示：未找到 src/app.icns，可运行 src/make_icon.py 生成"
fi

# 去掉本机绝对路径痕迹（若有）
echo "完成：双击「$APP」即可使用"
du -sh "$APP" 2>/dev/null || true
