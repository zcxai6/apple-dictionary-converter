#!/bin/bash
# 用命令行把「苹果字典转换器.app」打成 macOS PKG 安装包。
#
# 用法：
#   bash packaging/build_pkg.sh [arm64|x86_64|universal] [输出.pkg]
#   例：bash packaging/build_pkg.sh arm64 ~/Desktop/苹果字典转换器-arm64.pkg
#
# 要点（见知识库 Bugs/2026-11-22-pkg-identifier-slash-code110.md）：
#   - package identifier 必须是无斜杠的普通反向域名，不能用带层级的 bundle ID，
#     否则安装时报 Code=110「BOM 不存在」。
#   - --install-location / 配合载荷里的 Applications/xxx.app；
#   - postinstall 用 $3 定位目标卷，兼容装到非系统盘。
#   - 不签名（ad-hoc / 无签名）：postinstall 里清隔离 + spctl --add 兜底。

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
APP_VARIANT="${1:-arm64}"
OUT="${2:-$REPO/苹果字典转换器-$APP_VARIANT.pkg}"

case "$APP_VARIANT" in
  arm64)     SRC_APP="$REPO/苹果字典转换器（Arm64版）.app" ;;
  x86_64)    SRC_APP="$REPO/苹果字典转换器（64位原生版）.app" ;;
  universal) SRC_APP="$REPO/苹果字典转换器（通用版）.app" ;;
  *) echo "错误：未知架构 $APP_VARIANT（只接受 arm64/x86_64/universal）"; exit 1 ;;
esac

[ -d "$SRC_APP" ] || { echo "错误：找不到 $SRC_APP，请先跑 build_app.sh"; exit 1; }
[ -f "$HERE/postinstall.sh" ] || { echo "错误：缺少 postinstall.sh"; exit 1; }
command -v pkgbuild >/dev/null || { echo "错误：系统缺少 pkgbuild"; exit 1; }

STAGE="$(mktemp -d /tmp/dictconv-pkg.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/root/Applications" "$STAGE/scripts"
cp -R "$SRC_APP" "$STAGE/root/Applications/苹果字典转换器.app"
# pkgbuild 只认固定文件名的钩子：postinstall（无 .sh 扩展名）。
# 单独建一个干净的 scripts 目录，别把 build_pkg.sh 等一起塞进去。
cp "$HERE/postinstall.sh" "$STAGE/scripts/postinstall"
chmod +x "$STAGE/scripts/postinstall"

# package identifier 用固定的无斜杠格式；版本取 App 的 CFBundleVersion
IDENT="cn.workbuddy.dictionaryconverter.pkg"
VER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$SRC_APP/Contents/Info.plist" 2>/dev/null || echo 1.1.0)"

echo ">>> 打包 $APP_VARIANT：$SRC_APP -> $OUT"
echo "    identifier=$IDENT version=$VER"
pkgbuild \
  --root "$STAGE/root" \
  --scripts "$STAGE/scripts" \
  --identifier "$IDENT" \
  --version "$VER" \
  --install-location / \
  --ownership recommended \
  "$OUT"

echo ">>> 完成：$OUT"
echo "    校验：pkgutil --payload-files \"$OUT\" | head"
echo "    展开：pkgutil --expand \"$OUT\" /tmp/inspect-pkg"
