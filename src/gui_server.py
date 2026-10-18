#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
古汉语词典转换器 —— 内置图形界面后端（本地服务器，纯标准库）

设计目标（对应使用者的诉求）：
  1) 应用不再"自己退出 / 跑到终端"：双击 APP 后本进程即为服务器，
     一直存活到用户点"退出"，全部工作在 APP 内部完成，绝不弹出终端。
  2) 完整图形界面：内置一个本地页面（http://127.0.0.1:随机端口），
     支持 拖拽文件 + 点击打开、输出命名、安装到系统词典、实时进度。
  3) 自包含的 Python：本文件随打包的 python3.13 一起运行，
     不依赖系统 Python；并内置"脚本编辑器"可查看/修改转换脚本。

安全：仅监听 127.0.0.1（本机回环），不对外暴露。
"""

import os
import sys
import json
import uuid
import shutil
import subprocess
import tempfile
import signal
import threading
import queue
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES = os.path.dirname(SCRIPT_DIR)
DDK = os.path.join(RESOURCES, "ddk")
PY = sys.executable
CONVERT = os.path.join(SCRIPT_DIR, "convert.py")
SCRIPTS_DIR = SCRIPT_DIR

# 上传与任务的临时根目录（每启动一次新建一个，退出时清理）
TMPROOT = tempfile.mkdtemp(prefix="gudai_gui_")
JOBS = {}  # job_id -> queue.Queue
JOBS_LOCK = threading.Lock()
SERVER = None
ALIVE = True

EDITABLE = {
    "convert.py": os.path.join(SCRIPTS_DIR, "convert.py"),
    "wiktionary_parse.py": os.path.join(SCRIPTS_DIR, "wiktionary_parse.py"),
    "stardict_write.py": os.path.join(SCRIPTS_DIR, "stardict_write.py"),
    "fix_dict_plist.sh": os.path.join(SCRIPTS_DIR, "fix_dict_plist.sh"),
}

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>古汉语词典转换器</title>
<style>
  :root{
    --bg:#0f1419; --card:#1a212b; --card2:#222c38; --line:#2c3744;
    --txt:#e6edf3; --muted:#8b98a5; --accent:#2bb6a6; --accent2:#1d8f82;
    --warn:#e0a458; --err:#e06c75; --ok:#7ee787;
  }
  *{box-sizing:border-box}
  body{margin:0;background:linear-gradient(160deg,#0b0f14,#121a22);color:var(--txt);
       font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif;
       min-height:100vh;display:flex;justify-content:center;padding:28px 16px}
  .wrap{width:100%;max-width:760px}
  .head{display:flex;align-items:center;gap:14px;margin-bottom:18px}
  .logo{width:54px;height:54px;flex:0 0 auto}
  .head h1{font-size:21px;margin:0;font-weight:700}
  .head p{margin:2px 0 0;color:var(--muted);font-size:13px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
  .card h2{font-size:14px;margin:0 0 12px;color:var(--muted);font-weight:600;letter-spacing:.04em}
  .drop{border:2px dashed var(--line);border-radius:12px;padding:26px 16px;text-align:center;
        cursor:pointer;transition:.15s;background:var(--card2)}
  .drop.over{border-color:var(--accent);background:#1c2a2a}
  .drop .big{font-size:15px;margin-bottom:4px}
  .drop .sub{font-size:12px;color:var(--muted)}
  .files{margin-top:10px;font-size:13px}
  .files .f{display:flex;justify-content:space-between;padding:6px 10px;background:var(--card2);
            border-radius:8px;margin-bottom:6px}
  .files .f span:last-child{color:var(--muted)}
  .row{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}
  .field{flex:1;min-width:200px}
  label{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}
  input,select{width:100%;background:var(--card2);border:1px solid var(--line);color:var(--txt);
        border-radius:8px;padding:9px 10px;font-size:13px;outline:none}
  input:focus,select:focus{border-color:var(--accent)}
  .chk{display:flex;align-items:center;gap:8px;margin-top:14px;font-size:13px;color:var(--muted)}
  .chk input{width:auto}
  button{font-family:inherit;cursor:pointer;border:none;border-radius:9px;font-size:14px;font-weight:600;
          padding:11px 18px;transition:.15s}
  .primary{background:var(--accent);color:#04201c}
  .primary:hover{background:var(--accent2)}
  .primary:disabled{opacity:.5;cursor:not-allowed}
  .ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}
  .ghost:hover{color:var(--txt)}
  .danger{background:transparent;color:var(--err);border:1px solid #4a2c30}
  .bar{display:flex;gap:10px;margin-top:16px;align-items:center}
  pre.log{background:#0b0f14;border:1px solid var(--line);border-radius:10px;padding:12px;
          height:220px;overflow:auto;font-size:12px;line-height:1.55;color:#cfe3dc;white-space:pre-wrap;
          font-family:"SF Mono",Menlo,Consolas,monospace;margin:0}
  .result{margin-top:12px;padding:12px;background:#10211c;border:1px solid #1d4a40;border-radius:10px;font-size:13px}
  .result .path{color:var(--ok);word-break:break-all}
  .hidden{display:none}
  .about{font-size:12px;color:var(--muted);line-height:1.7}
  .editor textarea{width:100%;height:300px;background:#0b0f14;border:1px solid var(--line);color:#d7e6df;
        border-radius:10px;padding:12px;font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12px;
        line-height:1.5;resize:vertical;outline:none}
  .editor select{margin-bottom:8px}
  .hint{font-size:11px;color:var(--warn);margin-top:6px}
  a{color:var(--accent)}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <svg class="logo" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#2bb6a6"/><stop offset="1" stop-color="#157a6e"/></linearGradient></defs>
      <rect x="4" y="4" width="92" height="92" rx="22" fill="url(#g)"/>
      <text x="26" y="62" font-size="34" fill="#f4fbf9" font-family="serif" text-anchor="middle">词</text>
      <path d="M44 50 H62 M57 44 L63 50 L57 56" stroke="#ffffff" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      <text x="74" y="58" font-size="23" fill="#eafffb" font-family="serif" text-anchor="middle">词典</text>
    </svg>
    <div>
      <h1>古汉语词典转换器</h1>
      <p>把 BZ2 / CSV / StarDict 转成 macOS 原生 .dictionary —— 全程在应用内完成</p>
    </div>
  </div>

  <div class="card">
    <h2>1 · 选择源文件（可拖拽，也可点击选择）</h2>
    <div class="drop" id="drop">
      <div class="big">把文件拖到这里</div>
      <div class="sub">或点击选择 · 支持 .bz2（维基）/ .csv（dictkit）/ .ifo+.idx+.dict（StarDict，可多选）</div>
      <input type="file" id="file" multiple class="hidden" />
    </div>
    <div class="files" id="files"></div>
  </div>

  <div class="card">
    <h2>2 · 输出设置</h2>
    <div class="row">
      <div class="field">
        <label>转换模式</label>
        <select id="mode">
          <option value="auto">自动识别（按扩展名）</option>
          <option value="wiktionary">维基 BZ2 → 词典</option>
          <option value="csv">dictkit CSV → 词典</option>
          <option value="stardict">StarDict(.ifo) → 词典</option>
        </select>
      </div>
      <div class="field">
        <label>输出词典名称（.dictionary）</label>
        <input id="name" placeholder="留空则自动命名" />
      </div>
    </div>
    <div class="row">
      <div class="field">
        <label>输出目录</label>
        <input id="outdir" value="~/Downloads" />
      </div>
      <div class="field" id="bookWrap">
        <label>书名（仅 CSV 需要，写入元信息）</label>
        <input id="bookname" placeholder="如：古代汉语词典（第2版）" />
      </div>
    </div>
    <div class="chk"><input type="checkbox" id="install" /><label for="install" style="margin:0">转换完成后安装到系统词典目录（~/Library/Dictionaries）</label></div>
  </div>

  <div class="card">
    <h2>3 · 转换</h2>
    <div class="bar">
      <button class="primary" id="go">开始转换</button>
      <button class="ghost" id="clear">清空选择</button>
      <span id="status" style="color:var(--muted);font-size:13px"></span>
    </div>
    <pre class="log" id="log">就绪。拖入文件后点击「开始转换」。\n（大文件如维基 BZ2 解压较慢，请耐心等待，进度会实时显示。）</pre>
    <div class="result hidden" id="result">
      <div>✅ 转换完成，最终产物（请使用这个 *.dictionary）：</div>
      <div class="path" id="rpath"></div>
      <div style="margin-top:10px;display:flex;gap:10px">
        <button class="ghost" id="openFinder">在 Finder 中显示</button>
        <button class="ghost" id="copyPath">复制路径</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>关于 / 高级</h2>
    <div class="about" id="about">加载中…</div>
    <div class="bar">
      <button class="ghost" id="openScripts">在 Finder 打开脚本目录</button>
      <button class="ghost" id="toggleEditor">编辑转换脚本</button>
      <button class="danger" id="quit">退出应用</button>
    </div>
    <div class="editor hidden" id="editor" style="margin-top:14px">
      <select id="scriptSel">
        <option value="convert.py">convert.py（主流程）</option>
        <option value="wiktionary_parse.py">wiktionary_parse.py（BZ2 解析）</option>
        <option value="stardict_write.py">stardict_write.py（CSV→StarDict）</option>
        <option value="fix_dict_plist.sh">fix_dict_plist.sh（plist 修正）</option>
      </select>
      <textarea id="scriptBody" spellcheck="false"></textarea>
      <div class="hint">注意：保存会直接覆盖应用内的脚本文件。改坏可能导致转换失败，请谨慎。</div>
      <div class="bar">
        <button class="primary" id="saveScript">保存</button>
        <button class="ghost" id="reloadScript">重新加载</button>
        <span id="editMsg" style="font-size:12px;color:var(--ok)"></span>
      </div>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let selected = [];
let jobId = null;

function showFiles(){
  const box = $('files'); box.innerHTML='';
  selected.forEach(f=>{
    const d=document.createElement('div'); d.className='f';
    d.innerHTML=`<span>${f.name}</span><span>${(f.size/1048576).toFixed(2)} MB</span>`;
    box.appendChild(d);
  });
}
function addFiles(list){
  for(const f of list) if(!selected.find(x=>x.name===f.name && x.size===f.size)) selected.push(f);
  showFiles();
}
const drop=$('drop');
drop.addEventListener('click',()=>$('file').click());
$('file').addEventListener('change',e=>{addFiles(e.target.files);e.target.value='';});
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('over');}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('over');}));
drop.addEventListener('drop',e=>{ if(e.dataTransfer.files) addFiles(e.dataTransfer.files); });

function logLine(t){ const el=$('log'); el.textContent += t + "\n"; el.scrollTop=el.scrollHeight; }
function setStatus(t){ $('status').textContent=t; }

$('clear').onclick=()=>{ selected=[]; showFiles(); };

$('go').onclick=async ()=>{
  if(selected.length===0){ alert('请先选择文件'); return; }
  $('go').disabled=true; setStatus('上传中…');
  jobId = crypto.randomUUID();
  try{
    for(const f of selected){
      await fetch(`/api/upload?job=${encodeURIComponent(jobId)}&name=${encodeURIComponent(f.name)}`,{method:'POST',body:f});
    }
  }catch(e){ logLine('上传失败：'+e); $('go').disabled=false; return; }

  const params=new URLSearchParams({
    job:jobId, mode:$('mode').value, name:$('name').value,
    outdir:$('outdir').value, bookname:$('bookname').value,
    install:$('install').checked?'1':''
  });
  setStatus('转换中…');
  const r=await fetch('/api/convert?'+params,{method:'POST'});
  const j=await r.json();
  if(!j.ok){ logLine('启动失败：'+JSON.stringify(j)); $('go').disabled=false; return; }

  const es=new EventSource('/api/stream?job='+encodeURIComponent(jobId));
  es.onmessage=ev=>{
    const d=JSON.parse(ev.data);
    if(d.line!==undefined) logLine(d.line);
    if(d.done){
      es.close();
      if(d.ok){ $('result').classList.remove('hidden'); $('rpath').textContent=d.final||'(见上方日志)'; }
      else { logLine('‼ 转换失败，请查看上方日志。'); }
      setStatus(d.ok?'完成':'失败');
      $('go').disabled=false;
    }
  };
  es.onerror=()=>{ /* 正常在 done 后关闭，这里忽略 */ };
};

$('openFinder').onclick=async ()=>{
  const p=$('rpath').textContent.trim(); if(!p) return;
  await fetch('/api/open?path='+encodeURIComponent(p),{method:'POST'});
};
$('copyPath').onclick=()=>{ navigator.clipboard.writeText($('rpath').textContent.trim()); };

async function loadInfo(){
  const r=await fetch('/api/info'); const j=await r.json();
  $('about').innerHTML=
    `本应用已内置完整 Python 运行时，无需系统安装：<br>`+
    `• Python：${j.python.split(' ')[0]} （${j.python.split(' ')[1]||''}）<br>`+
    `• 脚本目录：${j.scripts}<br>`+
    `• Apple DDK：${j.ddk}<br>`+
    `• PyGlossary 与 DDK 均已打包在内。<br>`+
    `<span style="color:var(--warn)">提示：Apple Silicon Mac 需已安装 Rosetta 2（编译 .dictionary 用）。</span>`;
}
loadInfo();

$('openScripts').onclick=async ()=>{ await fetch('/api/open?path='+encodeURIComponent(SCRIPTS_PLACEHOLDER),{method:'POST'}); };
// 用真实脚本目录替换
fetch('/api/info').then(r=>r.json()).then(j=>{ window.SCRIPTS=j.scripts;
  $('openScripts').onclick=async ()=>{ await fetch('/api/open?path='+encodeURIComponent(j.scripts),{method:'POST'}); };
});

$('toggleEditor').onclick=()=>$('editor').classList.toggle('hidden');
async function loadScript(){
  const name=$('scriptSel').value;
  const r=await fetch('/api/script?name='+encodeURIComponent(name)); const j=await r.json();
  if(j.ok) $('scriptBody').value=j.content; else $('scriptBody').value='读取失败：'+j.error;
}
$('scriptSel').onchange=loadScript;
$('reloadScript').onclick=loadScript;
$('saveScript').onclick=async ()=>{
  const name=$('scriptSel').value; const body=$('scriptBody').value;
  const r=await fetch('/api/script',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name,content:body})});
  const j=await r.json();
  $('editMsg').textContent = j.ok?'已保存 ✔':'保存失败：'+j.error;
  setTimeout(()=>$('editMsg').textContent='',4000);
};
loadScript();

$('quit').onclick=async ()=>{ await fetch('/api/quit',{method:'POST'}); };
</script>
</body>
</html>
"""


def _send_json(handler, obj, code=200):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_body(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    return handler.rfile.read(length) if length else b""


def detect_mode(job_dir, override):
    if override and override != "auto":
        return override
    names = os.listdir(job_dir)
    if any(n.endswith(".ifo") for n in names):
        return "stardict"
    if any(n.endswith(".bz2") for n in names):
        return "wiktionary"
    if any(n.endswith(".csv") for n in names):
        return "csv"
    return "auto"


def find_input(job_dir, mode):
    names = sorted(os.listdir(job_dir))
    if mode == "stardict":
        for n in names:
            if n.endswith(".ifo"):
                return os.path.join(job_dir, n)
    if mode == "wiktionary":
        for n in names:
            if n.endswith(".bz2"):
                return os.path.join(job_dir, n)
    if mode == "csv":
        for n in names:
            if n.endswith(".csv"):
                return os.path.join(job_dir, n)
    return None


def run_convert(job_id, mode, name, outdir, bookname, install):
    q = JOBS[job_id]
    job_dir = os.path.join(TMPROOT, job_id)
    try:
        if mode == "auto":
            mode = detect_mode(job_dir, "auto")
        if mode == "auto":
            q.put({"line": "错误：无法识别文件类型，请手动选择转换模式。"})
            q.put({"done": True, "ok": False})
            return
        inp = find_input(job_dir, mode)
        if not inp:
            q.put({"line": "错误：在上传文件中未找到对应输入（%s）。" % mode})
            q.put({"done": True, "ok": False})
            return

        outdir = os.path.expanduser(outdir or "~/Downloads")
        os.makedirs(outdir, exist_ok=True)

        cmd = [PY, CONVERT, mode, inp, outdir]
        if mode == "csv":
            cmd.append(bookname or "自定义词典")
        if name:
            cmd += ["--name", name]
        if install:
            cmd.append("--install")

        q.put({"line": ">>> 启动转换：%s" % " ".join(cmd)})
        env = dict(os.environ)
        env["DICT_BUILD_TOOL_BIN"] = DDK
        env["CONVERTER_PYTHON"] = PY
        proc = subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True,
        )
        final = None
        for line in proc.stdout:
            line = line.rstrip("\n")
            q.put({"line": line})
            if "DONE:" in line or "最终产物" in line:
                # 提取路径
                import re
                m = re.search(r"(/\S+\.dictionary)", line)
                if m:
                    final = m.group(1)
        proc.wait()
        ok = proc.returncode == 0
        if ok:
            q.put({"line": "=== 转换成功 ==="})
        else:
            q.put({"line": "=== 转换失败（返回码 %d）===" % proc.returncode})
        q.put({"done": True, "ok": ok, "final": final})
    except Exception as e:
        q.put({"line": "异常：%s" % e})
        q.put({"done": True, "ok": False})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # 静默

    def _ok_html(self):
        body = INDEX_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._ok_html()
            return
        if u.path == "/api/info":
            _send_json(self, {
                "python": sys.version.replace("\n", " "),
                "scripts": SCRIPTS_DIR,
                "ddk": DDK,
            })
            return
        if u.path == "/api/script":
            qs = parse_qs(u.query)
            name = qs.get("name", [""])[0]
            path = EDITABLE.get(name)
            if not path:
                _send_json(self, {"ok": False, "error": "未知脚本"})
                return
            try:
                _send_json(self, {"ok": True, "content": open(path, "r", encoding="utf-8").read()})
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)})
            return
        if u.path == "/api/stream":
            qs = parse_qs(u.query)
            job = qs.get("job", [""])[0]
            q = JOBS.get(job)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            import time
            while ALIVE:
                try:
                    item = q.get(timeout=15)
                except Exception:
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except Exception:
                        break
                    continue
                try:
                    self.wfile.write(("data: " + json.dumps(item, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    break
                if item.get("done"):
                    break
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/upload":
            qs = parse_qs(u.query)
            job = qs.get("job", [""])[0]
            name = qs.get("name", [""])[0]
            body = _read_body(self)
            d = os.path.join(TMPROOT, job)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, name), "wb") as f:
                f.write(body)
            _send_json(self, {"ok": True, "size": len(body)})
            return
        if u.path == "/api/convert":
            qs = parse_qs(u.query)
            job = qs.get("job", [""])[0]
            mode = qs.get("mode", ["auto"])[0]
            name = qs.get("name", [""])[0]
            outdir = qs.get("outdir", ["~/Downloads"])[0]
            bookname = qs.get("bookname", [""])[0]
            install = qs.get("install", [""])[0] == "1"
            with JOBS_LOCK:
                if job not in JOBS:
                    JOBS[job] = queue.Queue()
            t = threading.Thread(
                target=run_convert, args=(job, mode, name, outdir, bookname, install), daemon=True
            )
            t.start()
            _send_json(self, {"ok": True, "job": job})
            return
        if u.path == "/api/open":
            qs = parse_qs(u.query)
            path = qs.get("path", [""])[0]
            try:
                subprocess.run(["open", "-R", path], check=False)
                _send_json(self, {"ok": True})
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)})
            return
        if u.path == "/api/script":
            raw = _read_body(self)
            try:
                obj = json.loads(raw.decode("utf-8"))
                name = obj.get("name", "")
                content = obj.get("content", "")
                path = EDITABLE.get(name)
                if not path:
                    _send_json(self, {"ok": False, "error": "未知脚本"})
                    return
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                _send_json(self, {"ok": True})
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)})
            return
        if u.path == "/api/quit":
            _send_json(self, {"ok": True})
            threading.Thread(target=shutdown_server, daemon=True).start()
            return
        self.send_response(404)
        self.end_headers()


def shutdown_server():
    global ALIVE
    ALIVE = False
    if SERVER:
        try:
            SERVER.shutdown()
        except Exception:
            pass


def main():
    global SERVER
    port = 0
    # 找空闲端口
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    SERVER = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    def _sig(*a):
        shutdown_server()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    url = "http://127.0.0.1:%d/" % port
    print("[GUI] 古汉语词典转换器已启动：%s" % url)
    try:
        subprocess.run(["open", url], check=False)
    except Exception:
        pass

    try:
        SERVER.serve_forever()
    finally:
        shutil.rmtree(TMPROOT, ignore_errors=True)
        print("[GUI] 已退出。")


if __name__ == "__main__":
    main()
