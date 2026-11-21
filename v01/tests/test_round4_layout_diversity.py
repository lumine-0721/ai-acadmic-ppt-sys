"""Round-4 tests: Stage F layout diversity, compact non-hero placeholders, layout_diversity gate, and QA counterexamples."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from pptv01.contracts import (
    AssetRegistry,
    CANVAS_H_IN,
    CANVAS_W_IN,
    DeckOpKind,
    DeckOps,
    DesignElement,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    ProductionPlan,
    SlideAssetMapping,
    SlidePlan,
    UserInputRequests,
    write_contract,
)
from pptv01.f_layout import (
    CANONICAL_LAYOUT_IDS,
    LEGACY_LAYOUT_ID,
    MAX_PLACEHOLDER_AREA_RATIO,
    run_stage_f,
    slide_content_area_in2,
)
from pptv01.f_layout.layouts import SlotItem, build_layout_elements
from pptv01.g_build import compile_deck_ops
from pptv01.g_pptx import run_stage_g
from pptv01.grading.gates import check_gates
from pptv01.h_qa.inspect import inspect_deck
from tests.conftest import GOOD_A

RID = "20261121-000000-r4ld"


def _png(w: int, h: int, color=(30, 85, 145)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _make_5_page_concise_deck(tmp_path: Path):
    """5-page concise text deck with 0 real images and 5 missing requests (models T36)."""
    pp = ProductionPlan(
        run_id=RID,
        source_utterance="做一份不超过5页的机器学习简介，尽量少用文字。",
        **{**GOOD_A, "constraints": ["不超过5页", "尽量少用文字"]},
    )
    pages = [
        {
            "page_id": "S001",
            "title": "机器学习是什么",
            "goal": "一句话定义",
            "key_points": ["从数据中自动归纳规律", "区别于手工编写固定规则"],
            "content_summary": "规划提示：用简图展示概念",
            "visual_intent": "输入处理输出概念图",
            "asset_needs": [],
            "status": "provisional",
        },
        {
            "page_id": "S002",
            "title": "模型是怎样训练出来的",
            "goal": "展示三步流程",
            "key_points": ["收集并清洗标注样本", "迭代优化损失函数参数", "在未知样本上验证泛化"],
            "content_summary": "规划提示：展示训练流程",
            "visual_intent": "三步流程图",
            "asset_needs": [],
            "status": "provisional",
        },
        {
            "page_id": "S003",
            "title": "典型应用场景与核心指标",
            "goal": "列举常见场景",
            "key_points": ["语音与图像识别覆盖日常交互", "检索与推荐系统提升信息匹配", "科研辅助与工业质检"],
            "content_summary": "规划提示：图标列表",
            "visual_intent": "场景图标",
            "asset_needs": [],
            "status": "provisional",
        },
        {
            "page_id": "S004",
            "title": "能力边界与局限对比",
            "goal": "区分能做与做不到",
            "key_points": ["擅长高维模式识别与预测", "依赖训练数据分布可能产生偏差", "高风险决策仍需人类把关"],
            "content_summary": "规划提示：对比能力与局限",
            "visual_intent": "左右对比图",
            "asset_needs": [],
            "status": "provisional",
        },
        {
            "page_id": "S005",
            "title": "我们该如何看待这项技术",
            "goal": "收尾观点",
            "key_points": ["将其作为增效工具而非全知权威", "持续关注数据隐私与公平性"],
            "content_summary": "规划提示：收尾总结",
            "visual_intent": "总结页",
            "asset_needs": [],
            "status": "provisional",
        },
    ]
    sp = SlidePlan(run_id=RID, status="provisional", narrative="五页极简科普。", pages=pages)
    (tmp_path / "assets").mkdir(exist_ok=True)
    ref_path = tmp_path / "assets" / "A001.json"
    ref_path.write_text('{"title": "Machine Learning Overview", "year": 2024, "doi": "10.1000/ml-demo"}\n', encoding="utf-8")
    reg = AssetRegistry(
        run_id=RID,
        assets=[
            {
                "asset_id": "A001",
                "status": "acquired",
                "type": "reference",
                "file_path": "assets/A001.json",
                "source_url": "https://doi.org/10.1000/ml-demo",
                "source_name": "Crossref",
                "license_or_terms": "metadata",
                "identifier": "10.1000/ml-demo",
                "sha256": hashlib.sha256(ref_path.read_bytes()).hexdigest(),
                "retrieved_at": "2026-11-21T00:00:00+08:00",
            }
        ],
    )
    uir = UserInputRequests(
        run_id=RID,
        requests=[
            {
                "request_id": f"U00{i}",
                "kind": "diagram",
                "page_ids": [f"S00{i}"],
                "need": f"提供第 {i} 页的可核验概念示意资料",
                "why": "D 未找到可标来源的现成图；不允许凭空绘制",
                "format_wanted": "JSON 或 PNG",
                "acceptance": ["来源明确"],
                "blocking": False,
                "fallback": "placeholder",
                "status": "open",
            }
            for i in range(1, 6)
        ],
        summary="5 open (0 blocking)",
    )
    mapping = SlideAssetMapping(
        run_id=RID,
        mappings=[{"page_id": "S001", "asset_id": "A001", "usage": "概念参考出处", "accepted": True}],
        unused_assets=[],
    )
    write_contract(pp, tmp_path / "production_plan.json")
    write_contract(sp, tmp_path / "slide_plan.json")
    write_contract(reg, tmp_path / "asset_registry.json")
    write_contract(uir, tmp_path / "user_input_requests.json")
    write_contract(mapping, tmp_path / "slide_asset_mapping.json")
    return pp, sp, reg, uir, mapping


def _make_7_page_mixed_deck(tmp_path: Path):
    """7-page mixed deck with landscape photos, multiple placeholders, and varied topics (models T12)."""
    pp = ProductionPlan(run_id=RID, source_utterance="准备一份7页的社区书店创业展示PPT。", **GOOD_A)
    pages = [
        {"page_id": "S001", "title": "为什么现在开一家社区书店", "goal": "背景与机会", "key_points": ["实体阅读空间提供线下社交场域", "社区文化消费需求持续回暖", "打造高黏性邻里文化节点"], "content_summary": "规划提示：用实景照片交代社区书店背景机会", "visual_intent": "书店实景图", "asset_needs": [], "status": "provisional"},
        {"page_id": "S002", "title": "目标客群是谁", "goal": "客群画像", "key_points": ["周边亲子家庭与周末阅读者", "远程办公与自习人群", "对安静氛围与精选书单有需求"], "content_summary": "规划提示：描述两类核心客群特征并标注待核实统计", "visual_intent": "画像图", "asset_needs": [], "status": "provisional"},
        {"page_id": "S003", "title": "空间定位与产品组合", "goal": "定位与差异化", "key_points": ["精选人文社科与绘本选品", "咖啡轻食与文创提升复购", "与大型连锁书店形成错位"], "content_summary": "规划提示：结合空间实景展示产品组合思路", "visual_intent": "空间实景", "asset_needs": [], "status": "provisional"},
        {"page_id": "S004", "title": "选址与日常运营流程", "goal": "运营步骤", "key_points": ["选址综合评估人流与租金", "建立周度选品与补货节奏", "策划周末读书会与讲座活动"], "content_summary": "规划提示：按阶段说明选址到日常运营如何跑起来", "visual_intent": "流程图", "asset_needs": [], "status": "provisional"},
        {"page_id": "S005", "title": "线上与线下渠道对比", "goal": "渠道对比", "key_points": ["线下到店重在体验与驻留时长", "社群私域承接复购与活动报名", "本地生活平台带来新客曝光"], "content_summary": "规划提示：对比线下到店体验与线上私域获客", "visual_intent": "渠道对比", "asset_needs": [], "status": "provisional"},
        {"page_id": "S006", "title": "成本结构与核心测算指标", "goal": "财务关键点", "key_points": ["房租与人力占固定支出核心", "客单价与日均进店量决定盈亏平衡", "具体财务数字待补充本地调研"], "content_summary": "规划提示：列出关键成本项但不编造未核实数字", "visual_intent": "数据图表", "asset_needs": [], "status": "provisional"},
        {"page_id": "S007", "title": "风险评估与行动展望", "goal": "总结风险", "key_points": ["严控初期装修投入与库存周转", "先做小规模快闪测试客群反馈"], "content_summary": "规划提示：收尾提示主要经营风险与试错建议", "visual_intent": "收尾页", "asset_needs": [], "status": "provisional"},
    ]
    sp = SlidePlan(run_id=RID, status="provisional", narrative="七页创业计划。", pages=pages)
    (tmp_path / "assets").mkdir(exist_ok=True)
    img1 = _png(1400, 900, (45, 75, 115))
    img2 = _png(1600, 900, (95, 65, 45))
    img3 = _png(1200, 800, (55, 105, 85))
    (tmp_path / "assets" / "A001.png").write_bytes(img1)
    (tmp_path / "assets" / "A002.png").write_bytes(img2)
    (tmp_path / "assets" / "A003.png").write_bytes(img3)
    reg = AssetRegistry(
        run_id=RID,
        assets=[
            {
                "asset_id": "A001", "status": "acquired", "type": "image", "file_path": "assets/A001.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:Bookstore1.png", "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0", "author": "PhotoA", "attribution": "PhotoA, CC BY 4.0, Wikimedia Commons",
                "sha256": hashlib.sha256(img1).hexdigest(), "mime": "image/png", "width": 1400, "height": 900,
                "retrieved_at": "2026-11-21T00:00:00+08:00",
            },
            {
                "asset_id": "A002", "status": "acquired", "type": "image", "file_path": "assets/A002.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:Bookstore2.png", "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0", "author": "PhotoB", "attribution": "PhotoB, CC BY 4.0, Wikimedia Commons",
                "sha256": hashlib.sha256(img2).hexdigest(), "mime": "image/png", "width": 1600, "height": 900,
                "retrieved_at": "2026-11-21T00:00:00+08:00",
            },
            {
                "asset_id": "A003", "status": "acquired", "type": "image", "file_path": "assets/A003.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:Bookstore3.png", "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0", "author": "PhotoC", "attribution": "PhotoC, CC BY 4.0, Wikimedia Commons",
                "sha256": hashlib.sha256(img3).hexdigest(), "mime": "image/png", "width": 1200, "height": 800,
                "retrieved_at": "2026-11-21T00:00:00+08:00",
            },
        ],
    )
    uir = UserInputRequests(
        run_id=RID,
        requests=[
            {"request_id": "U001", "kind": "statistic", "page_ids": ["S001"], "need": "补充本地实体书店客流统计", "why": "D 未找到", "format_wanted": "CSV", "acceptance": ["来源明确"], "blocking": False, "fallback": "placeholder", "status": "open"},
            {"request_id": "U002", "kind": "diagram", "page_ids": ["S002"], "need": "补充两类目标客群画像", "why": "D 未找到", "format_wanted": "JSON", "acceptance": ["来源明确"], "blocking": False, "fallback": "placeholder", "status": "open"},
            {"request_id": "U003", "kind": "statistic", "page_ids": ["S002"], "need": "补充目标社区人口与阅读频次", "why": "D 未找到", "format_wanted": "CSV", "acceptance": ["来源明确"], "blocking": False, "fallback": "placeholder", "status": "open"},
            {"request_id": "U004", "kind": "chart", "page_ids": ["S006"], "need": "补充本地租金与人力成本测算表", "why": "D 未找到", "format_wanted": "CSV", "acceptance": ["来源明确"], "blocking": False, "fallback": "placeholder", "status": "open"},
        ],
        summary="4 open (0 blocking)",
    )
    mapping = SlideAssetMapping(
        run_id=RID,
        mappings=[
            {"page_id": "S001", "asset_id": "A001", "usage": "封面场景图", "accepted": True},
            {"page_id": "S003", "asset_id": "A002", "usage": "空间定位主图", "accepted": True},
            {"page_id": "S005", "asset_id": "A003", "usage": "线下体验实景", "accepted": True},
        ],
        unused_assets=[],
    )
    write_contract(pp, tmp_path / "production_plan.json")
    write_contract(sp, tmp_path / "slide_plan.json")
    write_contract(reg, tmp_path / "asset_registry.json")
    write_contract(uir, tmp_path / "user_input_requests.json")
    write_contract(mapping, tmp_path / "slide_asset_mapping.json")
    return pp, sp, reg, uir, mapping


def test_round4_5_page_concise_deck_diverse_layouts_and_compact_placeholders(tmp_path):
    """5-page concise deck (T36-like): >= 5 distinct layouts, no adjacent repeat, compact <= 15% non-hero placeholders."""
    pp, sp, reg, uir, mapping = _make_5_page_concise_deck(tmp_path)
    design = run_stage_f(pp, sp, reg, uir, mapping, tmp_path)
    ops = compile_deck_ops(sp, design, reg, uir, tmp_path)
    deck_path = run_stage_g(sp, design, reg, uir, tmp_path)

    lids = [p.layout_id for p in design.pages]
    assert all(lid in CANONICAL_LAYOUT_IDS for lid in lids), lids
    assert all(lids[i] != lids[i - 1] for i in range(1, len(lids))), f"adjacent repeat: {lids}"
    # R7-2: 5 distinct eligible content layouts (two-content/three-card/hero-statement replace section/quote),
    # while S003/S004 still never force big-number or comparison without source/2v2 evidence.
    assert len(set(lids)) == 5, f"expected 5 distinct layouts: {lids}"
    assert "section" not in lids and "quote" not in lids, f"multi-bullet content should not use divider/quote: {lids}"
    assert "big-number" not in lids and "comparison" not in lids
    assert LEGACY_LAYOUT_ID not in lids

    # Compiled DeckOps must preserve layout_id on every add_slide op
    slide_ops = [o for o in ops.ops if o.kind is DeckOpKind.add_slide]
    assert [o.params.get("layout_id") for o in slide_ops] == lids

    # Every placeholder must stay <= 15% of content area and strictly smaller than primary content slot
    content_area = slide_content_area_in2()
    for p in design.pages:
        phs = [e for e in p.elements if e.kind is ElementKind.placeholder]
        primaries = [
            e.w_in * e.h_in
            for e in p.elements
            if e.kind not in (ElementKind.placeholder, ElementKind.shape, ElementKind.caption, ElementKind.footer)
        ]
        max_primary = max(primaries)
        for ph in phs:
            area = ph.w_in * ph.h_in
            assert area / content_area <= MAX_PLACEHOLDER_AREA_RATIO, f"{p.page_id}.{ph.element_id} too large: {area/content_area:.1%}"
            assert area < max_primary, f"{p.page_id}.{ph.element_id} hijacked largest slot"

    # Hard gates & Stage H QA must pass cleanly with zero oversized-placeholder or occlusion issues
    rep = check_gates(tmp_path)
    by_gate = {r.gate: r for r in rep.results}
    assert by_gate["layout_diversity"].status == "pass", by_gate["layout_diversity"].detail
    assert by_gate["placeholders_marked"].status == "pass", by_gate["placeholders_marked"].detail
    assert by_gate["no_fabricated_visuals"].status == "pass", by_gate["no_fabricated_visuals"].detail

    qa_issues = inspect_deck(deck_path, slide_plan=sp, design=design, reg=reg, uir=uir, mapping=mapping)
    assert not [i for i in qa_issues if "oversized" in i.description or i.kind.value == "occlusion"], qa_issues


def test_round4_7_page_mixed_deck_diversity_real_photos_in_hero_slots_and_gates_pass(tmp_path):
    """7-page mixed deck (T12-like): >= 5 distinct layouts, no adjacent repeat, real photos in hero slots, gates pass."""
    pp, sp, reg, uir, mapping = _make_7_page_mixed_deck(tmp_path)
    design = run_stage_f(pp, sp, reg, uir, mapping, tmp_path)
    ops = compile_deck_ops(sp, design, reg, uir, tmp_path)
    deck_path = run_stage_g(sp, design, reg, uir, tmp_path)

    lids = [p.layout_id for p in design.pages]
    assert len(lids) == 7
    assert all(lids[i] != lids[i - 1] for i in range(1, len(lids))), f"adjacent repeat: {lids}"
    assert len(set(lids)) >= 5, f"expected >= 5 distinct layouts across 7 pages, got: {lids}"
    assert LEGACY_LAYOUT_ID not in lids

    # Real photos must enter generous hero slots (>= 20 sq in) with anchored captions (not floating far away)
    for pid in ("S001", "S003", "S005"):
        page = next(p for p in design.pages if p.page_id == pid)
        img = next(e for e in page.elements if e.kind is ElementKind.image)
        assert img.w_in * img.h_in >= 20.0, f"{pid} image slot too small: {img.w_in}x{img.h_in}"
        cap = next(e for e in page.elements if e.element_id == "cap_1")
        if page.layout_id != "full-bleed":
            assert abs(cap.y_in - (img.y_in + img.h_in)) <= 0.15, f"{pid} caption detached from image"

    # Compiled ops carry layout_id
    slide_ops = [o for o in ops.ops if o.kind is DeckOpKind.add_slide]
    assert [o.params.get("layout_id") for o in slide_ops] == lids

    rep = check_gates(tmp_path)
    by_gate = {r.gate: r for r in rep.results}
    for gname in ("layout_diversity", "placeholders_marked", "no_fabricated_visuals", "attribution_present", "native_content_not_screenshots"):
        assert by_gate[gname].status == "pass", f"{gname} failed: {by_gate[gname].detail}"

    qa_issues = inspect_deck(deck_path, slide_plan=sp, design=design, reg=reg, uir=uir, mapping=mapping)
    assert not [i for i in qa_issues if "oversized" in i.description or i.kind.value == "occlusion"], qa_issues


def test_round4_counterexample_t36_giant_yellow_placeholder_caught_by_qa_and_gate(tmp_path):
    """Counterexample modeling T36 slides 1-3: repeated legacy layout + giant yellow placeholder caught by gate AND QA."""
    pp, sp, reg, uir, mapping = _make_5_page_concise_deck(tmp_path)

    # Deliberately construct the old monotonous layout (bg_header + small top-left bullets + giant 6x5.2 yellow box)
    bad_pages: list[PageDesignEntry] = []
    for idx, page in enumerate(sp.pages, 1):
        uid = f"U00{idx}"
        bad_pages.append(
            PageDesignEntry(
                page_id=page.page_id,
                layout_id=LEGACY_LAYOUT_ID,
                layout_name="split_text_left",
                canvas_width_in=CANVAS_W_IN,
                canvas_height_in=CANVAS_H_IN,
                elements=[
                    DesignElement(element_id="bg_header", kind=ElementKind.shape, x_in=0.0, y_in=0.0, w_in=CANVAS_W_IN, h_in=1.24, z_index=0, fill_hex="#EFF6FF", line_hex="#EFF6FF", role="header_band"),
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.65, y_in=0.22, w_in=12.03, h_in=0.88, z_index=2, text=page.title, font="Microsoft YaHei", font_size_pt=28.0, bold=True),
                    DesignElement(element_id="bullets", kind=ElementKind.bullet, x_in=0.85, y_in=1.50, w_in=5.40, h_in=1.20, z_index=2, text="\n".join(f"• {p}" for p in page.key_points), font="Microsoft YaHei", font_size_pt=18.0),
                    DesignElement(
                        element_id=f"PLACEHOLDER_{uid}",
                        kind=ElementKind.placeholder,
                        x_in=6.68,
                        y_in=1.50,
                        w_in=6.00,
                        h_in=5.20,  # 31.2 sq in = ~43% of content area & largest slot!
                        z_index=2,
                        text=f"待补充：提供第 {idx} 页的可核验概念示意资料（{uid}）",
                        font="Microsoft YaHei",
                        font_size_pt=14.0,
                        fill_hex="#FEF3C7",
                        line_hex="#D97706",
                        request_id=uid,
                    ),
                ],
            )
        )

    bad_design = PageDesign(run_id=RID, pages=bad_pages)
    write_contract(bad_design, tmp_path / "page_design.json")
    deck_path = run_stage_g(sp, bad_design, reg, uir, tmp_path)

    # 1) layout_diversity hard gate MUST fail and report oversized placeholder + adjacent repeat + legacy overuse
    rep = check_gates(tmp_path)
    ld = {r.gate: r for r in rep.results}["layout_diversity"]
    assert ld.status == "fail"
    assert "placeholder" in ld.detail.lower() or "15%" in ld.detail

    # 2) Stage H QA inspect_deck MUST catch all giant yellow placeholders directly on deck.pptx
    issues = inspect_deck(deck_path, slide_plan=sp, design=bad_design, reg=reg, uir=uir, mapping=mapping)
    oversized = [i for i in issues if "placeholder" in i.description.lower() and "oversized" in i.description.lower()]
    assert len(oversized) == 5, f"expected 5 oversized placeholder QA alerts, got: {issues}"
    assert any("largest content slot" in i.description for i in oversized)


def test_round4_layout_diversity_gate_rules_adjacent_count_and_legacy_cap(tmp_path):
    """Unit-test each layout_diversity gate rule independently (adjacent repeat, distinct count, legacy <= 1)."""
    def _simple_page(pid: str, lid: str) -> PageDesignEntry:
        return PageDesignEntry(
            page_id=pid,
            layout_id=lid,
            layout_name=lid,
            elements=[
                DesignElement(element_id="title", kind=ElementKind.title, x_in=0.65, y_in=0.5, w_in=12.0, h_in=0.9, z_index=2, text=f"Title {pid}", font_size_pt=28.0),
                DesignElement(element_id="bullets", kind=ElementKind.bullet, x_in=0.65, y_in=1.8, w_in=11.0, h_in=2.5, z_index=2, text="• Point A", font_size_pt=18.0),
            ],
        )

    # (a) Adjacent duplicate must fail even with 5 distinct layouts across 6 slides
    d1 = tmp_path / "adj"
    d1.mkdir()
    write_contract(
        PageDesign(
            run_id=RID,
            pages=[
                _simple_page("S001", "cover"),
                _simple_page("S002", "section"),
                _simple_page("S003", "section"),  # adjacent repeat!
                _simple_page("S004", "timeline"),
                _simple_page("S005", "comparison"),
                _simple_page("S006", "quote"),
            ],
        ),
        d1 / "page_design.json",
    )
    g1 = {r.gate: r for r in check_gates(d1).results}["layout_diversity"]
    assert g1.status == "fail" and "adjacent" in g1.detail

    # (b) 7 pages with only 3 distinct layouts must fail (need >= 5)
    d2 = tmp_path / "few"
    d2.mkdir()
    write_contract(
        PageDesign(
            run_id=RID,
            pages=[
                _simple_page("S001", "cover"),
                _simple_page("S002", "section"),
                _simple_page("S003", "quote"),
                _simple_page("S004", "section"),
                _simple_page("S005", "quote"),
                _simple_page("S006", "section"),
                _simple_page("S007", "quote"),
            ],
        ),
        d2 / "page_design.json",
    )
    g2 = {r.gate: r for r in check_gates(d2).results}["layout_diversity"]
    assert g2.status == "fail" and "insufficient layout diversity" in g2.detail

    # (c) Legacy layout appearing twice (even non-adjacent) must fail; appearing once passes
    d3 = tmp_path / "legacy2"
    d3.mkdir()
    write_contract(
        PageDesign(
            run_id=RID,
            pages=[
                _simple_page("S001", "cover"),
                _simple_page("S002", LEGACY_LAYOUT_ID),
                _simple_page("S003", "timeline"),
                _simple_page("S004", "comparison"),
                _simple_page("S005", LEGACY_LAYOUT_ID),  # 2nd legacy page -> fail!
                _simple_page("S006", "quote"),
            ],
        ),
        d3 / "page_design.json",
    )
    g3 = {r.gate: r for r in check_gates(d3).results}["layout_diversity"]
    assert g3.status == "fail" and "legacy layout" in g3.detail

    d4 = tmp_path / "legacy1"
    d4.mkdir()
    write_contract(
        PageDesign(
            run_id=RID,
            pages=[
                _simple_page("S001", "cover"),
                _simple_page("S002", LEGACY_LAYOUT_ID),  # 1 legacy page -> allowed
                _simple_page("S003", "timeline"),
                _simple_page("S004", "comparison"),
                _simple_page("S005", "big-number"),
                _simple_page("S006", "quote"),
            ],
        ),
        d4 / "page_design.json",
    )
    g4 = {r.gate: r for r in check_gates(d4).results}["layout_diversity"]
    assert g4.status == "pass", g4.detail


def test_round4_all_canonical_layouts_build_cleanly_including_needs_input_list():
    """Smoke-verify every canonical layout builder produces valid geometry with compact placeholders."""
    from pptv01.contracts import SlidePage
    from pptv01.f_layout.validate import validate_page_geometry

    page = SlidePage(
        page_id="S001",
        title="待补充材料清单",
        goal="列出缺失素材",
        key_points=["要点一：核心定义", "要点二：关键流程", "要点三：典型对比"],
        content_summary="仅备注",
        visual_intent="列表",
        asset_needs=[],
        status="provisional",
    )
    ph_items = [
        SlotItem("placeholder", None, "U001", None, "待补充：统计数据（U001）"),
        SlotItem("placeholder", None, "U002", None, "待补充：机制流程（U002）"),
    ]
    img_item = SlotItem("image", "A001", None, "assets/A001.png", "主图", width_px=1200, height_px=800, attribution="Author, CC BY 4.0")

    for lid in CANONICAL_LAYOUT_IDS:
        notes: list[str] = []
        vis = [img_item] if lid in ("image-left", "image-right", "full-bleed") else []
        els = build_layout_elements(lid, page, page.key_points, vis, ph_items, ["[A009] Ref (2024)"], notes)
        entry = PageDesignEntry(page_id="S001", layout_id=lid, layout_name=lid, elements=els)
        probs = validate_page_geometry(entry)
        assert not probs, f"layout {lid} geometry failed: {probs}"


def test_r5_3_multi_visual_five_slide_deck_places_all_ten_visuals_and_passes_gate(tmp_path):
    """Round-5 R5-3 #3: 5-slide deck where EVERY slide has 2 real images must place ALL 10 images (never drop 2nd on cover/section/etc.)."""
    from pptv01.f_layout.validate import validate_page_geometry

    pp = ProductionPlan(
        run_id=RID,
        source_utterance="制作一份5页图文并茂的AI专题演示。",
        **GOOD_A,
    )
    pages = [
        {
            "page_id": f"S00{i}",
            "title": title,
            "goal": f"目标 {i}",
            "key_points": [f"第{i}页要点一：核心特征", f"第{i}页要点二：实证结论"],
            "content_summary": f"第{i}页双图演示",
            "visual_intent": "双图比较",
            "asset_needs": [],
            "status": "provisional",
        }
        for i, title in enumerate(
            ["专题概览", "第一部分：方法演进", "实验对比分析", "关键指标趋势", "核心结论与启示"],
            1,
        )
    ]
    sp = SlidePlan(run_id=RID, status="provisional", narrative="五页每页两张真实图片。", pages=pages)

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(exist_ok=True)
    asset_rows = []
    mapping_rows = []
    for k in range(1, 11):
        aid = f"A{k:03d}"
        p = assets_dir / f"{aid}.png"
        p.write_bytes(_png(1200, 800, color=(20 + k * 15, 70 + k * 10, 140)))
        asset_rows.append(
            {
                "asset_id": aid,
                "status": "acquired",
                "type": "image",
                "file_path": f"assets/{aid}.png",
                "width": 1200,
                "height": 800,
                "mime": "image/png",
                "source_url": f"https://example.org/{aid}.png",
                "source_name": "Wikimedia",
                "license_or_terms": "CC BY 4.0",
                "author": f"Author {aid}",
                "attribution": f"Author {aid}, CC BY 4.0",
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "retrieved_at": "2026-11-21T00:00:00+08:00",
            }
        )
        page_num = (k - 1) // 2 + 1
        mapping_rows.append(
            {
                "page_id": f"S00{page_num}",
                "asset_id": aid,
                "usage": f"第{page_num}页配图{aid}",
                "accepted": True,
            }
        )

    reg = AssetRegistry(run_id=RID, assets=asset_rows)
    uir = UserInputRequests(run_id=RID, requests=[], summary="0 open")
    mapping = SlideAssetMapping(run_id=RID, mappings=mapping_rows, unused_assets=[])
    write_contract(pp, tmp_path / "production_plan.json")
    write_contract(sp, tmp_path / "slide_plan.json")
    write_contract(reg, tmp_path / "asset_registry.json")
    write_contract(uir, tmp_path / "user_input_requests.json")
    write_contract(mapping, tmp_path / "slide_asset_mapping.json")

    design = run_stage_f(pp, sp, reg, uir, mapping, tmp_path)
    write_contract(design, tmp_path / "page_design.json")
    assert len(design.pages) == 5

    placed_asset_ids: set[str] = set()
    for p in design.pages:
        assert not validate_page_geometry(p), f"{p.page_id} failed geometry"
        page_imgs = [e for e in p.elements if e.kind is ElementKind.image]
        assert len(page_imgs) == 2, f"{p.page_id} ({p.layout_id}) dropped visual(s): {page_imgs}"
        placed_asset_ids.update(e.asset_id for e in page_imgs if e.asset_id)

    assert placed_asset_ids == {f"A{k:03d}" for k in range(1, 11)}

    gates = {r.gate: r for r in check_gates(tmp_path).results}
    assert gates["layout_diversity"].status == "pass", gates["layout_diversity"].detail
    assert gates["mapped_visuals_placed"].status == "pass", gates["mapped_visuals_placed"].detail

    # Counterexample: drop 2nd image from cover (S001) -> mapped_visuals_placed gate MUST FAIL!
    bad_pages = []
    for p in design.pages:
        if p.page_id == "S001":
            kept = [e for e in p.elements if e.asset_id != "A002"]
            bad_pages.append(p.model_copy(update={"elements": kept}))
        else:
            bad_pages.append(p)
    write_contract(design.model_copy(update={"pages": bad_pages}), tmp_path / "page_design.json")
    gates_bad = {r.gate: r for r in check_gates(tmp_path).results}
    assert gates_bad["mapped_visuals_placed"].status == "fail"
    assert "A002" in gates_bad["mapped_visuals_placed"].detail


def test_r5_3_placeholder_missing_request_id_raises_not_u001():
    """Round-5 R5-3 #4: placeholder without request_id must raise ValueError instead of fabricating U001 / PLACEHOLDER_None."""
    from pptv01.contracts import SlidePage

    page = SlidePage(
        page_id="S001",
        title="缺失请求号测试",
        goal="测试边界",
        key_points=["要点一"],
        content_summary="备注",
        visual_intent="图示",
        asset_needs=[],
        status="provisional",
    )
    bad_ph = SlotItem("placeholder", None, None, None, "待补充：数据缺失")
    with pytest.raises(ValueError, match="missing request_id"):
        build_layout_elements("section", page, page.key_points, [], [bad_ph], [], [])
    with pytest.raises(ValueError, match="missing request_id"):
        build_layout_elements("needs-input-list", page, page.key_points, [], [bad_ph], [], [])


def test_r5_3_deck_ops_mismatch_and_corrupt_fail_layout_diversity_gate(tmp_path):
    """Round-5 R5-3 #5: corrupt or slide-count-mismatched deck_ops.json must FAIL layout_diversity (never silently pass)."""
    def _p(pid: str, lid: str) -> PageDesignEntry:
        return PageDesignEntry(
            page_id=pid,
            layout_id=lid,
            layout_name=lid,
            elements=[
                DesignElement(element_id="title", kind=ElementKind.title, x_in=0.65, y_in=0.5, w_in=12.0, h_in=0.9, z_index=2, text=f"Title {pid}", font_size_pt=28.0),
                DesignElement(element_id="bullets", kind=ElementKind.bullet, x_in=0.65, y_in=1.8, w_in=11.0, h_in=2.5, z_index=2, text="• Point", font_size_pt=18.0),
            ],
        )

    design = PageDesign(
        run_id=RID,
        pages=[_p("S001", "cover"), _p("S002", "section"), _p("S003", "quote"), _p("S004", "timeline")],
    )

    # 1) Mismatched slide count in deck_ops.json
    d_mismatch = tmp_path / "mismatch"
    d_mismatch.mkdir()
    write_contract(design, d_mismatch / "page_design.json")
    write_contract(
        DeckOps(
            run_id=RID,
            page_ids=["S001", "S002"],
            ops=[
                {"op_id": "OP0001", "page_id": "S001", "kind": DeckOpKind.add_slide, "params": {"layout_id": "cover"}},
                {"op_id": "OP0002", "page_id": "S002", "kind": DeckOpKind.add_slide, "params": {"layout_id": "section"}},
            ],
        ),
        d_mismatch / "deck_ops.json",
    )
    g_mis = {r.gate: r for r in check_gates(d_mismatch).results}["layout_diversity"]
    assert g_mis.status == "fail" and "add_slide count" in g_mis.detail

    # 2) Corrupt deck_ops.json
    d_corrupt = tmp_path / "corrupt"
    d_corrupt.mkdir()
    write_contract(design, d_corrupt / "page_design.json")
    (d_corrupt / "deck_ops.json").write_text("{not valid json", encoding="utf-8")
    g_cor = {r.gate: r for r in check_gates(d_corrupt).results}["layout_diversity"]
    assert g_cor.status == "fail" and "deck_ops.json invalid/unreadable" in g_cor.detail


def test_r5_3_single_placeholder_compact_corner_badge_styling_and_alignment():
    """Round-5 R5-3 #6: single placeholder is a compact corner badge (<= 6% content area), dark-themed on cover, and alignment-drift-free."""
    from pptv01.contracts import SlidePage
    from pptv01.f_layout.validate import validate_page_geometry
    from pptv01.h_qa.geometry import GeoBox, find_edge_misalignments

    page = SlidePage(
        page_id="S001",
        title="人工智能演进核心特征",
        goal="展示要点",
        key_points=["从规则系统迈向数据驱动表示学习", "生成式模型重塑人机协作边界", "评测重点转向可靠性与可追溯性"],
        content_summary="备注",
        visual_intent="概念",
        asset_needs=[],
        status="provisional",
    )
    ph = [SlotItem("placeholder", None, "U001", None, "待补充：核心演进机制示意图（U001）")]
    content_area = slide_content_area_in2(CANVAS_W_IN, CANVAS_H_IN)

    for lid in ("cover", "section", "quote", "timeline", "comparison", "big-number"):
        notes: list[str] = []
        els = build_layout_elements(lid, page, page.key_points, [], ph, [], notes)
        entry = PageDesignEntry(page_id="S001", layout_id=lid, layout_name=lid, elements=els)
        assert not validate_page_geometry(entry), f"{lid} failed geometry"

        ph_els = [e for e in els if e.kind is ElementKind.placeholder]
        assert len(ph_els) == 1
        box = ph_els[0]
        assert (box.w_in * box.h_in) / content_area <= 0.06
        if lid == "cover":
            assert box.fill_hex == "#172A45"

        geo = [
            GeoBox(
                element_id=e.element_id,
                x_pt=e.x_in * 72.0,
                y_pt=e.y_in * 72.0,
                w_pt=e.w_in * 72.0,
                h_pt=e.h_in * 72.0,
                is_background=(e.z_index == 0 and e.kind is ElementKind.shape),
                is_picture=(e.kind is ElementKind.image),
                has_text=bool(e.text),
                text_len=len(e.text or ""),
                role=e.role,
            )
            for e in els
        ]
        ph_drifts = [m for m in find_edge_misalignments(geo) if "PLACEHOLDER_" in m[0] or "PLACEHOLDER_" in m[1]]
        assert not ph_drifts, f"{lid} placeholder has edge misalignment: {ph_drifts}"
