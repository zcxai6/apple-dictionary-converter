# 古汉语词典转换器（macOS）

一个**独立、双击即用**的 macOS 应用，把你手头的古汉语词典源文件转换成系统「词典」App 能直接打开的原生格式（`.dictionary`）。

不用再装 PyGlossary、不用再配置 Apple DDK、也不用忍受 DictUnifier 一导入压缩包就崩的毛病——
本应用已经把 **Python 运行时 + PyGlossary + Apple DDK 编译器** 全部打包在内，并修好了之前踩过的所有坑。

## 它能做什么

把一个源文件转换成 macOS 原生词典：

| 源文件 | 说明 |
| --- | --- |
| `*.bz2` | 中文维基词典全量 dump（`zhwiktionary-*-pages-articles.xml.bz2`），抽取带文言标记的义项 → 古汉语→现代汉语 |
| `*.ifo` | 标准 StarDict（`.ifo` + `.idx` + `.dict`），直接编译为 Apple 格式 |
| `*.csv` | [dictkit/data-dicts](https://github.com/dictkit/data-dicts) 风格的 CSV（如《古代汉语词典》《古汉语常用字字典》） |

转换链路：`源文件 → StarDict（中间格式）→ AppleDict 工程 → DDK 编译 .dictionary → 修正 plist`。

转换成功后，输出目录里**只保留最终产物 `名称.dictionary`**——中间的 StarDict 源和 AppleDict 编译工程都会被自动删除（`/bin/rm -rf`），不会出现两个长得差不多的目录让你分不清哪个能用。**带 `.dictionary` 后缀的那个就是要用的**，直接拖进 `~/Library/Dictionaries/` 即可。

## 使用方法（内置图形界面）

双击 `古汉语词典转换器.app` 后，**应用内部自带一个图形界面**会自动打开（本地页面，不联网、文件不出本机）。
全部转换工作都在应用内部完成，**不会弹出终端**；应用会一直运行到你在界面点「退出」。

界面功能：
- **拖入文件或点击选择**：支持 `.bz2`（维基）/ `.csv`（dictkit）/ `.ifo` + `.idx` + `.dict`（StarDict，可多选一起拖入）。
- **转换模式**：默认「自动识别」（按扩展名），也可手动指定。
- **输出名称 / 输出目录 / 是否安装到系统词典目录**：按需填写。
- **实时进度**：点击「开始转换」后，下方日志框实时滚动显示每一步（PyGlossary → DDK 编译 → 修正 plist）。
- **完成定位**：转换结束后给出最终 `*.dictionary` 路径，一键「在 Finder 中显示」。
- **关于 / 高级**：
  - 显示内置 Python 版本与组件路径；
  - 「在 Finder 打开脚本目录」；
  - **内置「脚本编辑器」**：可直接查看/修改转换脚本（convert.py 等），保存即写回应用内（改坏可能导致转换失败，请谨慎）。

> 若双击提示「无法打开」：在 Finder 中右键 → 打开，或执行
> `xattr -dr com.apple.quarantine "古汉语词典转换器.app"` 解除隔离。

> **Apple Silicon 用户**：DDK 编译器是 x86 程序，需要 **Rosetta 2**。
> 若未安装，应用会弹出提示，按提示在终端执行 `softwareupdate --install-rosetta` 即可。
> （本机若曾用 DictUnifier，说明 Rosetta 早已就绪。）

## 命令行用法

高级用户可直接调用内部脚本：

```bash
APP="古汉语词典转换器.app"
PY="$APP/Contents/Resources/python3.13/bin/python3"
CONVERT="$APP/Contents/Resources/scripts/convert.py"

# 维基 BZ2
"$PY" "$CONVERT" wiktionary 路径/zhwiktionary-*.xml.bz2 输出目录 --name 维基文言文词典

# 已有 StarDict
"$PY" "$CONVERT" stardict 路径/xxx.ifo 输出目录 --name 我的词典

# dictkit 系 CSV
"$PY" "$CONVERT" csv 路径/古代汉语词典（第2版）.csv 输出目录 "古代汉语词典（第2版）" --name "古代汉语词典（第2版）" --install
```

常用参数：`--name 名称`（指定输出名）、`--install`（装到系统词典目录）、`--keep-src`（保留全部中间产物：StarDict 源与 AppleDict 编译工程，供调试）。

## 从源码重建本应用

仓库内的 `古汉语词典转换器.app` 已可直接使用。如需在自己机器上重新打包：

```bash
bash build_app.sh
```

脚本会从本机已有的 Python + PyGlossary、Apple DDK 路径收集并复制（可用环境变量 `PYTHON_SRC` / `VENV_SP` / `VENV_SHARE` / `DDK_SRC` 覆盖来源），组装成新的 `.app`。
**打包产物完全自包含**，目标机器无需预装 Python / PyGlossary / DDK。

### App 图标

图标设计取「词典」的「**词**」字，用一个箭头把它变成「**词典**」——正是本工具做的事。
由 `src/make_icon.py`（Pillow + 宋体）生成 1024px 母图，再用 `sips` + `iconutil` 合成 `app.icns`：

```bash
pip install Pillow
python3 src/make_icon.py src   # 重新生成 src/app.icns（build_app.sh 会自动打包进 .app）
```

## 致谢与引用（第三方项目）

本应用站在以下开源/公开项目肩上，特此致谢并标注来源与许可：

| 项目 | 用途 | 许可 | 来源 |
| --- | --- | --- | --- |
| **PyGlossary** | 词典格式转换核心（StarDict ↔ AppleDict 工程） | GPL-3.0 | https://github.com/ilius/pyglossary |
| **Apple Dictionary Development Kit (DDK)** | 把 AppleDict 工程编译成 `.dictionary` | Apple 专有（随 Xcode / DictUnifier 分发） | 取自 DictUnifier.app 自带 DDK |
| **DictUnifier** | 其打包的 DDK 工具链被本应用沿用 | 见其仓库 | https://github.com/ctg/DictUnifier |
| **dictkit/data-dicts** | 提供《古代汉语词典（第2版）》《古汉语常用字字典（第5版）》等 CSV 数据 | 仓库未声明显式许可证；数据版权归原词典编者/出版机构，整理自网络，仅供个人学习 | https://github.com/dictkit/data-dicts |
| **中文维基词典 (zh.wiktionary.org)** | `*.bz2` 源数据 | CC BY-SA 4.0 | https://zh.wiktionary.org |
| **Python 3.13 + 依赖**（lxml, beautifulsoup4, html5lib, openpyxl 等） | 运行时与解析 | PSF / BSD / MIT 等各自许可 | https://www.python.org |

> ⚠️ **关于 DDK 二进制**：本应用内 `Contents/Resources/ddk/` 下的 x86 编译器来自 DictUnifier 自带的 Apple DDK。
> 这些二进制版权归 Apple，仅用于个人/教育用途；如你已自行安装 Apple 官方 Dictionary Development Kit，
> 可用环境变量 `DICT_BUILD_TOOL_BIN` 指向你自己的 DDK `bin` 目录来替换。

## 许可证

本应用自身的打包脚本与转换逻辑以 **MIT** 许可证发布（见 `LICENSE`）。
所打包的第三方组件（PyGlossary / DDK / 词典数据）遵循其各自的许可证与版权声明，详见上方表格与 `NOTICE`。

## 免责声明

所有词典数据版权归原作者/出版机构所有。本工具仅供**个人学习研究**使用，请勿用于商业分发。
