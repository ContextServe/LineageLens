"""Optional local UI and GraphQL contract for tooling clients."""

from __future__ import annotations

import json
from pathlib import Path

import strawberry
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from strawberry.fastapi import GraphQLRouter

from .config import ProjectConfig


@strawberry.type
class Edge:
    relation: str
    source: str
    target: str
    evidence: str
    line: int


@strawberry.type
class CodeSymbol:
    id: str
    kind: str
    file: str
    line: int
    entry_point: str | None
    async_: bool
    inputs: strawberry.scalars.JSON
    outputs: strawberry.scalars.JSON
    risks: strawberry.scalars.JSON


@strawberry.type
class Query:
    @strawberry.field
    def symbol(self, id: str) -> CodeSymbol | None:
        symbols = _DATA["symbols"]
        return _adapt(symbols[id]) if id in symbols else None

    @strawberry.field
    def search(self, text: str, limit: int = 30) -> list[CodeSymbol]:
        term = text.lower()
        return [_adapt(item) for item in _DATA["symbols"].values() if term in item["id"].lower()][:limit]

    @strawberry.field
    def callers(self, id: str) -> list[Edge]:
        return [_edge(item) for item in _DATA["relations"] if item["target"] == id]

    @strawberry.field
    def callees(self, id: str) -> list[Edge]:
        return [_edge(item) for item in _DATA["relations"] if item["source"] == id]


_schema = strawberry.Schema(query=Query)


def _adapt(item: dict) -> CodeSymbol:
    return CodeSymbol(id=item["id"], kind=item["kind"], file=item["file"], line=item["line"], entry_point=item.get("entry_point"), async_=item.get("async_", False), inputs=item.get("inputs", []), outputs=item.get("outputs", []), risks=item.get("risks", []))


def _edge(item: dict) -> Edge:
    return Edge(relation=item["kind"], source=item["source"], target=item["target"], evidence=item["evidence"], line=item["line"])


_DATA: dict = {}


def create_app(project: Path, config: ProjectConfig | None = None):
    config = config or ProjectConfig.load(project)
    graph_file = project / config.output.directory / config.output.filename
    raw = json.loads(graph_file.read_text(encoding="utf-8"))
    _DATA["symbols"] = {item["id"]: item for item in raw["symbols"]}
    _DATA["relations"] = raw["relations"]

    app = FastAPI(title="LineageLens")
    app.include_router(GraphQLRouter(_schema), prefix="/graphql")
    app.get("/", response_class=HTMLResponse)(lambda: HTML)
    return app


HTML = """<!doctype html><title>LineageLens</title><style>body{margin:0;font:15px system-ui;background:#101725;color:#e8eefb;display:grid;grid-template-columns:300px 1fr 420px;height:100vh}aside,main{padding:18px;overflow:auto;border-right:1px solid #29364d}input{box-sizing:border-box;width:100%;padding:10px;background:#182337;color:white;border:1px solid #3a4a68;border-radius:6px}button{display:block;width:100%;margin:6px 0;padding:9px;text-align:left;background:#182337;color:#bcd7ff;border:1px solid #29364d;border-radius:5px}.card{background:#182337;padding:12px;margin:10px 0;border-radius:8px}h1{font-size:20px}h2{font-size:16px}code{color:#a7d4ff;overflow-wrap:anywhere}.muted{color:#98a7bd}</style><aside><h1>LineageLens</h1><input id=q placeholder="Find a route, CLI command, or method"><div id=results></div></aside><main><h2>Trace a flow</h2><p class=muted>Select an entry point, then follow callers and callees. This view emphasizes a decision path rather than a raw node cloud.</p><div id=flow></div></main><aside id=detail><h2>Method dossier</h2><p class=muted>Select a symbol to view its contract, effects, risks, and relationships.</p></aside><script>const api='/graphql';const request=q=>fetch(api,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({query:q})}).then(r=>r.json());const out=document.querySelector('#results'),flow=document.querySelector('#flow'),detail=document.querySelector('#detail');function esc(x){return String(x).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}async function select(id){let q=`{ symbol(id:${JSON.stringify(id)}){id kind file line entryPoint async_ inputs outputs risks} callers(id:${JSON.stringify(id)}){relation source} callees(id:${JSON.stringify(id)}){relation target} }`;let d=(await request(q)).data;let s=d.symbol;detail.innerHTML=`<h2>${esc(s.id)}</h2><div class=card><b>${s.kind}</b> · ${s.async_?'async':'sync'}<br><code>${esc(s.file)}:${s.line}</code></div><h2>Inputs</h2><pre>${esc(JSON.stringify(s.inputs,null,2))}</pre><h2>Outputs</h2><pre>${esc(JSON.stringify(s.outputs,null,2))}</pre><h2>Risks</h2><pre>${esc(JSON.stringify(s.risks,null,2))}</pre>`;flow.innerHTML=`<h2>Call path for ${esc(s.id)}</h2><div class=card><b>Callers</b>${d.callers.map(e=>`<button onclick='select(${JSON.stringify(e.source)})'>${esc(e.relation)} ← ${esc(e.source)}</button>`).join('')||'<p class=muted>Known root or unresolved caller</p>'}</div><div class=card><b>Callees</b>${d.callees.map(e=>`<button onclick='select(${JSON.stringify(e.target)})'>${esc(e.relation)} → ${esc(e.target)}</button>`).join('')||'<p class=muted>No resolved callees</p>'}</div>`}document.querySelector('#q').oninput=async e=>{let t=e.target.value;if(t.length<2){out.innerHTML='';return}let d=(await request(`{search(text:${JSON.stringify(t)}){id kind entryPoint}}`)).data.search;out.innerHTML=d.map(s=>`<button onclick='select(${JSON.stringify(s.id)})'>${esc(s.id)}<br><small>${esc(s.kind)} ${esc(s.entryPoint||'')}</small></button>`).join('')};</script>"""
