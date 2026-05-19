from __future__ import annotations

import re
from typing import Any

from .models import ENV_DISPLAY, MarkdownSection


ITEM_START_RE = re.compile(
    r"^(?:#{1,6}\s+)?(?:(?P<kind>"
    r"Definition|Theorem|Corollary|Proposition|Lemma|Example|Exercise|Remark|"
    r"Algorithm|Assumption|Claim|Conjecture|Problem|Question|Notation"
    r")\s*(?P<number>[A-Z]?\d+(?:\.\d+)*)?\.?|"
    r"(?P<cn_kind>定义|定理|命题|引理|推论|例|练习|注记|算法|假设|断言|猜想|问题|记号)"
    r"\s*(?P<cn_number>\d+(?:\.\d+)*)?|"
    r"(?P<number_first>\d+(?:\.\d+)+)\.?\s+(?P<title>"
    r"Definition|Theorem|Corollary|Proposition|Lemma|Example|Exercise|Remark"
    r"))\b",
    re.I,
)
PROOF_RE = re.compile(r"^(?:Proof(?:\s+of(?:\s+Theorem)?\s+(?P<number>[A-Z]?\d+(?:\.\d+)*))?[.;:]?|证明)\s*(?P<rest>.*)$", re.I)
CONSTRUCTION_RE = re.compile(r"^Construction\.\s*(?P<rest>.*)$", re.I)

TITLE_ENV_RULES = [
    ("definition", "def"),
    ("定义", "def"),
    ("theorem", "thm"),
    ("定理", "thm"),
    ("corollary", "cor"),
    ("推论", "cor"),
    ("proposition", "prop"),
    ("命题", "prop"),
    ("lemma", "lemma"),
    ("引理", "lemma"),
    ("example", "example"),
    ("例", "example"),
    ("exercise", "exercise"),
    ("练习", "exercise"),
    ("algorithm", "algorithm"),
    ("算法", "algorithm"),
    ("assumption", "assumption"),
    ("claim", "claim"),
    ("conjecture", "conjecture"),
    ("problem", "problem"),
    ("question", "question"),
    ("notation", "notation"),
    ("remark", "remark"),
    ("注记", "remark"),
]


class LocalSectionExtractor:
    """Deterministic smoke-test fallback.

    This is deliberately conservative and is not intended to replace the LLM
    extractor for production-quality semantic boundaries.
    """

    def extract_section(self, section: MarkdownSection) -> list[dict[str, Any]]:
        lines = section.text.splitlines()
        starts: list[tuple[int, str, str]] = []
        delayed_proofs: list[tuple[int, str | None]] = []
        for idx, line in enumerate(lines):
            stripped = line.strip()
            item = ITEM_START_RE.match(stripped)
            if item:
                number = item.group("number") or item.group("cn_number") or item.group("number_first") or ""
                title = item.group("kind") or item.group("cn_kind") or item.group("title") or "Remark"
                starts.append((idx, number, title))
                continue
            proof = PROOF_RE.match(stripped)
            if proof:
                delayed_proofs.append((idx, proof.group("number")))

        if not starts and _looks_like_standalone_proof_section(section):
            return [_standalone_proof_remark(section)]

        items: list[dict[str, Any]] = []
        by_number: dict[str, dict[str, Any]] = {}
        for local_index, (start, number, title) in enumerate(starts, start=1):
            end = starts[local_index][0] if local_index < len(starts) else len(lines)
            block_lines = lines[start:end]
            content_lines, proof_lines = _split_inline_proof(block_lines, current_number=number)
            env = infer_env(title)
            item = make_item(section, local_index, env, number, "\n".join(content_lines).strip(), proof_lines)
            items.append(item)
            if number:
                by_number[number] = item

        for proof_start, explicit_number in delayed_proofs:
            if explicit_number and explicit_number in by_number:
                proof_end = _next_item_or_section_end(lines, proof_start + 1)
                proof_text = "\n".join(lines[proof_start:proof_end]).strip()
                proof_text = PROOF_RE.sub(lambda m: m.group("rest"), proof_text, count=1).strip()
                if proof_text:
                    current = by_number[explicit_number].get("proof")
                    by_number[explicit_number]["proof"] = (current + "\n\n" + proof_text).strip() if current else proof_text

        return [item for item in items if item["content"]]


def _split_inline_proof(block_lines: list[str], current_number: str) -> tuple[list[str], str | None]:
    for idx, line in enumerate(block_lines):
        stripped = line.strip()
        proof_match = PROOF_RE.match(stripped)
        construction_match = CONSTRUCTION_RE.match(stripped)
        if proof_match or construction_match:
            explicit = proof_match.group("number") if proof_match else None
            if explicit and current_number and explicit != current_number:
                return block_lines[:idx], None
            content = block_lines[:idx]
            proof_tail = block_lines[idx:]
            for tail_idx, tail_line in enumerate(proof_tail[1:], start=1):
                later_proof = PROOF_RE.match(tail_line.strip())
                if later_proof and later_proof.group("number") and later_proof.group("number") != current_number:
                    proof_tail = proof_tail[:tail_idx]
                    break
            proof = "\n".join(proof_tail).strip()
            proof = PROOF_RE.sub(lambda m: m.group("rest"), proof, count=1).strip()
            proof = CONSTRUCTION_RE.sub(lambda m: m.group("rest"), proof, count=1).strip()
            return content, proof or None
    return block_lines, None


def _next_item_or_section_end(lines: list[str], start: int) -> int:
    for idx in range(start, len(lines)):
        if ITEM_START_RE.match(lines[idx].strip()):
            return idx
    return len(lines)


def infer_env(title: str) -> str:
    title_l = title.lower()
    for needle, env in TITLE_ENV_RULES:
        if needle in title_l:
            return env
    return "remark"


def make_item(
    section: MarkdownSection,
    local_index: int,
    env: str,
    number: str,
    content: str,
    proof: str | None,
) -> dict[str, Any]:
    ctx = section.context.as_json()
    return {
        "index": local_index,
        "label": f"{ENV_DISPLAY[env]} {ctx['section_number']}-{local_index}",
        "env": env,
        "number_components": _number_components(number),
        "context": ctx,
        "content": re.sub(r"(?m)^#{1,6}\s+", "", content).strip(),
        "dependencies": [],
        "proof": proof,
    }


def _number_components(number: str) -> list[str]:
    if not number:
        return []
    return [piece for piece in number.split(".") if piece]


def _looks_like_standalone_proof_section(section: MarkdownSection) -> bool:
    heading = f"{section.source_heading} {section.context.section}".lower()
    if "proof of" in heading or "证明" in heading:
        return True
    for line in section.text.splitlines()[:5]:
        if PROOF_RE.match(line.strip()):
            return True
    return False


def _standalone_proof_remark(section: MarkdownSection) -> dict[str, Any]:
    ctx = section.context.as_json()
    dependency = _proof_dependency(section.source_heading) or _proof_dependency(section.context.section)
    return {
        "index": 1,
        "label": f"Remark {ctx['section_number']}-1",
        "env": "remark",
        "number_components": [],
        "context": ctx,
        "content": section.text.strip(),
        "dependencies": [dependency] if dependency else [],
        "proof": None,
    }


def _proof_dependency(text: str) -> str | None:
    match = re.search(r"Proof of\s+(Theorem|Lemma|Proposition|Corollary|Claim)\s+([A-Z]?\d+(?:\.\d+)*)", text, re.I)
    if match:
        return f"{match.group(1).capitalize()} {match.group(2)}"
    return None
