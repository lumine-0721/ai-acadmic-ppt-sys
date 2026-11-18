#!/usr/bin/env python3
"""Rebuild the committed APC fixtures (fixtures/apc/v001, fixtures/apc/v002).

Usage (from the repo root):  python tools/build_apc_fixtures.py

The handoff is a tiny synthetic "instrument gain calibration" project with two slides.
It stands in for the smoke files that used to live in C:/Users/86139/Desktop/codex/outputs/.
All numbers and sources are fictional test data, not research claims.

Shape (v002, the file most tests use):
  M001 user calibration table (material)
  E001 user evidence (VERIFIED, supports C002)           depends_on M001
  R001 retrieval DONE (no further literature needed)
  C001 context claim (problem statement)
  C002 finding claim (gain stays linear 1-100x)          depends_on E001, R001
  V001 empirical visual (gain curve)                     depends_on C002, E001
  V002 layout visual (title layout)
  S001 problem/goal slide, S002 result slide
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "fixtures" / "apc"
PROJECT = "instrument-gain-smoke"

REQUIREMENTS = """# PPT 需求书（合成测试夹具）

Project-ID: {project}
Revision: {revision}

本文件是 APC 校验器的最小夹具，内容为虚构，仅用于测试。

## 00. 任务边界
一份 2 页的组会汇报：仪器增益标定的问题与结论。

## 01. 受众与场合
课题组内部组会，听众熟悉仪器但不熟悉本次标定方法。

## 02. 时长与页数
5 分钟，2 页。

## 03. 科学问题
增益在 1-100 倍范围内是否保持线性。

## 04. 已有材料
M001：用户提供的增益标定表（虚构）。

## 05. 证据状态
E001 为用户自有数据，已核对到表格行；无需文献检索（R001 已收尾）。

## 06. 页面清单
S001 问题与目标；S002 线性度结论。

## 07. 视觉要求
V001 增益曲线（实测）；V002 标题版式。

## 08. 风险与约束
结论仅限 1-100 倍范围，不外推。

## 09. 验收
check_handoff.py validate 通过；每页状态与证据一致。
"""


def handoff_v002() -> dict:
    return {
        "project_id": PROJECT,
        "revision": 2,
        "slide_order": ["S001", "S002"],
        "records": {
            "M001": {
                "summary": "用户提供的增益标定表：设定增益 1-100 倍，各点实测输出",
                "depends_on": [],
                "source": "user-data/instrument-gain-calibration.csv",
            },
            "E001": {
                "summary": "增益设定值与实测输出的线性拟合结果（R2 = 0.999，虚构数值）",
                "depends_on": ["M001"],
                "status": "VERIFIED",
                "origin": "user",
                "source": "user-data/instrument-gain-calibration.csv",
                "locator": "Table 1, rows 1-12",
                "supports": ["C002"],
                "opposes": [],
            },
            "R001": {
                "summary": "确认本页结论不依赖外部文献，无需检索",
                "depends_on": ["M001"],
                "status": "DONE",
                "target_claims": ["C002"],
                "result_ids": [],
                "resolution": "结论只依据用户自有标定数据；无外部文献需求。",
            },
            "C001": {
                "summary": "背景：仪器增益需要在使用前标定，否则无法比较不同日期的信号",
                "depends_on": [],
                "status": "INFERRED",
                "kind": "context",
                "origin": "not_applicable",
            },
            "C002": {
                "summary": "在 1-100 倍设定范围内，实测输出与设定增益呈线性关系",
                "depends_on": ["E001", "R001"],
                "status": "VERIFIED",
                "kind": "finding",
                "origin": "user",
            },
            "V001": {
                "summary": "增益设定值对实测输出的散点与拟合线",
                "depends_on": ["C002", "E001"],
                "status": "READY",
                "kind": "empirical",
            },
            "V002": {
                "summary": "标题加正文的单栏版式",
                "depends_on": [],
                "status": "READY",
                "kind": "layout",
            },
            "S001": {
                "summary": "仪器增益标定：问题与目标",
                "depends_on": ["C001", "V002"],
                "status": "READY",
                "reviewed_in": 2,
            },
            "S002": {
                "summary": "增益线性度在 1-100 倍范围内保持稳定",
                "depends_on": ["C002", "V001", "R001"],
                "status": "READY",
                "reviewed_in": 2,
            },
        },
    }


def handoff_v001() -> dict:
    """Earlier revision: result slide still PROVISIONAL, retrieval still open, claim only inferred."""
    data = handoff_v002()
    data["revision"] = 1
    r = data["records"]
    r["E001"]["status"] = "USER_PROVIDED"
    r["R001"] = {
        "summary": "确认本页结论不依赖外部文献，无需检索",
        "depends_on": ["M001"],
        "status": "OPEN",
        "target_claims": ["C002"],
    }
    r["C002"]["status"] = "INFERRED"
    r["C002"]["kind"] = "inference"
    r["C002"]["disclosure"] = "初稿：尚未核对到原始标定表，线性结论待确认。"
    r["V001"]["status"] = "PROVISIONAL"
    r["S001"]["reviewed_in"] = 1
    r["S002"]["status"] = "PROVISIONAL"
    del r["S002"]["reviewed_in"]
    return data


def missing_support() -> dict:
    """Schema-valid but semantically wrong: the VERIFIED finding C002 loses its supporting link."""
    data = handoff_v002()
    data["records"]["E001"]["supports"] = []
    return data


def dump(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    for rev, builder in ((1, handoff_v001), (2, handoff_v002)):
        folder = OUT / f"v{rev:03d}"
        dump(folder / "PPT_HANDOFF.json", builder())
        (folder / "PPT_REQUIREMENTS.md").write_text(
            REQUIREMENTS.format(project=PROJECT, revision=rev), encoding="utf-8", newline="\n"
        )
    dump(OUT / "v002" / "missing-support.json", missing_support())
    print("wrote APC fixtures under", OUT)


if __name__ == "__main__":
    main()
