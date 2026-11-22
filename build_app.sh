#!/bin/bash
# 组装自包含的「苹果字典转换器.app」（原生 Aqua GUI，tkinter，无浏览器/无 web 服务）
#
# 用法：
#   ARCH=x86_64   bash build_app.sh → 苹果字典转换器（64位原生版）.app
#   ARCH=arm64    bash build_app.sh → 苹果字典转换器（Arm64版）.app
#   ARCH=universal bash build_app.sh → 苹果字典转换器（通用版）.app
#
# 三版都内置 Python + PyGlossary + 同一套 x86_64 DDK：
#   - 64 位原生版：Python x86_64，Intel Mac 上全程原生，无需 Rosetta
#   - Arm64 版：Python arm64，Apple Silicon 上 DDK 需 Rosetta 2 转译
#   - 通用版：两套 Python 都在，launcher 按本机架构自选；DDK 仍为 x86_64，
#     Apple Silicon 上需要 Rosetta 2，Intel Mac 上全程原生
#
# 依赖来源可用环境变量覆盖；覆盖只对单架构版生效，通用版用内置的两套固定路径。
#   PYTHON_SRC / VENV_SP / VENV_SHARE / DDK_SRC
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/src"

ARM_PY="/Users/zhouchenxi/.workbuddy/binaries/python/versions/3.13.12"
ARM_SP="/Users/zhouchenxi/.workbuddy/binaries/python/envs/default/lib/python3.13/site-packages"
ARM_SHARE="/Users/zhouchenxi/.workbuddy/binaries/python/envs/default/share/pyglossary"
X86_PY="/tmp/x86py/unpack/python"
X86_SP="$X86_PY/lib/python3.13/site-packages"
X86_SHARE="$X86_PY/share/pyglossary"
[ -d "$X86_SHARE" ] || X86_SHARE="$ARM_SHARE"

# ---- 按目标架构选标注与要打包的运行时列表 ----
ARCH="${ARCH:-$(uname -m)}"
case "$ARCH" in
  x86_64|amd64)
    VARIANT="64位原生版"
    BUNDLE_ID="cn.workbuddy.gudai-hanyu-converter.x86_64"
    NEEDS_ROSETTA="false"
    RUNTIMES="x86_64|$X86_PY|$X86_SP|$X86_SHARE|python3.13"
    if [ -n "${PYTHON_SRC:-}" ]; then RUNTIMES="x86_64|$PYTHON_SRC|${VENV_SP:-$X86_SP}|${VENV_SHARE:-$X86_SHARE}|python3.13"; fi
    CREDIT="64 位原生版：Python 与 DDK 均为 x86_64，在 Intel Mac 上无需 Rosetta。macOS 28 起可能失效。"
    ;;
  arm64|aarch64)
    VARIANT="Arm64版"
    BUNDLE_ID="cn.workbuddy.gudai-hanyu-converter.arm64"
    NEEDS_ROSETTA="true"
    RUNTIMES="arm64|$ARM_PY|$ARM_SP|$ARM_SHARE|python3.13"
    if [ -n "${PYTHON_SRC:-}" ]; then RUNTIMES="arm64|$PYTHON_SRC|${VENV_SP:-$ARM_SP}|${VENV_SHARE:-$ARM_SHARE}|python3.13"; fi
    CREDIT="Arm64 版：内置 Python 为 arm64，DDK 为 x86_64，在 Apple Silicon 上需要 Rosetta 2 转译。macOS 28 起可能失效。"
    ;;
  universal)
    VARIANT="通用版"
    BUNDLE_ID="cn.workbuddy.gudai-hanyu-converter.universal"
    NEEDS_ROSETTA="true"
    RUNTIMES="arm64|$ARM_PY|$ARM_SP|$ARM_SHARE|python3.13-arm64 x86_64|$X86_PY|$X86_SP|$X86_SHARE|python3.13-x86_64"
    CREDIT="通用版：内置 arm64 与 x86_64 两套 Python，启动时按本机架构自选。DDK 为 x86_64，Apple Silicon 上需要 Rosetta 2，Intel Mac 上全程原生。macOS 28 起可能失效。"
    ;;
  *)
    echo "错误：不支持的架构 ARCH=${ARCH}（只接受 x86_64 / arm64 / universal）"; exit 1
    ;;
esac

APP="$HERE/苹果字典转换器（${VARIANT}）.app"
DDK_SRC="${DDK_SRC:-$HERE/../gudai-hanyu-dicts/ddk_bin}"

if [ ! -d "$DDK_SRC" ] || [ ! -f "$DDK_SRC/build_dict.sh" ]; then
  echo "错误：DDK_SRC 缺少 build_dict.sh：$DDK_SRC"; exit 1
fi

# ---- 逐个校验运行时（先全查完再动手，别拷半个 bundle 才失败）----
for spec in $RUNTIMES; do
  want="$(echo "$spec" | cut -d'|' -f1)"
  pysrc="$(echo "$spec" | cut -d'|' -f2)"
  sp="$(echo "$spec" | cut -d'|' -f3)"
  share="$(echo "$spec" | cut -d'|' -f4)"
  for pair in "Python:$pysrc" "site-packages:$sp" "share:$share"; do
    label="${pair%%:*}"; dir="${pair#*:}"
    if [ ! -d "$dir" ]; then echo "错误：$want 的 $label 目录不存在：$dir"; exit 1; fi
  done
  if [ ! -x "$pysrc/bin/python3" ]; then echo "错误：$want 的 bin/python3 不可执行"; exit 1; fi
  if [ ! -d "$sp/pyglossary" ]; then echo "错误：$want 的 site-packages 里没有 pyglossary"; exit 1; fi
  if ! "$pysrc/bin/python3" -c "import tkinter, tkinter.ttk" >/dev/null 2>&1; then
    echo "错误：$want 的 Python 不含 tkinter/Tcl-Tk，无法构建原生 GUI"; exit 1
  fi
  got="$(lipo -info "$pysrc/bin/python3" 2>/dev/null | sed -n 's/.*architecture: //p' | tr -d ' ')"
  if [ "$got" != "$want" ]; then
    echo "错误：${pysrc}/bin/python3 架构是 ${got}，应为 ${want}"; exit 1
  fi
  echo "校验通过: $want ← $pysrc"
done

echo "构建 .app: ${APP} （${ARCH}，${VARIANT}）"
PYRM="$(echo "$RUNTIMES" | awk '{print $1}' | cut -d'|' -f2)/bin/python3"
"$PYRM" -c "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" "$APP" || true
for OLD in "$HERE/苹果字典转换器.app" "$HERE/古汉语词典转换器.app"; do
  "$PYRM" -c "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" "$OLD" || true
done
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/scripts" "$APP/Contents/Resources/ddk"
for spec in $RUNTIMES; do
  mkdir -p "$APP/Contents/Resources/$(echo "$spec" | cut -d'|' -f5)"
done

for spec in $RUNTIMES; do
  want="$(echo "$spec" | cut -d'|' -f1)"
  pysrc="$(echo "$spec" | cut -d'|' -f2)"
  sp="$(echo "$spec" | cut -d'|' -f3)"
  share="$(echo "$spec" | cut -d'|' -f4)"
  dest="$(echo "$spec" | cut -d'|' -f5)"
  echo "[运行时 $want] 复制 Python + PyGlossary → $dest ..."
  cp -R "$pysrc/." "$APP/Contents/Resources/$dest/"
  SP="$APP/Contents/Resources/$dest/lib/python3.13/site-packages"
  mkdir -p "$SP"
  for d in pyglossary pyglossary-5.4.2.dist-info lxml lxml-6.1.3.dist-info bs4 beautifulsoup4-4.15.0.dist-info soupsieve soupsieve-3.0.3.dist-info html5lib html5lib-1.1.dist-info webencodings webencodings-0.6.1.dist-info six.py six-1.17.0.dist-info openpyxl openpyxl-3.1.5.dist-info et_xmlfile et_xmlfile-2.0.0.dist-info charset_normalizer charset_normalizer-3.5.2.dist-info idna idna-3.20.dist-info certifi certifi-2026.7.22.dist-info requests requests-2.34.2.dist-info urllib3 urllib3-2.8.0.dist-info typing_extensions.py typing_extensions-4.16.0.dist-info biplist; do
    if [ -e "$sp/$d" ]; then cp -R "$sp/$d" "$SP/" 2>/dev/null || true; fi
  done
  mkdir -p "$APP/Contents/Resources/$dest/share"
  cp -R "$share" "$APP/Contents/Resources/$dest/share/pyglossary"
done

echo "[共享] 复制 DDK 编译工具 (x86_64) ..."
cp -R "$DDK_SRC/." "$APP/Contents/Resources/ddk/"
echo "[共享] 去掉 DDK 二进制中的 i386 切片（仅留 x86_64）..."
for f in "$APP/Contents/Resources/ddk"/*; do
  if [ -f "$f" ] && lipo -info "$f" >/dev/null 2>&1; then
    if lipo -info "$f" 2>/dev/null | grep -q "i386"; then
      if lipo -thin x86_64 -output "$f.tmp" "$f" 2>/dev/null; then mv "$f.tmp" "$f"; else rm -f "$f.tmp"; fi
    fi
  fi
done

echo "[共享] 复制脚本 / Info.plist / launcher / 图标 ..."
cp "$SRC/convert.py" "$SRC/wiktionary_parse.py" "$SRC/stardict_write.py" "$SRC/fix_dict_plist.sh" "$SRC/gui_app.py" "$APP/Contents/Resources/scripts/"
if [ -f "$SRC/app_icon_1024.png" ]; then cp "$SRC/app_icon_1024.png" "$APP/Contents/Resources/scripts/"; fi
cp "$SRC/launcher.sh" "$APP/Contents/MacOS/launcher.sh"
chmod +x "$APP/Contents/MacOS/launcher.sh"
cp "$SRC/Info.plist" "$APP/Contents/Info.plist"
if [ -f "$SRC/app.icns" ]; then cp "$SRC/app.icns" "$APP/Contents/Resources/app.icns"; fi

# ---- 打上架构标注 ----
PB=/usr/libexec/PlistBuddy
DISPLAY="苹果字典转换器（${VARIANT}）"
"$PB" -c "Set :CFBundleName $DISPLAY" \
      -c "Set :CFBundleDisplayName $DISPLAY" \
      -c "Set :CFBundleIdentifier $BUNDLE_ID" \
      "$APP/Contents/Info.plist"
for key in DictConverterVariant DictConverterRosettaRequired NSHumanReadableCopyright; do
  "$PB" -c "Delete :$key" "$APP/Contents/Info.plist" >/dev/null 2>&1 || true
done
"$PB" -c "Add :DictConverterVariant string $ARCH" \
      -c "Add :DictConverterRosettaRequired bool $NEEDS_ROSETTA" \
      -c "Add :NSHumanReadableCopyright string $CREDIT" \
      "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null

echo "=== 构建完成 ==="
echo "路径: ${APP}"
echo "架构: ${ARCH} | Rosetta: ${NEEDS_ROSETTA}"
du -sh "$APP" 2>/dev/null || true
