#!/usr/bin/env python3
"""Validate evidence/asset traceability and report affected slides, without inferring science."""
import argparse
import json
import math
from pathlib import Path
import re
import sys
from audit_files import sha256, sniff

DOCS = ("00_RESEARCH_OVERVIEW.md", "04_RETRIEVAL_LOG.md", "05_GAPS_AND_CONFLICTS.md", "06_HANDOFF_TO_PROMPT3.md")
SCI = ("VERIFIED","USER_PROVIDED","INFERRED","HYPOTHESIS","TO_RETRIEVE","BLOCKED")
READY_E = ("VERIFIED","USER_PROVIDED")


def need(ok, msg):
    if not ok: raise ValueError(msg)


def text(x): return isinstance(x,str) and bool(x.strip())
def nat(x): return type(x) is int and x > 0
def valid_id(x, prefix): return isinstance(x,str) and re.fullmatch(rf"(?:{prefix})\d{{3,}}",x) is not None


def fields(row, keys, name):
    need(isinstance(row,dict),f"{name}: expected object")
    missing=[k for k in keys if k not in row]
    need(not missing,f"{name}: missing {','.join(missing)}")


def texts(row, keys, name):
    fields(row,keys,name)
    need(all(text(row[k]) for k in keys),f"{name}: empty text field")


def strings(x,name,nonempty=False):
    need(isinstance(x,list) and all(text(v) for v in x) and (x or not nonempty),f"{name}: expected text array")


def idlist(x,prefix,name):
    need(isinstance(x,list) and all(valid_id(v,prefix) for v in x),f"{name}: invalid ID list")
    need(len(x)==len(set(x)),f"{name}: repeated IDs")


def unique(pairs):
    out={}
    for k,v in pairs:
        need(k not in out,f"duplicate JSON key: {k}"); out[k]=v
    return out


def load(path):
    obj=json.loads(path.read_text(encoding="utf-8-sig"),object_pairs_hook=unique)
    need(isinstance(obj,dict),f"{path.name}: expected object"); return obj


def rows(value,key,prefix):
    need(isinstance(value,list),f"{key}: expected array")
    out={}
    for row in value:
        fields(row,[key],key); rid=row[key]
        need(valid_id(rid,prefix) and rid not in out,f"{key}: bad/duplicate ID {rid}")
        need(type(row.get("retired",False)) is bool,f"{rid}: retired must be boolean")
        out[rid]=row
    return out


def active(records): return {k:v for k,v in records.items() if not v.get("retired")}


def links(items, records, prefix, name, nonempty=False):
    idlist(items,prefix,name)
    need(not nonempty or items,f"{name}: at least one link is required")
    need(all(k in records and not records[k].get("retired") for k in items),f"{name}: unknown/retired ID")


def source_origin(origin):
    return "user" if origin.startswith("user_") else "literature" if origin in ("primary_research","review") else "other"


def check(folder, handoff=None, production=False):
    for doc in DOCS:
        need(text((folder/doc).read_text(encoding="utf-8-sig")),f"{doc}: empty")
    sources=rows(load(folder/"01_SOURCE_MANIFEST.json").get("sources"),"source_id","SRC")
    e=load(folder/"02_EVIDENCE_MANIFEST.json")
    fields(e,"project_id base_revision package_revision mode scope target_slide_ids claim_ids evidence tasks comparisons slides".split(),"evidence package")
    need(text(e["project_id"]) and nat(e["package_revision"]),"invalid project/package revision")
    need(e["base_revision"] is None or nat(e["base_revision"]),"invalid base revision")
    need(e["mode"] in ("FULL_RESEARCH","GIVEN_FIRST_AUDIT","TARGETED_RETRIEVAL","EVIDENCE_UPDATE"),"invalid mode")
    need(e["scope"] in ("full","targeted"),"invalid scope")
    need(e["mode"]!="TARGETED_RETRIEVAL" or e["scope"]=="targeted","targeted mode needs targeted scope")
    idlist(e["target_slide_ids"],"S","target_slide_ids"); idlist(e["claim_ids"],"C","claim_ids")
    claims=set(e["claim_ids"])
    evidence=rows(e["evidence"],"evidence_id","E")
    assets=rows(load(folder/"03_ASSET_MANIFEST.json").get("assets"),"asset_id","A")
    tasks=rows(e["tasks"],"task_id","R")
    comparisons=rows(e["comparisons"],"comparison_id","CMP")
    slides=rows(e["slides"],"slide_id","S")
    live_slides=active(slides)
    need(list(live_slides)==e["target_slide_ids"],"slide list/order differs from target_slide_ids")

    for sid,s in active(sources).items():
        texts(s,"title url_or_path usage_notes".split(),sid)
        fields(s,["origin","read_scope","verification_status","metadata"],sid)
        need(s["origin"] in ("user_raw","user_processed","user_statement","primary_research","review","other"),f"{sid}: invalid origin")
        need(s["read_scope"] in ("metadata","abstract","section","full_text","data","user_notes"),f"{sid}: invalid read scope")
        need(s["verification_status"] in ("VERIFIED","USER_PROVIDED","TO_VERIFY","UNAVAILABLE"),f"{sid}: invalid verification status")
        if s.get("sha256") is not None:
            need(isinstance(s["sha256"],str) and re.fullmatch("[0-9a-f]{64}",s["sha256"]) is not None,f"{sid}: invalid source SHA-256")
            local=s.get("local_path",s["url_or_path"])
            need(text(local) and not local.startswith(("http://","https://")),f"{sid}: source hash needs local_path")
            file=folder/local
            need(file.is_file() and sha256(file)==s["sha256"],f"{sid}: source file missing or changed")
        meta=s["metadata"]; fields(meta,"authors year doi publication peer_reviewed".split(),sid)
        need(meta["peer_reviewed"] is None or type(meta["peer_reviewed"]) is bool,f"{sid}: peer_reviewed must be boolean or null")

    for eid,r in active(evidence).items():
        fields(r,"source_id origin status verification_scope locator subject method finding measurements conditions controls limitations alternative_explanations applicability claim_links slide_ids task_ids".split(),eid)
        texts(r,"verification_scope subject method finding applicability".split(),eid)
        links([r["source_id"]],sources,"SRC",eid)
        src=sources[r["source_id"]]
        need(r["origin"]==source_origin(src["origin"]),f"{eid}: evidence origin disagrees with source")
        need(r["status"] in SCI,f"{eid}: invalid status")
        if r["status"] in READY_E:
            need(text(r["locator"]),f"{eid}: verified/provided evidence needs exact locator")
            need(src["read_scope"]!="metadata" and src["verification_status"] in ("VERIFIED","USER_PROVIDED"),
                 f"{eid}: source not read beyond metadata or not checked")
        if r["status"]=="VERIFIED":
            need(src["verification_status"]=="VERIFIED",f"{eid}: verified evidence needs verified source scope")
        for key in ("conditions","controls","limitations","alternative_explanations"):
            strings(r[key],eid+" "+key,True)
        need(isinstance(r["measurements"],list),f"{eid}: measurements must be array")
        for measurement in r["measurements"]:
            fields(measurement,["value","unit","definition"],eid)
            texts(measurement,["unit","definition"],eid)
            value=measurement["value"]
            need(text(value) or (type(value) in (int,float) and math.isfinite(value)),
                 f"{eid}: measurement must be finite or an explicit qualified value")
        need(isinstance(r["claim_links"],list) and r["claim_links"],f"{eid}: needs claim links")
        seen=set()
        for c in r["claim_links"]:
            fields(c,["claim_id","relation"],eid)
            cid=c["claim_id"]
            need(valid_id(cid,"C") and cid in claims and cid not in seen,f"{eid}: unknown/repeated claim")
            seen.add(cid)
            need(c["relation"] in ("supports","opposes","context"),f"{eid}: invalid relation")
        links(r["slide_ids"],slides,"S",eid); links(r["task_ids"],tasks,"R",eid)

    for aid,a in active(assets).items():
        fields(a,"source_id evidence_ids slide_ids status source_locator path sha256 format caption conditions rights scientific_immutable transformation".split(),aid)
        links([a["source_id"]],sources,"SRC",aid)
        links(a["evidence_ids"],evidence,"E",aid,True); links(a["slide_ids"],slides,"S",aid)
        need(a["source_id"] in {evidence[x]["source_id"] for x in a["evidence_ids"]},f"{aid}: main source missing from evidence")
        texts(a,["source_locator","caption"],aid)
        strings(a["conditions"],aid+" conditions",True); strings(a["scientific_immutable"],aid+" immutable",True)
        need(a["status"] in ("LOCATED","ACQUIRED","UNAVAILABLE"),f"{aid}: invalid acquisition status")
        rights=a["rights"]; fields(rights,["status","basis","attribution"],aid)
        need(rights["status"] in ("ALLOWED","REFERENCE_ONLY","NEEDS_PERMISSION","UNKNOWN"),f"{aid}: invalid rights")
        need(text(rights["basis"]) and isinstance(rights["attribution"],str),f"{aid}: missing rights basis/attribution")
        tr=a["transformation"]; texts(tr,["kind","basis"],aid)
        need(tr["kind"] in ("original","redrawn","converted","digitized","schematic"),f"{aid}: invalid transformation")
        if a["status"]=="ACQUIRED":
            texts(a,["path","sha256","format"],aid)
            need(re.fullmatch("[0-9a-f]{64}",a["sha256"]) is not None,f"{aid}: invalid SHA-256")
            path=folder/a["path"]
            need(path.is_file(),f"{aid}: acquired file missing")
            need(sha256(path)==a["sha256"],f"{aid}: file hash mismatch")
            actual=sniff(path)
            if a["format"].lower() in ("pdf","png","jpeg","jpg","gif"):
                need(actual==a["format"].lower().replace("jpg","jpeg"),f"{aid}: file signature differs from format")
        else:
            need(a["path"] is None and a["sha256"] is None,f"{aid}: unacquired asset must not claim a local file")

    for rid,t in active(tasks).items():
        fields(t,["status","evidence_ids","asset_ids","resolution"],rid)
        need(t["status"] in ("OPEN","IN_PROGRESS","DONE","BLOCKED"),f"{rid}: invalid task status")
        links(t["evidence_ids"],evidence,"E",rid); links(t["asset_ids"],assets,"A",rid)
        if t["status"]=="DONE":
            need(text(t["resolution"]),f"{rid}: DONE requires a result or no-result resolution")

    for cid,c in active(comparisons).items():
        fields(c,["evidence_ids","status","dimensions","rationale","permitted_use"],cid)
        links(c["evidence_ids"],evidence,"E",cid,True)
        need(len(c["evidence_ids"])>=2,f"{cid}: comparison needs at least two evidence records")
        need(c["status"] in ("DIRECTLY_COMPARABLE","CONDITIONAL","NOT_COMPARABLE","UNKNOWN"),f"{cid}: invalid comparison status")
        texts(c["dimensions"],"metric unit conditions controls statistics scope".split(),cid)
        texts(c,["rationale","permitted_use"],cid)
        need(c["permitted_use"] in ("rank","qualified","separate","none"),f"{cid}: invalid permitted use")
        need(c["permitted_use"]!="rank" or c["status"]=="DIRECTLY_COMPARABLE",f"{cid}: ranking requires directly comparable evidence")


    up=load(handoff) if handoff is not None else None
    if up is not None:
        need(e["project_id"]==up.get("project_id") and e["base_revision"] is not None
             and e["base_revision"]==up.get("revision"),"package is out of sync with upstream project/revision")
        ur=up.get("records"); need(isinstance(ur,dict),"upstream records missing")
        us=up.get("slide_order"); idlist(us,"S","upstream slide_order")
        expected=us if e["scope"]=="full" else [s for s in us if s in e["target_slide_ids"]]
        need(expected==e["target_slide_ids"],"target slides differ from upstream scope/order")
        for key in claims | set(active(tasks)) | set(live_slides):
            need(key in ur and not ur[key].get("retired"),f"{key}: missing/retired upstream ID")
        # New E records are returned as proposals; collisions with existing E must keep their source.
        for eid,r in active(evidence).items():
            if eid in ur:
                old=ur[eid]
                need(not old.get("retired"),f"{eid}: upstream ID is retired")
                if old.get("source") is not None:
                    new_source=sources[r["source_id"]]["url_or_path"]
                    need(old["source"]==new_source or r.get("upstream_source") == old["source"],
                         f"{eid}: existing upstream E needs explicit original-source linkage")

    for sid,s in live_slides.items():
        texts(s,["scientific_message","asset_rationale"],sid)
        fields(s,"status claims evidence_ids asset_ids visual_ids asset_requirement must_preserve prohibited_inferences gaps comparison_ids".split(),sid)
        need(s["status"] in ("READY","PROVISIONAL","BLOCKED"),f"{sid}: invalid status")
        for key in ("must_preserve","prohibited_inferences","gaps"): strings(s[key],sid+" "+key)
        links(s["evidence_ids"],evidence,"E",sid); links(s["asset_ids"],assets,"A",sid)
        links(s["comparison_ids"],comparisons,"CMP",sid)
        idlist(s["visual_ids"],"V",sid)
        need(s["asset_requirement"] in ("required","optional","none"),f"{sid}: invalid asset requirement")
        need(isinstance(s["claims"],list) and s["claims"],f"{sid}: missing claim treatment")
        seen=set()
        for c in s["claims"]:
            fields(c,["claim_id","purpose","disclosure"],sid)
            cid=c["claim_id"]
            need(valid_id(cid,"C") and cid in claims and cid not in seen,f"{sid}: bad or repeated claim")
            seen.add(cid)
            need(c["purpose"] in ("establish","discuss") and isinstance(c["disclosure"],str),f"{sid}: invalid claim purpose")
            if c["purpose"]=="discuss": need(text(c["disclosure"]),f"{sid}: discussion needs visible qualification")
            if s["status"]=="READY" and c["purpose"]=="establish":
                support=[evidence[x] for x in s["evidence_ids"] if evidence[x]["status"] in READY_E
                         and {"claim_id":cid,"relation":"supports"} in evidence[x]["claim_links"]]
                need(support,f"{sid}: {cid} lacks usable supporting evidence")
                opposed=any({"claim_id":cid,"relation":"opposes"} in r["claim_links"] for r in active(evidence).values())
                if opposed:
                    need(text(c.get("conflict_assessment")) and text(c["disclosure"]),
                         f"{sid}: {cid} has opposing evidence; assess and disclose the conflict")
                if up and ur[cid].get("origin")=="user":
                    need(any(r["origin"]=="user" for r in support),f"{sid}: personal result lacks user evidence")
                if any(r["status"]=="USER_PROVIDED" for r in support):
                    need(text(c["disclosure"]),f"{sid}: user-provided finding needs disclosure")
        for eid in s["evidence_ids"]:
            need(sid in evidence[eid]["slide_ids"],f"{sid}: E backlink missing")
        for aid in s["asset_ids"]:
            need(sid in assets[aid]["slide_ids"],f"{sid}: A backlink missing")
            need(set(assets[aid]["evidence_ids"])<=set(s["evidence_ids"]),f"{sid}: asset evidence missing from slide")
        for cmpid in s["comparison_ids"]:
            need(set(comparisons[cmpid]["evidence_ids"])<=set(s["evidence_ids"]),f"{sid}: comparison evidence missing")
        if s["status"]=="READY":
            need(not s["gaps"],f"{sid}: READY has unresolved gaps")
            if s["asset_requirement"]=="required": need(s["asset_ids"],f"{sid}: required assets missing")
            for aid in s["asset_ids"]:
                need(assets[aid]["status"]=="ACQUIRED" and assets[aid]["rights"]["status"]=="ALLOWED",
                     f"{sid}: asset {aid} not acquired or not cleared for use")
        else: need(s["gaps"],f"{sid}: non-READY slide needs gaps")
        if production: need(s["status"]=="READY",f"{sid}: {s['status']} is not production-ready")
        if up:
            expected={k for k in ur[sid].get("depends_on",[]) if valid_id(k,"V")}
            need(set(s["visual_ids"])==expected,f"{sid}: V IDs differ from upstream")
            need(all(v in ur and not ur[v].get("retired") for v in expected),f"{sid}: missing/retired V")
    if production: need(live_slides,"production validation needs target slides")
    body=(folder/"06_HANDOFF_TO_PROMPT3.md").read_text(encoding="utf-8-sig")
    summary=re.findall(r"(?m)^\|[ \t]*(S\d{3,})[ \t]*\|[ \t]*(READY|PROVISIONAL|BLOCKED)[ \t]*\|",body)
    need(summary==[(sid,s["status"]) for sid,s in live_slides.items()],"handoff summary differs from slide statuses/order")
    return {"header":e,"sources":sources,"evidence":evidence,"assets":assets,"tasks":tasks,
            "comparisons":comparisons,"slides":slides}


def impact(old,new):
    a,b=old["header"],new["header"]
    need(a["project_id"]==b["project_id"] and a["scope"]==b["scope"],"project/scope changed; review as separate package")
    need(b["package_revision"]==a["package_revision"]+1,"package revision must increment by one")
    if a["base_revision"] is not None:
        need(b["base_revision"] is not None and b["base_revision"]>=a["base_revision"],"upstream revision moved backward")
    categories=("sources","evidence","assets","tasks","comparisons","slides")
    changed={}
    for cat in categories:
        before,after=old[cat],new[cat]
        need(not (before.keys()-after.keys()),f"{cat}: prior IDs removed; retire instead")
        for key in before:
            need(not before[key].get("retired") or after[key].get("retired"),f"{key}: retired ID reused")
            identity={"sources":"url_or_path","evidence":"source_id","assets":"source_id"}.get(cat)
            if identity: need(before[key][identity]==after[key][identity],f"{key}: source identity reassigned")
        changed[cat]=sorted(k for k in after if before.get(k)!=after[k])
    edges={}
    def edge(parent,child): edges.setdefault(parent,set()).add(child)
    for p in (old,new):
        for eid,r in active(p["evidence"]).items():
            edge(r["source_id"],eid)
            for rid in r["task_ids"]: edge(rid,eid)
            for sid in r["slide_ids"]: edge(eid,sid)
        for aid,r in active(p["assets"]).items():
            edge(r["source_id"],aid)
            for eid in r["evidence_ids"]: edge(eid,aid)
            for sid in r["slide_ids"]: edge(aid,sid)
        for cmpid,r in active(p["comparisons"]).items():
            for eid in r["evidence_ids"]: edge(eid,cmpid)
        for sid,r in active(p["slides"]).items():
            for key in r["evidence_ids"]+r["asset_ids"]+r["comparison_ids"]: edge(key,sid)
    affected=set(k for keys in changed.values() for k in keys)
    previous_positions={s:i for i,s in enumerate(a["target_slide_ids"])}
    affected.update(s for i,s in enumerate(b["target_slide_ids"]) if previous_positions.get(s)!=i)
    pending=list(affected)
    while pending:
        for child in edges.get(pending.pop(),set())-affected:
            affected.add(child); pending.append(child)
    return {"changed":changed,"affected_slides":[s for s in b["target_slide_ids"] if s in affected],
            "removed_slides":[s for s in a["target_slide_ids"] if s not in b["target_slide_ids"]]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    v=sub.add_parser("validate"); v.add_argument("package",type=Path)
    v.add_argument("--handoff",type=Path); v.add_argument("--production",action="store_true")
    d=sub.add_parser("impact"); d.add_argument("old",type=Path); d.add_argument("new",type=Path)
    args=p.parse_args()
    try:
        if args.command=="impact":
            print(json.dumps(impact(check(args.old),check(args.new)),ensure_ascii=False))
        else:
            pkg=check(args.package,args.handoff,args.production); h=pkg["header"]
            print(f"PASS revision={h['package_revision']} sources={len(active(pkg['sources']))} "
                  f"evidence={len(active(pkg['evidence']))} assets={len(active(pkg['assets']))} "
                  f"slides={len(active(pkg['slides']))} scope={h['scope']} production={str(args.production).lower()}")
        return 0
    except (OSError,ValueError,KeyError) as exc:
        print(f"FAIL {exc}",file=sys.stderr); return 1


if __name__=="__main__":
    sys.exit(main())

