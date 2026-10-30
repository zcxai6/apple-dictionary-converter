#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
苹果字典转换器 —— 原生图形界面（tkinter / Aqua）

不依赖任何浏览器、不启动 web 服务。所有转换都在本进程内的子进程中完成，
文件不会离开本机。支持：拖文件到程序图标 / 选择文件、自动模式（解包压缩包并
识别格式）、完成后在 Finder 定位、内置脚本编辑器。日志默认隐藏，点「显示日志」可展开。
"""

import os
import sys
import json
import queue
import signal
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES = os.path.dirname(SCRIPT_DIR)
CONVERT = os.path.join(SCRIPT_DIR, "convert.py")
SCRIPTS_DIR = SCRIPT_DIR
PY = os.environ.get("CONVERTER_PYTHON") or os.path.join(RESOURCES, "python3.13", "bin", "python3")
ICON_PNG = os.path.join(SCRIPT_DIR, "app_icon_1024.png")

SUPPORTED_EXT = (".tar.bz2", ".tar.gz", ".tgz", ".tar.xz", ".tar", ".zip",
                  ".bz2", ".csv", ".ifo", ".idx", ".dict", ".xml")

ARCHIVE_SUFFIXES = (".tar.bz2", ".tar.gz", ".tgz", ".tar.xz", ".tar", ".zip")

# 这些信号 = 被外部中止（退出/注销/合盖/手动杀），与进程自身崩溃区分开
EXTERNAL_SIGNALS = {signal.SIGHUP, signal.SIGTERM, signal.SIGKILL, signal.SIGINT}


def _is_archive(path):
    return path.lower().endswith(ARCHIVE_SUFFIXES)


def _default_name(path):
    """从文件名推导词典名：先剥掉多段压缩包后缀（foo.tar.bz2 -> foo），再退回 splitext。"""
    base = os.path.basename(os.path.normpath(path))
    low = base.lower()
    for suf in ARCHIVE_SUFFIXES:
        if low.endswith(suf):
            return base[: -len(suf)] or base
    return os.path.splitext(base)[0] or base


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("苹果字典转换器")
        try:
            self.root.tk.call("tk", "useinputmethods", 1)
        except Exception:
            pass
        self.root.option_add("*tearOff", False)
        self.root.minsize(680, 560)

        # 窗口图标（macOS 标题栏/ Dock）
        if os.path.isfile(ICON_PNG):
            try:
                img = tk.PhotoImage(file=ICON_PNG)
                img = img.subsample(max(1, img.width() // 64))
                self.root.iconphoto(True, img)
            except Exception:
                pass

        self.files = []          # 选中的源文件
        self.mode = tk.StringVar(value="auto")
        self.running = False
        self.log_q = queue.Queue()
        self.proc = None

        self._build_menu()
        self._build_ui()
        self._poll_log()

        # 拖文件到程序图标启动：argv 里带进来的真实文件路径
        dropped = [a for a in sys.argv[1:] if os.path.exists(a)]
        if dropped:
            self.add_files(dropped)

    # ---------- 菜单 ----------
    def _build_menu(self):
        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar)
        m_file.add_command(label="退出", command=self.root.quit)
        menubar.add_cascade(label="文件", menu=m_file)

        m_adv = tk.Menu(menubar)
        m_adv.add_command(label="编辑转换脚本…", command=self.open_script_editor)
        m_adv.add_command(label="打开脚本目录", command=self.open_scripts_dir)
        m_adv.add_separator()
        m_adv.add_command(label="关于 Python 运行时…", command=self.about_python)
        menubar.add_cascade(label="高级", menu=m_adv)

        m_help = tk.Menu(menubar)
        m_help.add_command(label="使用说明", command=self.show_help)
        m_help.add_command(label="关于本程序", command=self.about_app)
        menubar.add_cascade(label="帮助", menu=m_help)

        self.root.config(menu=menubar)

    # ---------- 主界面 ----------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}
        f = ttk.Frame(self.root)
        f.pack(fill="both", expand=True, padx=10, pady=10)

        # 标题行
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="苹果字典转换器", font=("Songti SC", 18, "bold")).pack(side="left")
        ttk.Label(top, text="源文件 → macOS 原生 .dictionary", foreground="#666").pack(side="left", padx=8)

        # 源文件
        row = ttk.Frame(f)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="源文件：", width=9).pack(side="left")
        self.file_var = tk.StringVar(value="（未选择）")
        ttk.Label(row, textvariable=self.file_var, relief="sunken", anchor="w").pack(side="left", fill="x", expand=True, ipady=3)
        ttk.Button(row, text="选择文件…", command=self.choose_files).pack(side="left", padx=6)
        ttk.Button(row, text="清除", command=self.clear_files).pack(side="left")

        hint = ttk.Label(f, text="提示：可直接把文件拖到 Dock/程序图标上打开；或点「选择文件」。", foreground="#888", font=("", 10))
        hint.pack(anchor="w")

        # 模式
        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="转换模式：", width=9).pack(side="left")
        self.mode_cb = ttk.Combobox(row, textvariable=self.mode, state="readonly", width=42,
                                    values=["auto", "wiktionary", "csv", "stardict"])
        self.mode_cb.pack(side="left")
        ttk.Label(row, text="（auto = 自动解包并识别格式，推荐）", foreground="#888").pack(side="left", padx=6)

        # 词典名称
        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="词典名称：", width=9).pack(side="left")
        self.name_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.name_var).pack(side="left", fill="x", expand=True)

        # 输出目录
        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="输出目录：", width=9).pack(side="left")
        self.out_var = tk.StringVar(value=os.path.expanduser("~/Desktop"))
        ttk.Entry(row, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="浏览…", command=self.choose_outdir).pack(side="left")

        # 选项
        row = ttk.Frame(f)
        row.pack(fill="x", pady=2)
        self.install_var = tk.BooleanVar()
        ttk.Checkbutton(row, text="转换完成后安装到系统词典目录（~/Library/Dictionaries）", variable=self.install_var).pack(side="left")

        # 按钮行
        row = ttk.Frame(f)
        row.pack(fill="x", pady=8)
        self.start_btn = ttk.Button(row, text="开始转换", command=self.start_convert)
        self.start_btn.pack(side="left", ipadx=12, ipady=4)
        self.finder_btn = ttk.Button(row, text="在 Finder 中显示", command=self.open_last, state="disabled")
        self.finder_btn.pack(side="left", padx=10)
        self.last_out = None

        # 进度条 + 日志开关（日志默认隐藏，点「显示日志」才展开）
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(8, 0))
        self.toggle_log_btn = ttk.Button(row, text="显示日志 ▸", command=self.toggle_log, width=11)
        self.toggle_log_btn.pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(row, textvariable=self.status_var, foreground="#666").pack(side="right")

        self.prog = ttk.Progressbar(f, mode="indeterminate", maximum=100)
        self.prog.pack(fill="x", pady=(6, 0))

        # 日志（默认不 pack，隐藏）
        self.log_frame = ttk.Frame(f)
        self.log = scrolledtext.ScrolledText(self.log_frame, height=12, wrap="word", font=("Menlo", 11))
        self.log.pack(fill="both", expand=True)
        self.log.config(state="disabled")
        self.log.bind("<Button-2>", self._log_menu)
        self.log.bind("<Control-Button-1>", self._log_menu)
        self.log_visible = False

    # ---------- 文件选择 ----------
    def add_files(self, paths):
        for p in paths:
            if os.path.exists(p) and p not in self.files:
                self.files.append(p)
        self._refresh_files()
        # 默认名称从首个文件/目录名推断（模式保持「自动」，由引擎内部识别/解包）
        if not self.name_var.get() and self.files:
            self.name_var.set(_default_name(self.files[0]))

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="选择词典源文件",
            filetypes=[("支持的词典文件", " ".join("*" + e for e in SUPPORTED_EXT)),
                       ("全部文件", "*.*")])
        if paths:
            self.add_files(list(paths))

    def clear_files(self):
        self.files = []
        self._refresh_files()

    def _refresh_files(self):
        if not self.files:
            self.file_var.set("（未选择）")
        else:
            self.file_var.set("；".join(os.path.basename(p) for p in self.files))

    def choose_outdir(self):
        d = filedialog.askdirectory(title="选择输出目录", initialdir=self.out_var.get())
        if d:
            self.out_var.set(d)

    # ---------- 转换 ----------
    def start_convert(self):
        if self.running:
            return
        if not self.files:
            messagebox.showerror("缺少源文件", "请先选择要转换的源文件。")
            return
        mode = self.mode.get()
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("缺少名称", "请填写词典名称（即产出 .dictionary 的文件夹名）。")
            return
        outdir = self.out_var.get().strip()
        if not outdir:
            messagebox.showerror("缺少输出目录", "请选择输出目录。")
            return
        os.makedirs(outdir, exist_ok=True)

        cmd = self._build_cmd(mode, name, outdir)
        if not cmd:
            return
        self.running = True
        self.start_btn.config(state="disabled")
        self.finder_btn.config(state="disabled")
        self.last_out = None
        self.status_var.set("转换中…")
        self.prog.config(mode="indeterminate")
        self.prog["value"] = 0
        self.prog.start(25)
        self._log("=" * 48)
        self._log("命令：%s" % " ".join(cmd))
        self._log("=" * 48)
        threading.Thread(target=self._run, args=(cmd,), daemon=True).start()

    def _build_cmd(self, mode, name, outdir):
        install = ["--install"] if self.install_var.get() else []
        if mode == "auto":
            if not self.files:
                return None
            # 多选时不能盲取 files[0]（可能是 .dict/.idx），挑引擎真正认得的输入
            ranked = sorted(self.files, key=lambda f: (
                0 if f.lower().endswith(".ifo") else
                1 if _is_archive(f) else
                2 if f.lower().endswith(".csv") else
                3 if f.lower().endswith(".bz2") else
                4 if os.path.isdir(f) else 5))
            src = ranked[0]
            return [sys.executable, CONVERT, "auto", src, outdir, "--name", name] + install
        if mode == "wiktionary":
            # 注意：StarDict 的 .tar.bz2 也以 .bz2 结尾，不能当成维基 dump
            bz2 = next((f for f in self.files
                        if f.lower().endswith(".bz2") and not _is_archive(f)), None)
            if not bz2:
                messagebox.showerror("缺少 .bz2",
                                     "维基模式需要 .bz2 dump 文件（不是 StarDict 压缩包）。")
                return None
            return [sys.executable, CONVERT, "wiktionary", bz2, outdir, "--name", name] + install
        if mode == "csv":
            csvf = next((f for f in self.files if f.lower().endswith(".csv")), None)
            if not csvf:
                messagebox.showerror("缺少 .csv", "CSV 模式需要一个 .csv 文件。")
                return None
            return [sys.executable, CONVERT, "csv", csvf, outdir, name, "--name", name] + install
        if mode == "stardict":
            ifo = next((f for f in self.files if f.lower().endswith(".ifo")), None)
            if not ifo:
                messagebox.showerror("缺少 .ifo", "StarDict 模式需要 .ifo 文件（.idx/.dict 需与其同目录）。")
                return None
            return [sys.executable, CONVERT, "stardict", ifo, outdir, "--name", name] + install
        return None

    def _run(self, cmd):
        proc = None
        try:
            env = dict(os.environ)
            env["DICT_BUILD_TOOL_BIN"] = os.path.join(RESOURCES, "ddk")
            # 关键修复：start_new_session=True 让转换子进程自成会话、脱离主程序的
            # 进程组/控制终端。否则 Finder 启动的 .app 在会话变化（退出/注销/合盖睡眠）
            # 时会向该进程组发 SIGHUP，把正在跑的长时间编译（PyGlossary+DDK）一并杀掉，
            # 表现为 GUI 收到「返回码 -1」。脱离会话后即可独立跑完。
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, universal_newlines=True, env=env,
                start_new_session=True)
            self.proc = proc
            for line in proc.stdout:
                self.log_q.put(line.rstrip("\n"))
            rc = proc.wait()
            self.log_q.put(("__RC__", rc))
        except Exception as e:
            self.log_q.put("【异常】%s" % e)
            # 读日志阶段出错时，子进程可能仍在编译：必须终止并回收，
            # 否则它变成没人读 stdout 的孤儿进程，撑满管道后卡死；
            # 而且用户能再点「开始转换」，造成同目录并发编译互相踩踏。
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            # -9999 = 本程序侧异常（与「被信号杀死」区分开）
            self.log_q.put(("__RC__", -9999))

    def _poll_log(self):
        try:
            while True:
                item = self.log_q.get_nowait()
                if isinstance(item, tuple) and item[0] == "__RC__":
                    self._on_done(item[1])
                else:
                    self._log(item)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_log)

    def _log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _last_log_lines(self, n=8):
        """取日志末尾若干非空行，用于失败弹窗的诊断信息。"""
        try:
            txt = self.log.get("1.0", "end")
            lines = [l for l in txt.splitlines() if l.strip()]
            return "\n".join(lines[-n:]) if lines else "（无日志）"
        except Exception:
            return ""

    def _on_done(self, rc):
        self.running = False
        self.start_btn.config(state="normal")
        self.prog.stop()
        self.prog.config(mode="determinate")
        self.prog["value"] = 100 if rc == 0 else 0
        # 失败时自动展开日志，否则「详见转换日志」指向一个隐藏面板没意义
        if rc != 0 and not self.log_visible:
            self.toggle_log()
        if rc == 0:
            self.status_var.set("完成 ✓")
            self._log("\n✅ 转换成功。")
            # 尝试定位最终产物
            name = self.name_var.get().strip()
            cand = os.path.join(self.out_var.get().strip(), name + ".dictionary")
            if os.path.isdir(cand):
                self.last_out = cand
                self.finder_btn.config(state="normal")
                self._log("最终产物：%s" % cand)
        else:
            self.status_var.set("失败 ✗")
            self._log("\n❌ 转换失败（返回码 %s）。" % rc)
            detail = self._last_log_lines(8)
            if rc == -9999:
                msg = (
                    "转换进程启动或运行异常（Python 运行时或脚本路径有误，"
                    "或读取进程输出时中断）。\n\n"
                    "日志末尾：\n%s"
                ) % detail
            elif rc < 0:
                sig = -rc
                try:
                    signame = signal.Signals(sig).name
                except Exception:
                    signame = "信号 %d" % sig
                if sig in EXTERNAL_SIGNALS:
                    msg = (
                        "转换进程被系统信号终止：%s（返回码 %d）。\n\n"
                        "这通常不是程序 bug，而是转换进程被外部杀掉了，常见原因：\n"
                        "  • 转换过程中退出了本程序 / 注销了账户\n"
                        "  • 笔记本合盖或系统睡眠打断了长时间编译\n"
                        "  • 系统资源不足（内存/CPU 限额）被系统杀掉\n\n"
                        "注意：大词典编译本来就极慢——5MB 的词典 PyGlossary 一步就要约 8 分钟，"
                        "维基全量可能数小时。转换期间请勿退出程序、勿合盖睡眠。\n\n"
                        "日志末尾：\n%s"
                    ) % (signame, rc, detail)
                else:
                    msg = (
                        "转换进程崩溃：%s（返回码 %d）。\n\n"
                        "这是转换过程自身崩溃（多发生在 DDK / perl / PyGlossary 阶段），"
                        "而不是被外部中止。请按日志末尾定位到具体步骤。\n\n"
                        "日志末尾：\n%s"
                    ) % (signame, rc, detail)
            else:
                msg = (
                    "转换失败（返回码 %d）。一般为源文件格式不支持或编译出错。\n\n"
                    "日志末尾：\n%s"
                ) % (rc, detail)
            messagebox.showerror("转换失败", msg)

    def open_last(self):
        if self.last_out:
            subprocess.run(["open", "-R", self.last_out])

    # ---------- 日志右键 ----------
    def _log_menu(self, event):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="复制", command=self._copy_log)
        m.add_command(label="全选", command=lambda: self.log.tag_add("sel", "1.0", "end"))
        m.add_separator()
        m.add_command(label="清空日志", command=lambda: (self.log.config(state="normal"), self.log.delete("1.0", "end"), self.log.config(state="disabled")))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _copy_log(self):
        try:
            txt = self.log.get("sel.first", "sel.last")
        except tk.TclError:
            return
        if txt:
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)

    def toggle_log(self):
        if self.log_visible:
            self.log_frame.pack_forget()
            self.toggle_log_btn.config(text="显示日志 ▸")
        else:
            self.log_frame.pack(fill="both", expand=True, pady=(6, 0))
            self.toggle_log_btn.config(text="隐藏日志 ▾")
        self.log_visible = not self.log_visible

    # ---------- 高级功能 ----------
    def open_scripts_dir(self):
        subprocess.run(["open", SCRIPTS_DIR])

    def about_python(self):
        try:
            ver = subprocess.check_output([PY, "--version"], stderr=subprocess.STDOUT).decode().strip()
        except Exception as e:
            ver = "（无法获取：%s）" % e
        messagebox.showinfo("Python 运行时", "本程序自带完整 Python 运行时，无需在目标机器预装。\n\n版本：%s\n路径：%s" % (ver, PY))

    def open_script_editor(self):
        win = tk.Toplevel(self.root)
        win.title("编辑转换脚本")
        win.minsize(640, 480)
        files = sorted([f for f in os.listdir(SCRIPTS_DIR) if f.endswith(".py")])
        ctrl = ttk.Frame(win)
        ctrl.pack(fill="x", padx=8, pady=6)
        ttk.Label(ctrl, text="文件：").pack(side="left")
        fvar = tk.StringVar(value=files[0] if files else "")
        cb = ttk.Combobox(ctrl, textvariable=fvar, values=files, state="readonly", width=28)
        cb.pack(side="left", padx=6)

        txt = scrolledtext.ScrolledText(win, wrap="none", font=("Menlo", 12))
        txt.pack(fill="both", expand=True, padx=8, pady=4)

        def load():
            p = os.path.join(SCRIPTS_DIR, fvar.get())
            try:
                txt.delete("1.0", "end")
                txt.insert("end", open(p, encoding="utf-8").read())
            except Exception as e:
                messagebox.showerror("读取失败", str(e))

        def save():
            p = os.path.join(SCRIPTS_DIR, fvar.get())
            if not messagebox.askyesno("确认保存", "将覆盖应用内的 %s，可能影响转换功能。确定？" % fvar.get()):
                return
            try:
                open(p, "w", encoding="utf-8").write(txt.get("1.0", "end"))
                messagebox.showinfo("已保存", "已写回：%s" % p)
            except Exception as e:
                messagebox.showerror("保存失败", str(e))

        btn = ttk.Frame(win)
        btn.pack(fill="x", padx=8, pady=6)
        ttk.Button(btn, text="载入", command=load).pack(side="left")
        ttk.Button(btn, text="保存", command=save).pack(side="left", padx=8)
        ttk.Label(btn, text="（改坏可能导致转换失败）", foreground="#a33").pack(side="left", padx=10)
        load()

    # ---------- 帮助/关于 ----------
    def show_help(self):
        messagebox.showinfo("使用说明",
            "1. 选择源文件：维基 dump（.bz2）/ dictkit CSV（.csv）/ StarDict（.ifo 或完整 .tar.bz2 包）。\n"
            "   也可把文件直接拖到 Dock / 程序图标上打开（应用未运行时拖入才会被接收）。\n"
            "2. 模式保持「auto」即可：程序会自动解包压缩包、识别格式（维基/CSV/StarDict）再转换。\n"
            "   高级用户也可手动选 wiktionary / csv / stardict。CSV 用「词典名称」作为书名。\n"
            "3. 填「词典名称」（即产出 .dictionary 的文件夹名）与「输出目录」。\n"
            "4. 可选：勾选安装到系统词典目录（勾选后才会真正写入 ~/Library/Dictionaries）。\n"
            "5. 点「开始转换」，等待日志显示完成，再点「在 Finder 中显示」。\n\n"
            "Apple Silicon 需已安装 Rosetta 2（DDK 编译器为 x86）。大词典编译很慢，转换中勿退出/合盖。")

    def about_app(self):
        messagebox.showinfo("关于",
            "苹果字典转换器\n\n"
            "把词典源文件（维基 .bz2 / dictkit .csv / StarDict）转换成 macOS 原生词典（.dictionary）。\n"
            "基于 PyGlossary + Apple Dictionary Development Kit。\n"
            "详见仓库 README / NOTICE。")

    def mainloop(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().mainloop()
