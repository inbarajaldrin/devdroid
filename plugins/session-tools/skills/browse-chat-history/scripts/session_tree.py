#!/usr/bin/env python3
"""session_tree.py — render a Claude Code session's FULL branch tree (every rewind
fork / abandoned arm) as a self-contained interactive HTML page.

Collapses linear runs between fork points into single edges (git-graph style), so a
2000-record, 9-fork session shows as a readable tree of branch points + leaves.
Each leaf shows its depth, last user prompt, recency, and uuid; the deepest arm
(the "full chat") is highlighted, and you can click any node to expand/collapse.

Usage:
    session_tree.py <session.jsonl> <out.html> [--title "..."]
"""
from __future__ import annotations

import json, sys, html, datetime

def load(path):
    recs=[]
    for line in open(path, encoding="utf-8"):
        line=line.strip()
        if not line: continue
        try: d=json.loads(line)
        except: continue
        if isinstance(d, dict): recs.append(d)
    return recs

def user_text(d):
    if d.get("type")!="user": return ""
    m=d.get("message") or {}; c=m.get("content")
    if isinstance(c,str): t=c
    elif isinstance(c,list):
        t=" ".join(x.get("text","") for x in c if isinstance(x,dict) and x.get("type")=="text")
    else: t=""
    t=t.strip()
    if not t or t.startswith("<") or "tool_result" in t or "[Request interrupted" in t: return ""
    return " ".join(t.split())

def asst_text(d):
    if d.get("type")!="assistant": return ""
    for x in (d.get("message") or {}).get("content") or []:
        if isinstance(x,dict) and x.get("type")=="text" and x.get("text","").strip():
            return " ".join(x["text"].split())
    return ""

def build(path, title=None):
    recs=load(path)
    by={d["uuid"]:d for d in recs if d.get("uuid")}
    kids={}
    for d in recs:
        p=d.get("parentUuid")
        if d.get("uuid"): kids.setdefault(p,[]).append(d["uuid"])
    roots=[d["uuid"] for d in recs if d.get("uuid") and (d.get("parentUuid") not in by)]
    # de-dup roots preserving order
    seen=set(); roots=[r for r in roots if not (r in seen or seen.add(r))]

    # depth of each uuid (chain length to a root)
    depthcache={}
    def depth(u):
        if u in depthcache: return depthcache[u]
        n=0; cur=u; s=set()
        while cur in by and cur not in s:
            s.add(cur); n+=1; cur=by[cur].get("parentUuid")
        depthcache[u]=n; return n
    leaves=[u for u in by if not kids.get(u)]
    deepest=max(leaves, key=depth) if leaves else None
    # ancestor set of deepest (the full-chat spine) for highlighting
    spine=set(); cur=deepest
    while cur in by:
        spine.add(cur); cur=by[cur].get("parentUuid")

    maxd=max((depth(l) for l in leaves), default=1)

    def seg_node(start):
        # collapse a linear run starting at `start` until a fork (>1 child) or leaf
        chain=[]; cur=start
        while True:
            chain.append(cur)
            ch=kids.get(cur,[])
            if len(ch)==1: cur=ch[0]; continue
            break
        end=chain[-1]; ch=kids.get(end,[])
        # representative prompt: last user prompt in the run, else last assistant text
        up=""; ap=""
        for u in chain:
            d=by[u]
            t=user_text(d)
            if t: up=t
            else:
                a=asst_text(d)
                if a: ap=a
        label=up or (("⤷ "+ap) if ap else by[end].get("type","·"))
        d=by[end]
        node={
            "id":end[:8], "uuid":end, "len":len(chain), "depth":depth(end),
            "ts":d.get("timestamp",""), "type":d.get("type"),
            "role":(d.get("message") or {}).get("role"),
            "label":label[:140], "full":(up or ap or ""),
            "leaf":len(ch)==0, "onspine":end in spine,
            "children":[seg_node(c) for c in ch],
        }
        return node

    if len(roots)==1:
        tree=seg_node(roots[0])
    else:
        tree={"id":"root","uuid":"","len":0,"depth":0,"ts":"","type":"root","role":None,
              "label":"(session root)","full":"","leaf":False,"onspine":True,
              "children":[seg_node(r) for r in roots]}

    meta={"title":title or "session", "records":len(recs), "leaves":len(leaves),
          "forks":sum(1 for u in by if len(kids.get(u,[]))>1), "maxdepth":maxd,
          "deepest":(deepest or "")[:8], "deepest_full":deepest or ""}
    return tree, meta

HTML=r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>__TITLE__ · branch tree</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<style>
 :root{--bg:#0d1117;--fg:#e6edf3;--mut:#8b949e;--line:#30363d;--spine:#3fb950;--leaf:#58a6ff;--fork:#d29922}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
 header{padding:12px 18px;border-bottom:1px solid var(--line);display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
 header h1{font-size:15px;margin:0;font-weight:600}
 header .k{color:var(--mut)} header .v{color:var(--fg);font-weight:600}
 .legend{margin-left:auto;display:flex;gap:14px;color:var(--mut)}
 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle}
 #wrap{position:absolute;top:54px;left:0;right:0;bottom:0;overflow:auto}
 svg{font:12px ui-monospace,monospace}
 .link{fill:none;stroke:var(--line);stroke-width:1.4px}
 .link.spine{stroke:var(--spine);stroke-width:2.2px;stroke-opacity:.8}
 .node circle{stroke-width:1.5px;cursor:pointer}
 .node text{fill:var(--fg)} .node .meta{fill:var(--mut)}
 #tip{position:fixed;pointer-events:none;background:#161b22;border:1px solid var(--line);border-radius:6px;
      padding:8px 10px;max-width:460px;color:var(--fg);opacity:0;transition:opacity .1s;box-shadow:0 6px 24px #000a;z-index:9}
 #tip b{color:var(--leaf)} #tip .p{color:var(--fg);margin-top:5px;white-space:pre-wrap}
 #tip .m{color:var(--mut);font-size:11px}
</style></head><body>
<header>
 <h1>__TITLE__</h1>
 <span><span class="k">records</span> <span class="v">__RECORDS__</span></span>
 <span><span class="k">leaves</span> <span class="v">__LEAVES__</span></span>
 <span><span class="k">rewind forks</span> <span class="v">__FORKS__</span></span>
 <span><span class="k">max depth</span> <span class="v">__MAXDEPTH__</span></span>
 <div class="legend">
   <span><span class="dot" style="background:var(--spine)"></span>full-chat spine (deepest)</span>
   <span><span class="dot" style="background:var(--leaf)"></span>leaf</span>
   <span><span class="dot" style="background:var(--fork)"></span>fork point</span>
   <span class="k">· click node to fold/unfold · hover for prompt</span>
 </div>
</header>
<div id="wrap"><svg id="svg"></svg></div>
<div id="tip"></div>
<script>
const DATA=__DATA__, META=__META__;
const dx=240, dy=72;   // dx = horizontal spacing between siblings, dy = vertical depth step
const root=d3.hierarchy(DATA);
let i=0; root.x0=0; root.y0=0;
root.descendants().forEach(d=>{d.id=++i; d._children=d.children;
  if(d.depth && d.data.children && d.data.children.length<=1) {} });
const svg=d3.select("#svg"), gLink=svg.append("g"), gNode=svg.append("g");
const tip=d3.select("#tip");
const tree=d3.tree().nodeSize([dx,dy]).separation((a,b)=>(a.parent==b.parent?1:1.25));
const diag=d3.linkVertical().x(d=>d.x).y(d=>d.y);
function recency(ts){ if(!ts) return .4; const t=Date.parse(ts); const span=META.tspan||1;
  return META.tmax? Math.max(.25,Math.min(1,1-(META.tmax-t)/ (1000*60*180))):.6; }
function color(d){ if(d.data.type==='root') return '#6e7681';
  if(d.data.leaf) return d.data.onspine? 'var(--spine)':'var(--leaf)';
  return (d.data.children&&d.data.children.length>1)? 'var(--fork)':'#6e7681'; }
function update(src){
  tree(root);
  let lo=root,hi=root; root.eachBefore(n=>{if(n.x<lo.x)lo=n; if(n.x>hi.x)hi=n;});
  const w=hi.x-lo.x+380; const h=(root.height+1)*dy+140;
  svg.attr("viewBox",[ lo.x-190, -60, w, h ]).attr("width",w).attr("height",h);
  const nodes=root.descendants(), links=root.links();
  const node=gNode.selectAll("g.node").data(nodes,d=>d.id);
  const nE=node.enter().append("g").attr("class","node")
    .attr("transform",d=>`translate(${src.x0},${src.y0})`)
    .on("click",(e,d)=>{d.children=d.children?null:d._children; update(d);})
    .on("mousemove",(e,d)=>{tip.style("opacity",1).style("left",(e.clientX+14)+"px").style("top",(e.clientY+12)+"px")
       .html(`<b>${d.data.id}</b> <span class="m">depth ${d.data.depth} · +${d.data.len} msg${d.data.leaf?' · LEAF':''}${d.data.onspine?' · spine':''}</span>`
         +`<div class="m">${d.data.ts||''}</div><div class="p">${(d.data.full||d.data.label||'').replace(/</g,'&lt;')}</div>`);})
    .on("mouseleave",()=>tip.style("opacity",0));
  nE.append("circle").attr("r",d=>d.data.leaf?5.5:4.5).attr("fill",color)
     .attr("stroke",d=>d.data.onspine?'var(--spine)':'#0d1117');
  nE.append("text").attr("dy","0.32em").attr("x",10).attr("text-anchor","start")
     .text(d=>{const L=d.data.label||''; return (d.data.leaf?'◖ ':'')+ (L.length>52?L.slice(0,52)+'…':L);})
     .clone(true).lower().attr("stroke","#0d1117").attr("stroke-width",3);
  nE.append("text").attr("class","meta").attr("dy","1.5em").attr("x",10).attr("text-anchor","start")
     .text(d=>d.data.leaf?`leaf ${d.data.id} · d${d.data.depth}`:(d.data.children&&d.data.children.length>1?`⑂ ${d.data.children.length} forks`:''));
  node.merge(nE).transition().duration(250).attr("transform",d=>`translate(${d.x},${d.y})`);
  node.exit().transition().duration(250).attr("transform",d=>`translate(${src.x},${src.y})`).remove();
  const link=gLink.selectAll("path").data(links,d=>d.target.id);
  const lE=link.enter().append("path").attr("class",d=>"link"+(d.target.data.onspine?" spine":""))
    .attr("d",d=>{const o={x:src.x0,y:src.y0};return diag({source:o,target:o});});
  link.merge(lE).transition().duration(250).attr("d",diag)
    .attr("class",d=>"link"+(d.target.data.onspine?" spine":""));
  link.exit().remove();
  root.eachBefore(d=>{d.x0=d.x;d.y0=d.y;});
}
// recency scale
const ts=[]; root.each(d=>{if(d.data.ts)ts.push(Date.parse(d.data.ts));});
META.tmax=Math.max(...ts); META.tmin=Math.min(...ts); META.tspan=META.tmax-META.tmin||1;
update(root);
</script></body></html>"""

def main():
    src=sys.argv[1]; out=sys.argv[2]
    title=None
    if "--title" in sys.argv: title=sys.argv[sys.argv.index("--title")+1]
    tree, meta=build(src, title)
    page=(HTML.replace("__TITLE__", html.escape(meta["title"]))
              .replace("__RECORDS__", str(meta["records"]))
              .replace("__LEAVES__", str(meta["leaves"]))
              .replace("__FORKS__", str(meta["forks"]))
              .replace("__MAXDEPTH__", str(meta["maxdepth"]))
              .replace("__DATA__", json.dumps(tree).replace("</","<\\/"))
              .replace("__META__", json.dumps(meta).replace("</","<\\/")))
    open(out,"w").write(page)
    print(f"wrote {out}  ({meta['records']} records, {meta['leaves']} leaves, {meta['forks']} forks, deepest={meta['deepest']})")

if __name__=="__main__": main()
