"""Benchmark relevance scorers on the replayed smoke notes with manual noise labels.

Usage: python tools/bench_relevance.py [--json out.json]
Optional deps (NOT needed by the pipeline's tests): fastembed, sentence-transformers+torch(cpu).
Scores are 'higher = more relevant'. Reports ROC-AUC, plus precision/recall of noise removal at
(a) the in-sample F1-optimal global threshold and (b) leave-one-task-out thresholds (less optimistic).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "benchmark" / "relevance"
TASKS = ("T02", "T12", "T26")


def load():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    labels = json.loads((ROOT / "smoke-agent-v2.labels.json").read_text("utf-8"))
    data = {}
    for t in TASKS:
        d = json.loads((ROOT / "notes" / f"{t}.json").read_text("utf-8"))
        qtopic = {q["query"]: q["topic"] for q in d["queries"]}
        items = []
        for n in d["notes"]:
            items.append({"id": n["note_id"], "title": n["title"], "text": n["text"],
                          "noise": n["note_id"] in labels[t]["noise"],
                          "subtopic": qtopic.get(n["query"], ""), "kind": n["kind"]})
        data[t] = {"topic": d["topic"], "items": items}
    return data


def doc(n):
    return f"{n['title']}。{n['text'][:300]}" if n["text"] != n["title"] else n["title"]


def auc(scores, noise):
    pos = [s for s, z in zip(scores, noise) if not z]       # relevant
    neg = [s for s, z in zip(scores, noise) if z]
    if not pos or not neg:
        return float("nan")
    w = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return w / (len(pos) * len(neg))


def prf(scores, noise, thr):
    removed = [z for s, z in zip(scores, noise) if s < thr]
    tp = sum(removed)
    n_noise = sum(noise)
    kept_rel_lost = len(removed) - tp
    prec = tp / len(removed) if removed else float("nan")
    rec = tp / n_noise if n_noise else float("nan")
    return prec, rec, kept_rel_lost


def best_thr(scores, noise):
    cands = sorted(set(scores))
    best = (-1, None)
    for c in cands:
        p, r, _ = prf(scores, noise, c)
        if p != p or r != r or (p + r) == 0:
            continue
        f = 2 * p * r / (p + r)
        if f > best[0]:
            best = (f, c)
    return best[1]


def evaluate(name, score_fn, data, variant):
    t0 = time.perf_counter()
    per = {}
    for t, d in data.items():
        per[t] = [float(x) for x in score_fn(d, variant)]
    lat = time.perf_counter() - t0
    n_docs = sum(len(d["items"]) for d in data.values())
    allsc = [s for t in TASKS for s in per[t]]
    allnz = [n["noise"] for t in TASKS for n in data[t]["items"]]
    row = {"scorer": name, "variant": variant, "latency_s_total": round(lat, 2),
           "ms_per_doc": round(1000 * lat / n_docs, 1), "n_docs": n_docs,
           "auc_per_task": {t: round(auc(per[t], [n["noise"] for n in data[t]["items"]]), 3) for t in TASKS},
           "auc_all": round(auc(allsc, allnz), 3)}
    thr = best_thr(allsc, allnz)
    p, r, lost = prf(allsc, allnz, thr)
    row["in_sample"] = {"thr": round(thr, 4), "precision": round(p, 3), "recall": round(r, 3), "relevant_lost": lost}
    # leave-one-task-out
    tp = rem = nn = lost = 0
    for hold in TASKS:
        tr_s = [s for t in TASKS if t != hold for s in per[t]]
        tr_z = [n["noise"] for t in TASKS if t != hold for n in data[t]["items"]]
        th = best_thr(tr_s, tr_z)
        te_z = [n["noise"] for n in data[hold]["items"]]
        removed = [z for s, z in zip(per[hold], te_z) if s < th]
        tp += sum(removed); rem += len(removed); nn += sum(te_z); lost += len(removed) - sum(removed)
    row["loto"] = {"precision": round(tp / rem, 3) if rem else None, "recall": round(tp / nn, 3), "relevant_lost": lost}
    return row, per


def make_fastembed(model):
    from fastembed import TextEmbedding
    m = TextEmbedding(model)
    import numpy as np

    def fn(d, variant):
        q = d["topic"]
        qs = [q if variant == "topic" else f"{q} {n['subtopic']}" for n in d["items"]]
        D = np.array(list(m.embed([doc(n) for n in d["items"]])))
        Q = np.array(list(m.embed(qs)))
        return (D * Q).sum(1) / np.linalg.norm(D, axis=1) / np.linalg.norm(Q, axis=1)
    return fn


def make_cross(model):
    from sentence_transformers import CrossEncoder
    m = CrossEncoder(model, device="cpu", max_length=512)

    def fn(d, variant):
        pairs = [((d["topic"] if variant == "topic" else f"{d['topic']} {n['subtopic']}"), doc(n)) for n in d["items"]]
        return m.predict(pairs, batch_size=8)
    return fn


def sweep(name, fn, data, grid):
    """Scores survivors of the structural layer; table of precision/recall for fixed thresholds."""
    from pptv01.retrieval.relevance import Candidate, structural_check
    surv, s_drop = {}, []
    for t, d in data.items():
        keep = []
        for n in d["items"]:
            v = structural_check(Candidate(n["title"], n["text"], n["kind"], "x", n["subtopic"], ""))
            (keep if v.keep else s_drop).append(n) if v.keep else s_drop.append({**n, "why": v.reason})
            if v.keep:
                pass
        surv[t] = {"topic": d["topic"], "items": [n for n in d["items"] if structural_check(
            Candidate(n["title"], n["text"], n["kind"], "x", n["subtopic"], "")).keep]}
    n_noise = sum(n["noise"] for d in data.values() for n in d["items"])
    n_all = sum(len(d["items"]) for d in data.values())
    sd_noise = sum(n["noise"] for n in s_drop)
    t0 = time.perf_counter()
    sc = {t: [float(x) for x in fn(d, "topic+subtopic")] for t, d in surv.items()}
    lat = time.perf_counter() - t0
    nsurv = sum(len(d["items"]) for d in surv.values())
    allsc = [x for t in TASKS for x in sc[t]]
    allnz = [n["noise"] for t in TASKS for n in surv[t]["items"]]
    res = {"scorer": name, "n_all": n_all, "n_noise_total": n_noise,
           "structural": {"removed": len(s_drop), "noise_removed": sd_noise,
                          "relevant_lost": len(s_drop) - sd_noise,
                          "items": [(n["title"][:30], n["noise"], n.get("why")) for n in s_drop]},
           "survivors": nsurv, "auc_on_survivors": round(auc(allsc, allnz), 3),
           "ms_per_doc": round(1000 * lat / max(nsurv, 1), 1), "sweep": []}
    for thr in grid:
        rm = [z for x, z in zip(allsc, allnz) if x < thr]
        tp = sum(rm)
        tot_rm = len(rm) + len(s_drop); tot_tp = tp + sd_noise
        res["sweep"].append({"thr": thr, "scorer_removed": len(rm), "scorer_noise_removed": tp,
                             "scorer_precision": round(tp / len(rm), 3) if rm else None,
                             "combined_removed": tot_rm, "combined_precision": round(tot_tp / tot_rm, 3) if tot_rm else None,
                             "combined_noise_recall": round(tot_tp / n_noise, 3), "relevant_lost": tot_rm - tot_tp})
    return res


def main():
    data = load()
    if "--sweep" in sys.argv:
        out = []
        fe = make_fastembed("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        out.append(sweep("fastembed", fe, data, [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5]))
        ce = make_cross("BAAI/bge-reranker-v2-m3")
        out.append(sweep("bge-reranker", ce, data, [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.4]))
        print(json.dumps(out, ensure_ascii=False, indent=1))
        if "--json" in sys.argv:
            Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
        return
    rows = []
    scorers = [("fastembed:paraphrase-multilingual-MiniLM-L12-v2",
                lambda: make_fastembed("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")),
               ("CrossEncoder:BAAI/bge-reranker-v2-m3", lambda: make_cross("BAAI/bge-reranker-v2-m3"))]
    for name, mk in scorers:
        try:
            t0 = time.perf_counter(); fn = mk(); load_s = time.perf_counter() - t0
        except Exception as e:  # honest: report that it could not run
            rows.append({"scorer": name, "error": f"{type(e).__name__}: {str(e)[:200]}"}); continue
        for variant in ("topic", "topic+subtopic"):
            row, _ = evaluate(name, fn, data, variant)
            row["load_s"] = round(load_s, 1)
            rows.append(row)
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
