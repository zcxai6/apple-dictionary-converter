#!/bin/bash --noprofile --norc
# 苹果字典转换器 —— PKG 安装后处理脚本
#
# 参考 O4.pkg 的 postinstall 范例改写。$3 是安装目标卷（通常为 /），
# 用 "$3/Applications/..." 兼容装到非系统盘的情况。
#
# 与范例的差异：本工具不需要改名（.app 名即最终名），因此去掉 rm -rf/mv 步骤；
# 保留清隔离、补执行位、修属主、Gatekeeper 白名单、完成通知。

APP_NAME="苹果字典转换器.app"
SRC="$3/Applications/$APP_NAME"

# 1. 清除隔离属性（ad-hoc / 无签名包避免「已损坏」提示；属性跟文件走）
xattr -dr com.apple.quarantine "$SRC" 2>/dev/null || true

# 2. 补全内置命令行工具与启动脚本的执行权限（打包可能丢失 +x）
chmod +x "$SRC/Contents/MacOS/launcher.sh" 2>/dev/null || true
for exe in "$SRC"/Contents/Resources/python3.13*/bin/python3 \
           "$SRC"/Contents/Resources/python3.13*/bin/python3.13; do
  [ -f "$exe" ] && chmod +x "$exe" 2>/dev/null || true
done

# 3. 修正属主为 root:wheel（打包时非 root 可能导致属主错误）
chown -R root:wheel "$SRC" 2>/dev/null || true

# 4. 加入 Gatekeeper 白名单（ad-hoc 签名无稳定身份，双保险）
/usr/sbin/spctl --add "$SRC" 2>/dev/null || true

# 5. 安装完成提示
/usr/bin/osascript -e 'display notification "苹果字典转换器已安装完成，可以打开了" with title "苹果字典转换器"' 2>/dev/null || true

exit 0
