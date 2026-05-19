from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import MarkdownSection


SOURCE_ITEM_RE = re.compile(
    r"(?m)^\s{0,3}(?:#{1,6}\s+)?(?:"
    r"Definition|Theorem|Corollary|Proposition|Lemma|Example|Exercise|Remark|"
    r"Algorithm|Assumption|Claim|Conjecture|Problem|Question|Notation|"
    r"定义|定理|命题|引理|推论|例|练习|注记|算法|假设|断言|猜想|问题|记号"
    r")\b",
    re.I,
)
PROOF_MARKER_RE = re.compile(r"(?im)^\s{0,3}(?:Proof(?:\s+of\b.*?)?[.;:]?|证明)\b")
TRUNCATION_RE = re.compile(r"\[(?:mock )?excerpt truncated\]|\.\.\.\s*$", re.I)


def build_quality_report(
    *,
    source_file: Path,
    sections: list[MarkdownSection],
    section_items: list[list[dict[str, Any]]],
    all_items: list[dict[str, Any]],
    split_warnings: list[str],
) -> dict[str, Any]:
    labels: dict[str, int] = {}
    duplicate_labels: list[str] = []
    for item in all_items:
        label = str(item.get("label") or "")
        labels[label] = labels.get(label, 0) + 1
    duplicate_labels = sorted(label for label, count in labels.items() if count > 1)

    section_reports = []
    for section, items in zip(sections, section_items):
        item_marker_count = len(SOURCE_ITEM_RE.findall(section.text))
        proof_marker_count = len(PROOF_MARKER_RE.findall(section.text))
        env_counts: dict[str, int] = {}
        warnings: list[str] = []
        for item in items:
            env = str(item.get("env") or "")
            env_counts[env] = env_counts.get(env, 0) + 1
            content = str(item.get("content") or "")
            proof = item.get("proof")
            if TRUNCATION_RE.search(content):
                warnings.append(f"{item.get('label')}: content appears truncated")
            if proof is None and PROOF_MARKER_RE.search(content) and not _is_standalone_proof_section(section):
                warnings.append(f"{item.get('label')}: proof marker remains in content while proof is null")

        if item_marker_count and not items:
            warnings.append("source has theorem-like markers but extractor returned no items")
        if item_marker_count >= 3 and len(items) <= 1:
            warnings.append(
                f"source has {item_marker_count} theorem-like markers but only {len(items)} extracted item(s)"
            )
        if items and set(env_counts) == {"remark"} and item_marker_count >= 2:
            warnings.append("all extracted items are remark despite multiple theorem-like source markers")
        if proof_marker_count and not any(item.get("proof") for item in items) and not _is_standalone_proof_section(section):
            warnings.append("source has explicit proof markers but no item has a proof field")

        section_reports.append(
            {
                "section_index": section.index,
                "section": section.context.section,
                "section_number": section.context.section_number,
                "line_range": [section.start_line, section.end_line],
                "source_chars": len(section.text),
                "source_item_marker_count": item_marker_count,
                "source_proof_marker_count": proof_marker_count,
                "items": len(items),
                "env_counts": env_counts,
                "warnings": _dedupe(warnings),
            }
        )

    global_warnings = list(split_warnings)
    if duplicate_labels:
        global_warnings.append(f"duplicate labels: {', '.join(duplicate_labels[:20])}")
    if all_items and all(str(item.get("env") or "") == "remark" for item in all_items):
        global_warnings.append("all extracted items are remark")

    return {
        "source_file": str(source_file),
        "items_total": len(all_items),
        "sections_total": len(sections),
        "global_warnings": _dedupe(global_warnings),
        "duplicate_labels": duplicate_labels,
        "sections": section_reports,
    }


def write_quality_report(out_dir: Path, report: dict[str, Any]) -> None:
    (out_dir / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Quality Report",
        "",
        f"source_file: `{report.get('source_file')}`",
        f"sections_total: {report.get('sections_total')}",
        f"items_total: {report.get('items_total')}",
        "",
        "## Global Warnings",
        "",
    ]
    warnings = report.get("global_warnings") or []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")
    lines.extend(["", "## Section Warnings", ""])
    for section in report.get("sections") or []:
        section_warnings = section.get("warnings") or []
        if not section_warnings:
            continue
        lines.append(f"### section{int(section['section_index']):02d} {section.get('section')}")
        for warning in section_warnings:
            lines.append(f"- {warning}")
        lines.append("")
    if lines[-1] != "":
        lines.append("")
    (out_dir / "quality_report.md").write_text("\n".join(lines), encoding="utf-8")


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _is_standalone_proof_section(section: MarkdownSection) -> bool:
    heading = f"{section.source_heading} {section.context.section}".lower()
    return "proof of" in heading or "证明" in heading
