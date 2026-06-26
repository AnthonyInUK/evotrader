#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探针：列出 THS/东方财富 全量板块名称，并与当前 seed 脚本配置做模糊匹配。

用法（在本地机器运行，需要网络访问 THS/东方财富）：

    python scripts/probe_theme_sources.py

    # 只探 THS 行业板块
    python scripts/probe_theme_sources.py --source ths --kind industry

    # 探 THS 行业 + 概念，输出 JSON 建议文件
    python scripts/probe_theme_sources.py --out scripts/theme_name_corrections.json

    # 用东方财富替代 THS
    python scripts/probe_theme_sources.py --source em

目的：
    seed_market_index_cache.py 里的 DEFAULT_INDUSTRY_THEMES / DEFAULT_CONCEPT_THEMES
    名称必须与 THS 或 EM 返回的板块名称精确匹配。
    本脚本先拉全量列表，再对当前配置里的每个名称做模糊匹配，
    输出"建议用什么名字替换"，方便一次性校准。
"""
from __future__ import annotations

import argparse
import json
import sys
from difflib import get_close_matches, SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 与 seed 脚本保持同步的默认配置 ──────────────────────────────────────────
DEFAULT_INDUSTRY_THEMES = [
    "银行",
    "证券",
    "保险",
    "酿酒行业",
    "家电行业",
    "电池",
    "光伏设备",
    "半导体",
    "通信设备",
    "计算机设备",
    "软件开发",
    "医疗服务",
    "化学制药",
    "中药",
]

DEFAULT_CONCEPT_THEMES = [
    "白酒",
    "券商概念",
    "国企改革",
    "人工智能",
    "算力概念",
    "创新药",
    "固态电池",
]
# ─────────────────────────────────────────────────────────────────────────────


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _fuzzy_match(name: str, candidates: list[str], n: int = 3, cutoff: float = 0.4) -> list[str]:
    """返回与 name 最相似的 n 个候选名称（含分数）。"""
    close = get_close_matches(name, candidates, n=n, cutoff=cutoff)
    if not close:
        # cutoff 宽松一点再试
        close = sorted(candidates, key=lambda c: _similarity(name, c), reverse=True)[:n]
    return close


def _fetch_ths_industry() -> dict[str, str]:
    """返回 {name: code} 的 THS 行业板块映射。"""
    import akshare as ak  # type: ignore
    df = ak.stock_board_industry_name_ths()
    # 列名可能是 "name"/"code" 或 "板块名称"/"板块代码"
    name_col = next((c for c in df.columns if "name" in c.lower() or "名称" in c), df.columns[0])
    code_col = next((c for c in df.columns if "code" in c.lower() or "代码" in c), df.columns[1])
    return dict(zip(df[name_col].tolist(), df[code_col].tolist()))


def _fetch_ths_concept() -> dict[str, str]:
    """返回 {name: code} 的 THS 概念板块映射。"""
    import akshare as ak  # type: ignore
    df = ak.stock_board_concept_name_ths()
    name_col = next((c for c in df.columns if "name" in c.lower() or "名称" in c), df.columns[0])
    code_col = next((c for c in df.columns if "code" in c.lower() or "代码" in c), df.columns[1])
    return dict(zip(df[name_col].tolist(), df[code_col].tolist()))


def _fetch_em_industry() -> list[str]:
    """返回东方财富行业板块名称列表。"""
    import akshare as ak  # type: ignore
    df = ak.stock_board_industry_name_em()
    name_col = next((c for c in df.columns if "name" in c.lower() or "名称" in c or "板块" in c), df.columns[0])
    return df[name_col].tolist()


def _fetch_em_concept() -> list[str]:
    """返回东方财富概念板块名称列表。"""
    import akshare as ak  # type: ignore
    df = ak.stock_board_concept_name_em()
    name_col = next((c for c in df.columns if "name" in c.lower() or "名称" in c or "板块" in c), df.columns[0])
    return df[name_col].tolist()


def _probe_source(source: str, kind: str) -> dict[str, str] | list[str] | None:
    """拉取指定来源和类型的全量板块列表。返回 None 表示失败。"""
    try:
        if source == "ths" and kind == "industry":
            return _fetch_ths_industry()
        if source == "ths" and kind == "concept":
            return _fetch_ths_concept()
        if source == "em" and kind == "industry":
            return _fetch_em_industry()
        if source == "em" and kind == "concept":
            return _fetch_em_concept()
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {source}/{kind}: {exc}", file=sys.stderr)
        return None
    return None


def _build_report(
    configured: list[str],
    available: dict[str, str] | list[str],
    kind_label: str,
) -> list[dict]:
    """
    对比 configured 列表和 available 列表，输出每个配置名称的匹配情况。
    """
    if isinstance(available, dict):
        avail_names = list(available.keys())
    else:
        avail_names = list(available)

    report = []
    for name in configured:
        if name in avail_names:
            report.append({
                "configured": name,
                "status": "EXACT",
                "suggestion": name,
                "alternatives": [],
            })
        else:
            alts = _fuzzy_match(name, avail_names, n=5, cutoff=0.3)
            report.append({
                "configured": name,
                "status": "NOT_FOUND",
                "suggestion": alts[0] if alts else None,
                "alternatives": alts,
            })
    return report


def _print_report(report: list[dict], title: str) -> None:
    ok = [r for r in report if r["status"] == "EXACT"]
    bad = [r for r in report if r["status"] != "EXACT"]
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"  OK: {len(ok)}/{len(report)}  |  NOT_FOUND: {len(bad)}/{len(report)}")
    print(f"{'='*60}")
    if ok:
        print(f"\n✓ 精确匹配 ({len(ok)}):")
        for r in ok:
            print(f"    {r['configured']}")
    if bad:
        print(f"\n✗ 未匹配 ({len(bad)})  →  建议替换名称:")
        for r in bad:
            suggestion = r["suggestion"] or "(无建议)"
            alts = ", ".join(r["alternatives"][1:3]) if len(r["alternatives"]) > 1 else ""
            extra = f"  (备选: {alts})" if alts else ""
            print(f"    {r['configured']:20s}  →  {suggestion}{extra}")


def main() -> int:
    parser = argparse.ArgumentParser(description="探针：校准 THS/EM 板块名称")
    parser.add_argument(
        "--source",
        choices=["ths", "em", "both"],
        default="both",
        help="数据来源（默认 both：先试 THS，失败再试 EM）",
    )
    parser.add_argument(
        "--kind",
        choices=["industry", "concept", "both"],
        default="both",
        help="板块类型（默认 both）",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="将校正建议输出为 JSON 文件路径（可选）",
    )
    parser.add_argument(
        "--list-all",
        action="store_true",
        help="打印全量板块名称列表（不做匹配）",
    )
    args = parser.parse_args()

    sources = ["ths", "em"] if args.source == "both" else [args.source]
    kinds = ["industry", "concept"] if args.kind == "both" else [args.kind]

    all_corrections: dict[str, list[dict]] = {}

    for kind in kinds:
        configured = DEFAULT_INDUSTRY_THEMES if kind == "industry" else DEFAULT_CONCEPT_THEMES
        kind_label = "行业板块" if kind == "industry" else "概念板块"

        available = None
        used_source = None
        for source in sources:
            print(f"\n正在从 {source.upper()} 拉取{kind_label}列表...", end=" ", flush=True)
            result = _probe_source(source, kind)
            if result is not None:
                available = result
                used_source = source
                count = len(available) if isinstance(available, list) else len(available)
                print(f"✓ {count} 个板块")
                break
            else:
                print("✗ 失败")

        if available is None:
            print(f"  所有数据源均失败，跳过 {kind_label} 匹配")
            continue

        if args.list_all:
            names = list(available.keys()) if isinstance(available, dict) else available
            print(f"\n--- {used_source.upper()} 全量{kind_label}（{len(names)} 个）---")
            for i, name in enumerate(sorted(names), 1):
                print(f"  {i:3d}. {name}")
            continue

        report = _build_report(configured, available, kind_label)
        _print_report(report, f"{used_source.upper()} {kind_label} 匹配结果")
        all_corrections[f"{used_source}_{kind}"] = report

    # 输出 JSON 建议文件
    if args.out and all_corrections:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 整理成 seed 脚本直接可用的格式
        summary: dict[str, object] = {"raw": all_corrections, "suggested_config": {}}
        for key, report in all_corrections.items():
            source, kind = key.rsplit("_", 1)
            corrections = {
                r["configured"]: r["suggestion"]
                for r in report
                if r["status"] != "EXACT" and r["suggestion"]
            }
            confirmed = [r["configured"] for r in report if r["status"] == "EXACT"]
            suggested = [r["suggestion"] or r["configured"] for r in report]
            summary["suggested_config"][f"{kind}_themes"] = suggested  # type: ignore
            summary["suggested_config"][f"{kind}_corrections"] = corrections  # type: ignore

        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n校正建议已写入: {out_path}")
        print("将 suggested_config 里的列表复制到 seed_market_index_cache.py 的对应常量即可。")

    if not all_corrections and not args.list_all:
        print("\n没有可用数据，请检查网络环境（需访问 10jqka.com.cn / eastmoney.com）")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
