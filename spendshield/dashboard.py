# -*- coding: utf-8 -*-
"""SpendShield 审计 Dashboard — 可视化审计记录

用法:
  1. 程序里导出审计: guard.export_audit("audit.json")
  2. 启动看板:      python -m spendshield.dashboard --file audit.json --port 8775
  3. 打开 http://localhost:8775
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SpendShield 审计 Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:radial-gradient(900px 500px at 20% -10%,#1b1633 0%,#0b0e14 55%);color:#e6e8ee;padding:28px;min-height:100vh}
h1{font-size:20px;font-weight:800;margin-bottom:4px}h1 span{color:#a78bfa}
.sub{color:#7d8590;font-size:13px;margin-bottom:20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}
.stat{background:#141b26;border:1px solid #21262d;border-radius:10px;padding:12px 14px}
.stat .v{font-size:24px;font-weight:800;line-height:1.2}
.stat .k{font-size:12px;color:#7d8590;margin-top:2px}
.stat .v.green{color:#3fb950}.stat .v.red{color:#f85149}.stat .v.purple{color:#a78bfa}.stat .v.yellow{color:#d29922}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #21262d}
th{color:#7d8590;font-weight:600;font-size:12px}
.blocked{color:#f85149}.executed{color:#3fb950}.dry{color:#d29922}
</style></head><body>
<h1>💰 SpendShield <span>审计 Dashboard</span></h1>
<div class="sub" id="meta">加载中...</div>
<div class="stats" id="stats"></div>
<table id="tbl"><thead><tr>
<th>时间</th><th>操作</th><th>金额</th><th>收款方</th><th>决策</th><th>原因</th><th>累计已花</th>
</tr></thead><tbody></tbody></table>
<script>
async function load(){
  const r=await fetch('/api/audit');const d=await r.json();
  const recs=d.records||[];
  document.getElementById('meta').textContent=`${recs.length} 条记录 · 更新于 ${new Date().toLocaleTimeString()} · 30s 自动刷新`;
  const executed=recs.filter(x=>x.decision==='executed');
  const blocked=recs.filter(x=>x.decision.startsWith('blocked')||x.decision==='dry_run');
  const spent=executed.reduce((s,x)=>s+(x.amount||0),0);
  const maxAmt=Math.max(...recs.map(x=>x.amount||0),0);
  document.getElementById('stats').innerHTML=
   `<div class="stat"><div class="v purple">${recs.length}</div><div class="k">总尝试</div></div>
    <div class="stat"><div class="v green">${executed.length}</div><div class="k">已放行</div></div>
    <div class="stat"><div class="v red">${blocked.length}</div><div class="k">被拦截</div></div>
    <div class="stat"><div class="v">¥${spent.toFixed(2)}</div><div class="k">累计已花</div></div>
    <div class="stat"><div class="v yellow">¥${maxAmt.toFixed(2)}</div><div class="k">最大单笔</div></div>`;
  const tb=document.querySelector('#tbl tbody');tb.innerHTML='';
  for(const r of recs){
    const cls=r.decision==='executed'?'executed':(r.decision==='dry_run'?'dry':'blocked');
    const t=new Date(r.ts*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});
    tb.insertAdjacentHTML('beforeend',
      `<tr><td>${t}</td><td>${r.action}</td><td>¥${(r.amount||0).toFixed(2)}</td><td>${r.to}</td>
       <td class="${cls}">${r.decision}</td><td style="color:#9da7b3">${r.reason||''}</td><td>¥${(r.spent_after||0).toFixed(2)}</td></tr>`);
  }
}
load();setInterval(load,30000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    file = "audit.json"

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/audit":
            try:
                with open(self.file, encoding="utf-8") as f:
                    records = json.load(f)
            except Exception:
                records = []
            self._json({"records": records})
        else:
            self._json({"error": "not found"})

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(prog="spendshield-dashboard")
    ap.add_argument("--file", default="audit.json", help="审计 JSON 文件")
    ap.add_argument("--port", type=int, default=8775)
    args = ap.parse_args()
    Handler.file = args.file
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"SpendShield 审计看板: http://localhost:{args.port}  (file={args.file})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
