"""A small upload page: drop the month's files, get the report back.

It is a convenience for people who do not use a terminal. The scheduled run (cron) uses
the command line, not this server.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from . import synth
from .cli import run_report
from .readers import SUPPORTED
from .report import tiles

RUNS = Path(os.environ.get("SHEETAGENT_RUNS", "runs"))
MAX_BYTES = 20 * 1024 * 1024
_RUN_ID = re.compile(r"^[0-9a-f]{12}$")
_SHEETS = re.compile(r"^https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+")

app = FastAPI(title="Spreadsheet report agent", version="1.0.0")

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Spreadsheet report agent</title>
<style>
:root{color-scheme:light dark;--page:#f4f3f0;--surface:#fcfcfb;--text:#0b0b0b;--muted:#52514e;--line:#e7e6e2;--accent:#2a78d6}
@media (prefers-color-scheme:dark){:root{--page:#111110;--surface:#1a1a19;--text:#f0efec;--muted:#c3c2b7;--line:#33332f;--accent:#3987e5}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--text);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 64px}h1{font-size:24px;margin:0 0 4px}p.lead{color:var(--muted);margin:0 0 20px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:20px;margin-top:16px}
label{display:block;font-weight:600;margin:12px 0 4px}input[type=text],textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--page);color:var(--text);font:inherit}
#drop{border:2px dashed var(--line);border-radius:10px;padding:28px;text-align:center;color:var(--muted);cursor:pointer}
#drop.over{border-color:var(--accent)}button{font:inherit;padding:9px 16px;border-radius:6px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer;margin:16px 8px 0 0}
button.ghost{background:transparent;color:var(--text);border-color:var(--line)}button:disabled{opacity:.5;cursor:wait}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}.tile{border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.tile small{color:var(--muted);display:block}.tile b{font-size:20px}table{border-collapse:collapse;width:100%;font-size:14px}td{border-bottom:1px solid var(--line);padding:5px 8px 5px 0;vertical-align:top}
a{color:var(--accent)}.warn{color:var(--muted)}#files{font-size:13px;color:var(--muted);margin-top:8px}
</style></head><body><main>
<h1>Spreadsheet report agent</h1>
<p class="lead">Drop this month's sales exports, the product list and the targets. Excel and CSV files are read as they are: no template to follow.</p>
<div class="card">
  <div id="drop">Drop files here or click to choose (.xlsx, .csv)</div>
  <input id="picker" type="file" multiple accept=".xlsx,.xlsm,.csv,.tsv,.txt" hidden>
  <div id="files"></div>
  <label for="links">Google Sheets links (optional, one per line, shared as "anyone with the link")</label>
  <textarea id="links" rows="2"></textarea>
  <label for="period">Report month (optional, YYYY-MM; found from the data when empty)</label>
  <input id="period" type="text" placeholder="2026-09">
  <button id="run">Build the report</button><button id="demo" class="ghost">Try with the demo files</button>
</div>
<div id="result"></div>
<script>
const drop=document.getElementById('drop'),picker=document.getElementById('picker'),list=document.getElementById('files');
let chosen=[];
function show(){list.textContent=chosen.length?chosen.map(f=>f.name).join(', '):''}
drop.onclick=()=>picker.click();picker.onchange=()=>{chosen=[...picker.files];show()};
drop.ondragover=e=>{e.preventDefault();drop.classList.add('over')};drop.ondragleave=()=>drop.classList.remove('over');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');chosen=[...e.dataTransfer.files];show()};
function cell(tr,text){const td=document.createElement('td');td.textContent=text;tr.appendChild(td)}
function render(d){
  const out=document.getElementById('result');out.replaceChildren();
  const card=document.createElement('div');card.className='card';out.appendChild(card);
  if(!d.ok){const p=document.createElement('p');p.textContent=d.detail||d.message||'No report could be built.';card.appendChild(p);
    (d.warnings||[]).forEach(w=>{const q=document.createElement('p');q.className='warn';q.textContent=w;card.appendChild(q)});return}
  const h=document.createElement('h2');h.textContent='Report for '+d.period_label;card.appendChild(h);
  const p=document.createElement('p');p.textContent=d.summary;card.appendChild(p);
  const tiles=document.createElement('div');tiles.className='tiles';card.appendChild(tiles);
  d.tiles.forEach(t=>{const el=document.createElement('div');el.className='tile';const s=document.createElement('small');s.textContent=t.label;
    const b=document.createElement('b');b.textContent=t.value;const n=document.createElement('small');n.textContent=t.note;el.append(s,b,n);tiles.appendChild(el)});
  const links=document.createElement('p');[['Open the web report',d.html],['Download the Excel report',d.excel]].forEach(([text,href])=>{
    const a=document.createElement('a');a.href=href;a.textContent=text;a.target='_blank';links.append(a,' \\u00a0 ')});card.appendChild(links);
  const table=document.createElement('table');d.trace.forEach(s=>{const tr=document.createElement('tr');cell(tr,s.step);cell(tr,s.detail);table.appendChild(tr)});card.appendChild(table);
  d.warnings.forEach(w=>{const q=document.createElement('p');q.className='warn';q.textContent='Note: '+w;card.appendChild(q)});
}
async function send(url,body){
  const buttons=document.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);
  try{const r=await fetch(url,{method:'POST',body});render(await r.json())}
  catch(e){render({ok:false,message:'The server did not answer: '+e})}
  finally{buttons.forEach(b=>b.disabled=false)}
}
document.getElementById('run').onclick=()=>{const fd=new FormData();chosen.forEach(f=>fd.append('files',f));
  fd.append('period',document.getElementById('period').value);fd.append('links',document.getElementById('links').value);send('/api/run',fd)};
document.getElementById('demo').onclick=()=>send('/api/demo',new FormData());
// A link ending in #demo runs the demo straight away, handy for showing the tool to someone.
if(location.hash==='#demo')document.getElementById('demo').click();
</script></main></body></html>"""


def _payload(run, run_id: str) -> dict:
    if not run.ok:
        return {"ok": False, "message": "No usable sales lines were found in these files.", "warnings": run.warnings}
    names = {key: Path(path).name for key, path in run.outputs.items()}
    return {"ok": True, "run_id": run_id, "period": run.period, "period_label": run.facts["period_label"],
            "summary": run.summary.text, "summary_source": run.summary.source, "tiles": tiles(run.facts),
            "trace": run.trace, "warnings": run.warnings, "quality": run.facts["quality"],
            "html": f"/runs/{run_id}/{run.period}/{names['html']}", "excel": f"/runs/{run_id}/{run.period}/{names['excel']}"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/run")
async def api_run(files: list[UploadFile] = File(default=[]), period: str = Form(default=""),
                  links: str = Form(default="")) -> dict:
    period = period.strip()
    if period and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise HTTPException(422, "The report month must look like 2026-09.")
    # The server downloads these links itself, so only Google Sheets addresses are accepted.
    urls = [line.strip() for line in links.splitlines() if line.strip()]
    if any(not _SHEETS.match(u) for u in urls):
        raise HTTPException(422, "Links must be Google Sheets addresses (https://docs.google.com/spreadsheets/d/...).")
    if not files and not urls:
        raise HTTPException(422, "Add at least one file or one link.")
    run_id = uuid.uuid4().hex[:12]
    inbox = RUNS / run_id / "inbox"
    inbox.mkdir(parents=True)
    for upload in files:
        name = Path(upload.filename or "file").name  # drops any folder part sent by the browser
        if Path(name).suffix.lower() not in SUPPORTED:
            raise HTTPException(422, f"{name}: only Excel (.xlsx) and CSV files are accepted.")
        data = await upload.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"{name}: larger than 20 MB.")
        (inbox / name).write_bytes(data)
    run = run_report([str(inbox), *urls], period or None, RUNS / run_id, quiet=True)
    return _payload(run, run_id)


@app.post("/api/demo")
def api_demo() -> dict:
    run_id = uuid.uuid4().hex[:12]
    data = RUNS / run_id / "data"
    synth.write_demo(data)
    run = None
    for period in synth.DEMO_PERIODS:  # August first, so that September has a month to compare with
        run = run_report([str(data / "inbox" / period), str(data / "reference")], period, RUNS / run_id,
                         title="Cedar & Stone - monthly sales report", quiet=True)
    return _payload(run, run_id)


@app.get("/runs/{run_id}/{period}/{name}")
def download(run_id: str, period: str, name: str) -> FileResponse:
    if not _RUN_ID.match(run_id) or not re.fullmatch(r"\d{4}-\d{2}", period) or \
            not re.fullmatch(r"sales_report_\d{4}-\d{2}\.(xlsx|html)", name):
        raise HTTPException(404)
    path = RUNS / run_id / period / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=name if name.endswith(".xlsx") else None)
