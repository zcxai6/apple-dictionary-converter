#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
苹果字典转换器 —— 原生图形界面（tkinter / Aqua）

不依赖任何浏览器、不启动 web 服务。所有转换都在本进程内的子进程中完成，
文件不会离开本机。支持：拖文件到程序图标 / 选择文件（可多选，批量顺序转换）、
自动模式（解包压缩包并识别格式）、选中即预检类型、进度与耗时、完成后在 Finder
定位或直接打开「词典」App、内置脚本编辑器。日志默认隐藏，点「显示日志」可展开。
"""

import os
import sys
import json
import queue
import signal
import threading
import subprocess
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES = os.path.dirname(SCRIPT_DIR)
CONVERT = os.path.join(SCRIPT_DIR, "convert.py")
SCRIPTS_DIR = SCRIPT_DIR
PY = os.environ.get("CONVERTER_PYTHON") or os.path.join(RESOURCES, "python3.13", "bin", "python3")
ICON_PNG = os.path.join(SCRIPT_DIR, "app_icon_1024.png")

SETTINGS_DIR = os.path.expanduser("~/Library/Application Support/苹果字典转换器")
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

SUPPORTED_EXT = (".tar.bz2", ".tar.gz", ".tgz", ".tar.xz", ".tar", ".zip",
                  ".bz2", ".csv", ".txt", ".tsv", ".tab", ".ifo", ".idx", ".dict",
                  ".xml")

ARCHIVE_SUFFIXES = (".tar.bz2", ".tar.gz", ".tgz", ".tar.xz", ".tar", ".zip")
TEXT_SUFFIXES = (".txt", ".tsv", ".tab")
# StarDict 的组成件：选了同名 .ifo 时，这些不应各自变成一个任务
COMPANION_SUFFIXES = (".idx", ".dict", ".syn", ".dict.dz", ".idx.gz")

JOB_MODE_LABEL = {
    "auto": "自动",
    "wiktionary": "维基 dump",
    "csv": "CSV",
    "tabfile": "两列文本",
    "stardict": "StarDict",
    "appledict": "AppleDict 工程",
}


def _is_archive(path):
    return path.lower().endswith(ARCHIVE_SUFFIXES)


def _default_name(path):
    """从文件名推导词典名：先剥掉多段压缩包后缀（foo.tar.bz2 -> foo），再退回 splitext。"""
    base = os.path.basename(os.path.normpath(path))
    low = base.lower()
    for suf in ARCHIVE_SUFFIXES + TEXT_SUFFIXES:
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
        self.root.minsize(720, 600)

        settings = self._load_settings()

        # 窗口图标（macOS 标题栏/ Dock）
        if os.path.isfile(ICON_PNG):
            try:
                img = tk.PhotoImage(file=ICON_PNG)
                img = img.subsample(max(1, img.width() // 64))
                self.root.iconphoto(True, img)
            except Exception:
                pass

        self.files = []              # 选中的源文件/目录
        self.mode = tk.StringVar(value=settings.get("mode", "auto"))
        self.running = False
        self.cancel = False
        self.log_q = queue.Queue()
        self.detect_q = queue.Queue()
        self.proc = None
        self.last_out = None
        self.started_at = None
        self._tick_id = None

        self._build_menu()
        self._build_ui(settings)
        self._poll_log()
        # 预检用单线程串行处理，避免一次选几十个文件就起几十个解释器
        threading.Thread(target=self._detect_loop, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # 拖文件到程序图标启动：argv 里带进来的真实文件路径
        dropped = [a for a in sys.argv[1:] if os.path.exists(a)]
        if dropped:
            self.add_files(dropped)

    # ---------- 设置持久化 ----------
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_settings(self):
        d = {
            "outdir": self.out_var.get().strip(),
            "install": bool(self.install_var.get()),
            "mode": self.mode.get(),
            "geometry": self.root.geometry(),
        }
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- 菜单 ----------
    def _build_menu(self):
        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar)
        m_file.add_command(label="选择文件…", command=self.choose_files)
        m_file.add_command(label="清空列表", command=self.clear_files)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=m_file)

        m_adv = tk.Menu(menubar)
        m_adv.add_command(label="编辑转换脚本…", command=self.open_script_editor)
        m_adv.add_command(label="打开脚本目录", command=self.open_scripts_dir)
        m_adv.add_separator()
        m_adv.add_command(label="打开词典 App", command=self.open_dictionary_app)
        m_adv.add_command(label="关于 Python 运行时…", command=self.about_python)
        menubar.add_cascade(label="高级", menu=m_adv)

        m_help = tk.Menu(menubar)
        m_help.add_command(label="使用说明", command=self.show_help)
        m_help.add_command(label="关于本程序", command=self.about_app)
        menubar.add_cascade(label="帮助", menu=m_help)

        self.root.config(menu=menubar)

    # ---------- 主界面 ----------
    def _build_ui(self, settings):
        f = ttk.Frame(self.root)
        f.pack(fill="both", expand=True, padx=10, pady=10)

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="苹果字典转换器", font=("Songti SC", 18, "bold")).pack(side="left")
        ttk.Label(top, text="源文件 → macOS 原生 .dictionary", foreground="#888").pack(side="left", padx=8)

        # ---- 文件列表（批量队列）----
        box = ttk.LabelFrame(f, text=" 源文件（可多选，按顺序批量转换） ")
        box.pack(fill="both", expand=False, pady=(8, 4))

        cols = ("name", "kind", "status")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=5,
                                 selectmode="extended")
        self.tree.heading("name", text="文件")
        self.tree.heading("kind", text="识别为")
        self.tree.heading("status", text="状态")
        self.tree.column("name", width=330, anchor="w")
        self.tree.column("kind", width=170, anchor="w")
        self.tree.column("status", width=150, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(4, 0))

        row = ttk.Frame(box)
        row.pack(fill="x", padx=6, pady=6)
        ttk.Button(row, text="选择文件…", command=self.choose_files).pack(side="left")
        ttk.Button(row, text="移除选中", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(row, text="清空", command=self.clear_files).pack(side="left")
        ttk.Label(row, text="也可把文件拖到 Dock/程序图标上打开", foreground="#888").pack(side="right")

        # ---- 参数 ----
        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="转换模式：", width=9).pack(side="left")
        self.mode_cb = ttk.Combobox(row, textvariable=self.mode, state="readonly", width=18,
                                    values=["auto", "wiktionary", "csv", "tabfile",
                                            "stardict", "appledict"])
        self.mode_cb.pack(side="left")
        ttk.Label(row, text="auto = 自动解包并识别（推荐）", foreground="#888").pack(side="left", padx=8)

        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="词典名称：", width=9).pack(side="left")
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(row, textvariable=self.name_var)
        self.name_entry.pack(side="left", fill="x", expand=True)
        self.name_hint = ttk.Label(row, text="", foreground="#888")
        self.name_hint.pack(side="left", padx=6)
        self.name_var.trace_add("write", lambda *_: self._update_name_hint())

        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="输出目录：", width=9).pack(side="left")
        self.out_var = tk.StringVar(value=settings.get("outdir") or os.path.expanduser("~/Desktop"))
        ttk.Entry(row, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row, text="浏览…", command=self.choose_outdir).pack(side="left")

        row = ttk.Frame(f)
        row.pack(fill="x", pady=2)
        self.install_var = tk.BooleanVar(value=bool(settings.get("install")))
        ttk.Checkbutton(row, text="转换完成后安装到系统词典目录（~/Library/Dictionaries）",
                        variable=self.install_var).pack(side="left")

        # ---- 按钮 ----
        row = ttk.Frame(f)
        row.pack(fill="x", pady=8)
        self.start_btn = ttk.Button(row, text="开始转换", command=self.start_convert)
        self.start_btn.pack(side="left", ipadx=12, ipady=4)
        self.finder_btn = ttk.Button(row, text="在 Finder 中显示", command=self.open_last, state="disabled")
        self.finder_btn.pack(side="left", padx=8)
        self.dict_btn = ttk.Button(row, text="打开词典 App", command=self.open_dictionary_app)
        self.dict_btn.pack(side="left")
        self.cancel_btn = ttk.Button(row, text="停止", command=self.stop_convert, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)

        # ---- 进度 / 状态 ----
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(8, 0))
        self.toggle_log_btn = ttk.Button(row, text="显示日志 ▸", command=self.toggle_log, width=11)
        self.toggle_log_btn.pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(row, textvariable=self.status_var, foreground="#666").pack(side="right")

        self.prog = ttk.Progressbar(f, mode="determinate", maximum=100)
        self.prog.pack(fill="x", pady=(6, 0))

        self.log_frame = ttk.Frame(f)
        self.log = scrolledtext.ScrolledText(self.log_frame, height=12, wrap="word", font=("Menlo", 11))
        self.log.pack(fill="both", expand=True)
        self.log.config(state="disabled")
        self.log.bind("<Button-2>", self._log_menu)
        self.log.bind("<Control-Button-1>", self._log_menu)
        self.log_visible = False

        geom = settings.get("geometry")
        if isinstance(geom, str) and "x" in geom:
            try:
                self.root.geometry(geom)
            except Exception:
                pass

    # ---------- 文件选择 ----------
    def _filter_companions(self, paths):
        """选了 x.ifo 时，同名的 .idx/.dict/.syn/.dict.dz 是同一本词典的组成件，
        不能让它们各自变成一个注定失败的任务。

        词条名里可能带点（如 WordNet-3.0.ifo），所以按「剥掉组成件后缀」求 stem，
        不能用 split(".")[0] 那样把 WordNet-3.0 截成 WordNet-3。
        """
        ifo_stems = {os.path.splitext(os.path.basename(p))[0].lower()
                     for p in paths if p.lower().endswith(".ifo")}
        out = []
        for p in paths:
            low = p.lower()
            base = os.path.basename(low)
            stem, is_companion = base, False
            for suf in COMPANION_SUFFIXES:
                if base.endswith(suf):
                    stem, is_companion = base[: -len(suf)], True
                    break
            if is_companion and stem in ifo_stems:
                continue
            out.append(p)
        return out

    def add_files(self, paths):
        if self.running:
            messagebox.showinfo("正在转换", "当前批次尚未结束，请等完成或点「停止」后再添加文件。")
            return
        added = []
        for p in self._filter_companions(paths):
            if os.path.exists(p) and p not in self.files:
                self.files.append(p)
                self._insert_row(p)
                added.append(p)
        self._refresh_file_state()
        if not self.name_var.get() and len(self.files) == 1:
            self.name_var.set(_default_name(self.files[0]))
        # 预检类型（交给单个后台线程串行处理，不阻塞界面）
        for p in added:
            self.detect_q.put(p)

    def _insert_row(self, path):
        if self.tree.exists(path):
            return
        self.tree.insert("", "end", iid=path, values=(os.path.basename(path), "探测中…", "待转换"))

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="选择词典源文件",
            filetypes=[("支持的词典文件", " ".join("*" + e for e in SUPPORTED_EXT)),
                       ("全部文件", "*.*")])
        if paths:
            self.add_files(list(paths))

    def choose_outdir(self):
        d = filedialog.askdirectory(title="选择输出目录", initialdir=self.out_var.get())
        if d:
            self.out_var.set(d)

    def remove_selected(self):
        if self.running:
            return
        for iid in self.tree.selection():
            if iid in self.files:
                self.files.remove(iid)
            self.tree.delete(iid)
        self._refresh_file_state()

    def clear_files(self):
        if self.running:
            return
        self.files = []
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._refresh_file_state()

    def _update_name_hint(self):
        n = len(self.files)
        if n <= 1:
            self.name_hint.config(
                text="（产出 %s.dictionary）" % self.name_var.get().strip()
                if self.name_var.get().strip() else "")
        else:
            self.name_hint.config(text="批量模式：名称按各文件名自动推导")

    def _refresh_file_state(self):
        n = len(self.files)
        if n == 0:
            self.name_entry.config(state="normal")
        elif n == 1:
            self.name_entry.config(state="normal")
        else:
            self.name_entry.config(state="disabled")
        self._update_name_hint()
        self.status_var.set("就绪 · %d 个文件" % n if n else "就绪")

    # ---------- 类型预检 ----------
    def _detect_loop(self):
        while True:
            path = self.detect_q.get()
            if path is None:
                return
            try:
                r = subprocess.run([sys.executable, CONVERT, "detect", path],
                                   capture_output=True, text=True, timeout=30)
                info = json.loads((r.stdout or "").strip().splitlines()[-1])
            except Exception as e:
                info = {"ok": False, "error": str(e)}
            self.log_q.put(("__DETECT__", path, info))

    # ---------- 转换 ----------
    def start_convert(self):
        if self.running:
            return
        if not self.files:
            messagebox.showerror("缺少源文件", "请先选择要转换的源文件。")
            return
        outdir = self.out_var.get().strip()
        if not outdir:
            messagebox.showerror("缺少输出目录", "请选择输出目录。")
            return
        os.makedirs(outdir, exist_ok=True)

        mode = self.mode.get()
        if mode != "auto" and len(self.files) > 1:
            messagebox.showerror("批量限制",
                                 "手动指定模式时一次只能转换一个文件。\n"
                                 "请改用「auto」模式批量转换，或只保留一个文件。")
            return

        jobs, err = self._make_jobs(mode, outdir)
        if err:
            messagebox.showerror("无法开始", err)
            return
        if not jobs:
            return

        self.running = True
        self.cancel = False
        self.last_out = None
        self.finder_btn.config(state="disabled")
        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.started_at = time.time()
        for j in jobs:
            self.tree.set(j["src"], "status", "排队中")
        self.prog.config(mode="determinate", maximum=len(jobs), value=0)
        self._log("=" * 52)
        self._log("开始转换：%d 个任务，模式 %s" % (len(jobs), JOB_MODE_LABEL.get(mode, mode)))
        self._log("=" * 52)
        self._tick()
        threading.Thread(target=self._run_jobs, args=(jobs,), daemon=True).start()

    def _make_jobs(self, mode, outdir):
        """在主线程里把每个任务的命令拼好（要用到 messagebox 与界面值）。"""
        jobs = []
        used = set()
        single = len(self.files) == 1
        if single:
            name = self.name_var.get().strip()
            if not name:
                return [], "请填写词典名称（即产出 .dictionary 的文件夹名）。"
        for p in self.files:
            if single:
                nm = name
            else:
                nm = _default_name(p)
                base, k = nm, 2
                while nm in used:
                    nm = "%s (%d)" % (base, k)
                    k += 1
            used.add(nm)
            cmd, err = self._build_cmd(mode, nm, outdir, p)
            if err:
                return [], err
            jobs.append({"src": p, "name": nm, "cmd": cmd,
                         "out": os.path.join(outdir, nm + ".dictionary")})
        return jobs, None

    def _build_cmd(self, mode, name, outdir, src):
        install = ["--install"] if self.install_var.get() else []
        low = src.lower()
        if mode == "auto":
            return [sys.executable, CONVERT, "auto", src, outdir, "--name", name] + install, None
        if mode == "wiktionary":
            if not (low.endswith(".bz2") and not _is_archive(src)):
                return None, "维基模式需要 .bz2 dump 文件（不是 StarDict 压缩包）。"
            return [sys.executable, CONVERT, "wiktionary", src, outdir, "--name", name] + install, None
        if mode == "csv":
            if not low.endswith(".csv"):
                return None, "CSV 模式需要一个 .csv 文件。"
            return [sys.executable, CONVERT, "csv", src, outdir, name, "--name", name] + install, None
        if mode == "tabfile":
            if not low.endswith(TEXT_SUFFIXES):
                return None, "两列文本模式需要 .txt/.tsv/.tab 文件。"
            return [sys.executable, CONVERT, "tabfile", src, outdir, "--name", name] + install, None
        if mode == "stardict":
            if not low.endswith(".ifo"):
                return None, "StarDict 模式需要 .ifo 文件（.idx/.dict 需与其同目录）。"
            return [sys.executable, CONVERT, "stardict", src, outdir, "--name", name] + install, None
        if mode == "appledict":
            if not (os.path.isdir(src) or low.endswith(".xml")):
                return None, "AppleDict 模式需要一个工程目录（含 xxx.xml），或直接给 .xml 文件。"
            # 原样传给引擎：给的是 .xml 就用这个文件，不让引擎再扫目录选错工程
            return [sys.executable, CONVERT, "appledict", src, outdir, "--name", name] + install, None
        return None, "未知模式：%s" % mode

    def _run_jobs(self, jobs):
        done = 0
        failed = []
        try:
            for idx, job in enumerate(jobs):
                if self.cancel:
                    self.log_q.put(("__JOB_STATUS__", job["src"], "已取消"))
                    continue
                self.log_q.put(("__JOB_STATUS__", job["src"], "转换中…"))
                self.log_q.put(">>> [%d/%d] %s → %s" % (idx + 1, len(jobs),
                                                        os.path.basename(job["src"]), job["name"]))
                t0 = time.time()
                rc = self._exec(job["cmd"])
                dur = time.time() - t0
                if rc == 0:
                    done += 1
                    self.log_q.put(("__JOB_STATUS__", job["src"], "✓ 完成 %.1fs" % dur))
                    self.log_q.put(("__LAST_OUT__", job["out"]))
                elif self.cancel:
                    # 用户自己点「停止」中断，不算失败，别弹错误框
                    self.log_q.put(("__JOB_STATUS__", job["src"], "已取消"))
                else:
                    failed.append((os.path.basename(job["src"]), rc))
                    self.log_q.put(("__JOB_STATUS__", job["src"], "✗ 失败 %s" % self._rc_text(rc)))
                self.log_q.put(("__JOB_DONE__",))
        finally:
            # 无论中途出什么异常，都要让界面解除「转换中」状态，否则按钮永远卡住
            self.log_q.put(("__ALL_DONE__", len(jobs), done, failed,
                            time.time() - self.started_at))

    def _exec(self, cmd):
        """跑一次转换子进程，实时把输出塞进日志队列，返回退出码。"""
        proc = None
        try:
            env = dict(os.environ)
            # 已显式指定 DDK 时尊重它（源码方式运行时用）；否则用应用内置的 DDK。
            env.setdefault("DICT_BUILD_TOOL_BIN", os.path.join(RESOURCES, "ddk"))
            # start_new_session=True：让转换子进程自成会话，脱离主程序的进程组/控制终端。
            # 否则 Finder 启动的 .app 在会话变化（退出/注销/合盖）时会收到 SIGHUP，
            # 把正在跑的长编译一并杀掉，表现为返回码 -1。
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, universal_newlines=True, env=env,
                start_new_session=True)
            self.proc = proc
            for line in proc.stdout:
                self.log_q.put(line.rstrip("\n"))
            return proc.wait()
        except Exception as e:
            self.log_q.put("【异常】%s" % e)
            # 读日志阶段出错时子进程可能仍在编译：必须终止并回收，否则它变成
            # 没人读 stdout 的孤儿进程，撑满管道后卡死，还会和下一次转换互相踩踏。
            self._terminate(proc)
            if proc is not None:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    # 5 秒还没退，说明 make/perl 忽略 SIGTERM，整组 SIGKILL 免得留孤儿
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        pass
            return -9999
        finally:
            self.proc = None

    @staticmethod
    def _rc_text(rc):
        if rc == -9999:
            return "(启动异常)"
        if rc < 0:
            try:
                return "(%s)" % signal.Signals(-rc).name
            except Exception:
                return "(信号 %d)" % -rc
        return "(返回码 %d)" % rc

    @staticmethod
    def _terminate(proc):
        """结束整个转换进程组。子进程用 start_new_session 自成一会话，
        只 terminate 直接子进程的话，make/perl 会继续在后台编译，
        既没真正停下，又会和下一次转换互相踩踏。"""
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def stop_convert(self):
        if not self.running:
            return
        self.cancel = True
        self._terminate(self.proc)
        self.status_var.set("正在停止…")

    def _poll_log(self):
        try:
            while True:
                item = self.log_q.get_nowait()
                try:
                    self._handle_item(item)
                except Exception as e:
                    # 单条消息处理失败不能把整个泵打死（否则日志/进度/完成回调全停）
                    self._log("【界面处理异常】%s" % e)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_log)

    def _handle_item(self, item):
        if isinstance(item, str):
            self._log(item)
            return
        if not isinstance(item, tuple) or not item:
            return
        kind = item[0]
        if kind == "__DETECT__":
            _, path, info = item
            self._on_detect(path, info)
        elif kind == "__JOB_STATUS__":
            _, src, text = item
            if self.tree.exists(src):
                self.tree.set(src, "status", text)
        elif kind == "__LAST_OUT__":
            self.last_out = item[1]
        elif kind == "__JOB_DONE__":
            self.prog["value"] = min(self.prog["maximum"], self.prog["value"] + 1)
        elif kind == "__ALL_DONE__":
            self._on_all_done(*item[1:])

    def _on_detect(self, path, info):
        if not self.tree.exists(path):
            return
        if not isinstance(info, dict):
            info = {"ok": False, "error": "预检返回的不是 JSON 对象"}
        if info.get("ok"):
            label = info.get("label") or info.get("mode") or "?"
            wc = info.get("wordcount")
            if isinstance(wc, int) and wc > 0:
                label += " · %d 条" % wc
            self.tree.set(path, "kind", label)
        else:
            self.tree.set(path, "kind", "无法识别")
            self.tree.set(path, "status", "需手动选模式")
            self._log("[预检] %s：%s" % (os.path.basename(path),
                                       info.get("error") or "无法识别的输入"))

    def _tick(self):
        if not self.running:
            return
        elapsed = time.time() - self.started_at
        if self.cancel:
            self.status_var.set("正在停止… 已用 %.1fs" % elapsed)
        else:
            self.status_var.set("转换中… 已用 %.1fs" % elapsed)
        self._tick_id = self.root.after(500, self._tick)

    def _on_all_done(self, total, done, failed, seconds):
        self.running = False
        self.cancel = False
        self.start_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        if self._tick_id:
            try:
                self.root.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None

        if done == total and total > 0:
            self.status_var.set("完成 ✓ %d 个 · 共 %.1fs" % (total, seconds))
            self._log("\n✅ 全部完成：%d 个词典，用时 %.1fs" % (total, seconds))
        else:
            self.status_var.set("完成 %d/%d · 失败 %d" % (done, total, len(failed)))

        if self.last_out and os.path.isdir(self.last_out):
            self.finder_btn.config(state="normal")
        if self.install_var.get() or done:
            self.dict_btn.config(state="normal")

        if failed:
            if not self.log_visible:
                self.toggle_log()
            detail = "\n".join("  • %s %s" % (n, self._rc_text(rc)) for n, rc in failed)
            messagebox.showerror(
                "部分转换失败",
                "成功 %d 个，失败 %d 个：\n\n%s\n\n常见原因与处理：\n"
                "  • 源文件格式与所选模式不符 → 用 auto 模式重试\n"
                "  • Apple Silicon 未装 Rosetta 2 → 终端执行 softwareupdate --install-rosetta\n"
                "  • 大词典编译被系统中止（退出/合盖）→ 保持程序运行、勿合盖\n\n"
                "详见转换日志。" % (done, len(failed), detail))

    def open_last(self):
        if self.last_out:
            subprocess.run(["open", "-R", self.last_out])

    def open_dictionary_app(self):
        subprocess.run(["open", "-a", "Dictionary"])

    # ---------- 日志右键 ----------
    def _log_menu(self, event):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="复制", command=self._copy_log)
        m.add_command(label="全选", command=lambda: self.log.tag_add("sel", "1.0", "end"))
        m.add_separator()
        m.add_command(label="清空日志", command=lambda: (self.log.config(state="normal"),
                                                        self.log.delete("1.0", "end"),
                                                        self.log.config(state="disabled")))
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

    def _log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

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
        messagebox.showinfo("Python 运行时",
                            "本程序自带完整 Python 运行时，无需在目标机器预装。\n\n版本：%s\n路径：%s" % (ver, PY))

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

        def load(*_):
            p = os.path.join(SCRIPTS_DIR, fvar.get())
            try:
                txt.delete("1.0", "end")
                with open(p, encoding="utf-8") as fh:
                    txt.insert("end", fh.read())
            except Exception as e:
                messagebox.showerror("读取失败", str(e))

        def save():
            p = os.path.join(SCRIPTS_DIR, fvar.get())
            if not messagebox.askyesno("确认保存",
                                       "将覆盖应用内的 %s，可能影响转换功能。确定？" % fvar.get()):
                return
            try:
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(txt.get("1.0", "end"))
                messagebox.showinfo("已保存", "已写回：%s" % p)
            except Exception as e:
                messagebox.showerror("保存失败", str(e))

        cb.bind("<<ComboboxSelected>>", load)
        btn = ttk.Frame(win)
        btn.pack(fill="x", padx=8, pady=6)
        ttk.Button(btn, text="载入", command=load).pack(side="left")
        ttk.Button(btn, text="保存", command=save).pack(side="left", padx=8)
        ttk.Label(btn, text="（改坏可能导致转换失败）", foreground="#a33").pack(side="left", padx=10)
        load()

    # ---------- 帮助/关于 ----------
    def show_help(self):
        messagebox.showinfo(
            "使用说明",
            "1. 选择源文件（可一次选多个，按列表顺序批量转换）。支持：\n"
            "   · 维基词典 dump（.bz2）\n"
            "   · dictkit 系 CSV（.csv）\n"
            "   · 任意「词条<TAB>释义」两列文本（.txt/.tsv）\n"
            "   · StarDict（.ifo，或含 .dict.dz 的完整包）\n"
            "   · 已有的 AppleDict 工程目录（xxx.xml + plist + css）\n"
            "   · 上述任意内容的压缩包（.tar.bz2/.tar.gz/.zip）或已解压目录\n"
            "2. 模式保持「auto」即可：程序自动解包、识别格式再转换。\n"
            "   选好文件后，列表里的「识别为」列会显示检测结果。\n"
            "3. 填「词典名称」（即产出 .dictionary 的文件夹名）与「输出目录」。\n"
            "   批量转换时名称按各文件名自动推导。\n"
            "4. 可选：勾选安装到系统词典目录（勾选后才会真正写入 ~/Library/Dictionaries）。\n"
            "5. 点「开始转换」，完成后可「在 Finder 中显示」或「打开词典 App」。\n\n"
            "Apple Silicon 需已安装 Rosetta 2（DDK 编译器为 x86）。\n"
            "大词典编译很慢（5MB 词典 PyGlossary 一步约 8 分钟，维基全量可能数小时），\n"
            "转换期间请勿退出程序、勿合盖睡眠。")

    def about_app(self):
        messagebox.showinfo(
            "关于",
            "苹果字典转换器\n\n"
            "把词典源文件（维基 dump / CSV / 两列文本 / StarDict / AppleDict 工程）\n"
            "转换成 macOS 原生词典（.dictionary）。\n"
            "基于 PyGlossary + Apple Dictionary Development Kit。\n"
            "详见仓库 README / NOTICE。")

    # ---------- 退出 ----------
    def on_close(self):
        if self.running:
            if not messagebox.askyesno("正在转换",
                                       "转换尚未完成，现在退出会中止后续任务。确定退出？"):
                return
            self.cancel = True
            self._terminate(self.proc)
        self._save_settings()
        self.root.destroy()

    def mainloop(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().mainloop()
