from __future__ import annotations

import copy
import re
from typing import Any

from .models import ENV_DISPLAY, MarkdownSection


SOURCE_ITEM_LABELS: dict[str, tuple[str, ...]] = {
    "def": ("Definition", "Definitions", "Def.", "Def", "定义"),
    "thm": ("Theorem", "THEOREM", "Thm.", "Thm", "定理"),
    "prop": ("Proposition", "PROPOSITION", "Prop.", "Prop", "命题"),
    "lemma": ("Lemma", "LEMMA", "引理"),
    "cor": ("Corollary", "COROLLARY", "Cor.", "Cor", "推论"),
    "remark": ("Remark", "REMARK", "Remarks", "注", "注记"),
    "example": ("Example", "EXAMPLE", "例"),
    "exercise": ("Exercise", "EXERCISE", "Exercises", "练习"),
    "algorithm": ("Algorithm", "ALGORITHM", "算法"),
    "assumption": ("Assumption", "ASSUMPTION", "假设"),
    "claim": ("Claim", "CLAIM", "断言"),
    "conjecture": ("Conjecture", "CONJECTURE", "猜想"),
    "problem": ("Problem", "PROBLEM", "问题"),
    "question": ("Question", "QUESTION"),
    "notation": ("Notation", "NOTATION", "记号"),
}

SOURCE_NUMBER_RE = r"(?:[IVXLCDM]+|\d+[A-Za-z]?|[A-Z])(?:\.(?:[IVXLCDM]+|\d+[A-Za-z]?|[A-Z]))*"
LABEL_LOOKUP: dict[str, str] = {
    label.lower().rstrip("."): env for env, labels in SOURCE_ITEM_LABELS.items() for label in labels
}
SOURCE_ITEM_RE = re.compile(
    rf"(?im)^(?P<prefix>\s*(?:#{{1,6}}\s*)?)"
    rf"(?P<name>{'|'.join(re.escape(label) for labels in SOURCE_ITEM_LABELS.values() for label in labels)})"
    rf"\s*[:：.]?\s*(?P<number>{SOURCE_NUMBER_RE})(?P<after>\s*[.:：)]|\s+)"
)
PROOF_BOUNDARY_RE = re.compile(r"(?im)^\s*(?:Proof|PROOF|证明)\s*[.:：]?\s*")


def ground_audit_repair_payload(
    section: MarkdownSection,
    current_items: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply audit decisions using source text as the only authority for content/proof.

    The LLM audit is allowed to decide that labels should be added, updated, or deleted,
    but repaired item text is accepted only when it can be recovered from the source
    section. Explicitly numbered source items are re-extracted deterministically.
    """

    grounded = copy.deepcopy(payload)
    source_items = extract_explicit_source_items(section)
    source_by_label = {item["label"]: item for item in source_items}
    current_by_label = {str(item.get("label") or ""): item for item in current_items}
    llm_items = payload.get("repaired_items") or current_items
    deleted_labels = _deleted_labels(payload)
    desired_labels = _desired_labels(llm_items, payload, current_items)

    repaired: list[dict[str, Any]] = []
    seen: set[str] = set()
    accepted_from_source = 0
    rejected_text = 0

    for item in llm_items:
        label = str(item.get("label") or "").strip()
        if label in deleted_labels:
            continue
        if label in source_by_label:
            grounded_item = _merge_metadata(source_by_label[label], item, current_by_label.get(label))
            accepted_from_source += 1
        elif _item_text_is_source_grounded(item, section.text):
            grounded_item = copy.deepcopy(item)
        elif label in current_by_label and _item_text_is_source_grounded(current_by_label[label], section.text):
            grounded_item = copy.deepcopy(current_by_label[label])
            rejected_text += 1
        else:
            rejected_text += 1
            continue
        repaired.append(grounded_item)
        seen.add(str(grounded_item.get("label") or ""))

    for source_item in source_items:
        label = str(source_item.get("label") or "")
        if label in seen or label in deleted_labels:
            continue
        if desired_labels and label not in desired_labels:
            continue
        seed = _find_seed_item(label, llm_items, current_by_label)
        repaired.append(_merge_metadata(source_item, seed, current_by_label.get(label)))
        seen.add(label)
        accepted_from_source += 1

    repaired.sort(key=lambda item: _source_order_key(item, source_by_label))
    for index, item in enumerate(repaired, start=1):
        item["index"] = index

    grounding_notes = {
        "mode": "source_span_validation",
        "explicit_source_items_detected": len(source_items),
        "items_accepted_from_source": accepted_from_source,
        "items_rejected_or_reverted_for_ungrounded_text": rejected_text,
    }
    if rejected_text:
        grounded["audit_markdown"] = (
            str(grounded.get("audit_markdown") or "").rstrip()
            + "\n\nSource grounding note: rejected or reverted "
            + f"{rejected_text} repaired item(s) whose content/proof was not recoverable from source spans."
        ).strip()
    grounded["repaired_items"] = repaired
    grounded["source_grounding"] = grounding_notes
    return grounded


def extract_explicit_source_items(section: MarkdownSection) -> list[dict[str, Any]]:
    text = section.text
    matches = list(SOURCE_ITEM_RE.finditer(text))
    items: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        env = LABEL_LOOKUP.get(match.group("name").lower().rstrip("."))
        if env is None:
            continue
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if not block:
            continue
        number = match.group("number").strip()
        label = f"{ENV_DISPLAY[env]} {number}"
        content, proof = split_statement_and_proof(block)
        items.append(
            {
                "index": len(items) + 1,
                "label": label,
                "env": env,
                "number_components": [part for part in number.split(".") if part],
                "context": section.context.as_json(),
                "content": content,
                "dependencies": [],
                "proof": proof,
                "_source_start": start,
            }
        )
    return items


def split_statement_and_proof(block: str) -> tuple[str, str | None]:
    match = PROOF_BOUNDARY_RE.search(block)
    if match is None:
        return block.strip(), None
    content = block[: match.start()].strip()
    proof = block[match.end() :].strip()
    return content, proof or None


def source_text_contains(source_text: str, value: str | None) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    source_norm = _normalize_ws(source_text)
    value_norm = _normalize_ws(text)
    if value_norm in source_norm:
        return True
    stripped = PROOF_BOUNDARY_RE.sub("", text, count=1).strip()
    stripped = re.sub(r"\s*(?:\\\(\s*\\parallel\s*\\\)|\|\||∥)\s*$", "", stripped).strip()
    return bool(stripped and _normalize_ws(stripped) in source_norm)


def _item_text_is_source_grounded(item: dict[str, Any], source_text: str) -> bool:
    return source_text_contains(source_text, item.get("content")) and source_text_contains(source_text, item.get("proof"))


def _merge_metadata(
    source_item: dict[str, Any],
    llm_item: dict[str, Any] | None,
    current_item: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = {key: copy.deepcopy(value) for key, value in source_item.items() if not key.startswith("_")}
    if isinstance(llm_item, dict):
        proof_span = source_span_for_ordered_tokens(str(source_item.get("proof") or ""), llm_item.get("proof"))
        if proof_span is not None:
            merged["proof"] = proof_span
    for seed in (llm_item, current_item):
        if not isinstance(seed, dict):
            continue
        dependencies = seed.get("dependencies")
        if isinstance(dependencies, list):
            merged["dependencies"] = [str(dep).strip() for dep in dependencies if str(dep).strip()]
            break
    return merged


def _deleted_labels(payload: dict[str, Any]) -> set[str]:
    actions = (payload.get("patch_candidate") or {}).get("actions") or []
    return {
        str(action.get("target_label") or "").strip()
        for action in actions
        if action.get("action") == "delete" and str(action.get("target_label") or "").strip()
    }


def _desired_labels(
    llm_items: list[dict[str, Any]],
    payload: dict[str, Any],
    current_items: list[dict[str, Any]],
) -> set[str]:
    labels = {str(item.get("label") or "").strip() for item in llm_items if str(item.get("label") or "").strip()}
    labels.update(str(item.get("label") or "").strip() for item in current_items if str(item.get("label") or "").strip())
    actions = (payload.get("patch_candidate") or {}).get("actions") or []
    for action in actions:
        for key in ("target_label", "provisional_label", "anchor_target_label"):
            label = str(action.get(key) or "").strip()
            if label:
                labels.add(label)
    return labels


def _find_seed_item(
    label: str,
    llm_items: list[dict[str, Any]],
    current_by_label: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for item in llm_items:
        if str(item.get("label") or "").strip() == label:
            return item
    return current_by_label.get(label)


def _source_order_key(item: dict[str, Any], source_by_label: dict[str, dict[str, Any]]) -> tuple[int, int]:
    label = str(item.get("label") or "")
    source_item = source_by_label.get(label)
    if source_item is not None:
        return (0, int(source_item.get("_source_start") or 0))
    return (1, int(item.get("index") or 0))


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def source_span_for_ordered_tokens(source_text: str, candidate_text: str | None, *, min_coverage: float = 0.98) -> str | None:
    candidate = str(candidate_text or "").strip()
    if not candidate:
        return None
    if source_text_contains(source_text, candidate):
        return candidate
    source_tokens = _token_spans(source_text)
    candidate_tokens = [token for token, _, _ in _token_spans(candidate)]
    if not source_tokens or not candidate_tokens:
        return None
    source_index = 0
    matched: list[tuple[int, int]] = []
    for token in candidate_tokens:
        found = False
        while source_index < len(source_tokens):
            source_token, start, end = source_tokens[source_index]
            source_index += 1
            if source_token == token:
                matched.append((start, end))
                found = True
                break
        if not found:
            continue
    if len(matched) / len(candidate_tokens) < min_coverage:
        return None
    return source_text[matched[0][0] : matched[-1][1]].strip()


def _token_spans(value: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(0), match.start(), match.end())
        for match in re.finditer(r"\\[A-Za-z]+|\\[()\[\]]|[A-Za-z]+|\d+(?:\.\d+)*|[+*/=<>≤≥∞∈⊂⊃∪∩∅{}_^|-]", value)
    ]
