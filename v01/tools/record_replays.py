"""One-off recorder: saves REAL API responses used as replay fixtures by tests/test_stage_d.py.
Run from v01/:  python tools/record_replays.py   (needs network; replays are committed, tests never call the network)."""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pptv01.retrieval.backends import make_http  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "tests" / "replay"
http = make_http(30, "https://github.com/lumine-0721/ai-acadmic-ppt-sys")
INFO = {"prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "format": "json", "formatversion": 2, "iiurlwidth": 1280}


def get(url, **p):
    r = http.get(url, params=p)
    r.raise_for_status()
    return r.json()


def save(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("saved", name)


save("wikidata_search_parthenon_zh.json", get("https://www.wikidata.org/w/api.php", action="wbsearchentities", search="帕特农神庙", language="zh", uselang="zh", type="item", limit=5, format="json"))
ent = get("https://www.wikidata.org/w/api.php", action="wbgetentities", ids="Q10288", props="claims|labels|descriptions", languages="zh|en", format="json")
e = ent["entities"]["Q10288"]
e["claims"] = {"P18": e["claims"].get("P18", [])}          # keep only what the client reads
save("wikidata_entity_Q10288.json", ent)
save("commons_files_parthenon_p18.json", get("https://commons.wikimedia.org/w/api.php", action="query", titles="File:The Parthenon in Athens.jpg", **INFO))
save("commons_search_parthenon.json", get("https://commons.wikimedia.org/w/api.php", action="query", generator="search", gsrsearch="Parthenon", gsrnamespace=6, gsrlimit=8, **INFO))
from pyalex import Works  # noqa: E402
import pyalex  # noqa: E402
pyalex.config.email = "https://github.com/lumine-0721/ai-acadmic-ppt-sys"
from pptv01.d_retrieve.sources import SELECT  # noqa: E402
save("openalex_uhi.json", list(Works().search("urban heat island intensity").select(SELECT).get(per_page=4)))
save("crossref_work_uhi.json", get("https://api.crossref.org/works/10.1038/s41467-020-16461-9"))

# --- entity-class (P31) replays: real Wikidata answers for "佛光寺东大殿" (papers about the hall must not be picked)
srch = get("https://www.wikidata.org/w/api.php", action="wbsearchentities", search="佛光寺东大殿", language="zh", uselang="zh", type="item", limit=5, format="json")
save("wikidata_search_foguang_zh.json", srch)
ids = [x["id"] for x in srch["search"]]
cl = get("https://www.wikidata.org/w/api.php", action="wbgetentities", ids="|".join(ids), props="claims", format="json")
for e in cl["entities"].values():
    e["claims"] = {"P31": e.get("claims", {}).get("P31", [])}          # keep only what the client reads
save("wikidata_p31_foguang.json", cl)
