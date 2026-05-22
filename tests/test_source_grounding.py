from __future__ import annotations

import unittest

from md2json_api.models import MarkdownSection, SectionContext
from md2json_api.source_grounding import (
    extract_explicit_source_items,
    ground_audit_repair_payload,
    source_span_for_ordered_tokens,
)


def make_section(text: str) -> MarkdownSection:
    return MarkdownSection(
        index=1,
        context=SectionContext(
            chapter="Complete book",
            chapter_number="",
            section="1. Test Section",
            section_number="1",
        ),
        text=text,
        start_line=1,
        end_line=len(text.splitlines()),
        heading_level=2,
        source_heading="Test Section",
    )


class SourceGroundingTests(unittest.TestCase):
    def test_extracts_explicit_items_from_source_spans(self) -> None:
        section = make_section(
            """## Test Section

THEOREM 1.1. First source statement.

Proof. First source proof. \\( \\parallel \\)

Corollary 1.1.1. Source corollary statement.

Proof. Source corollary proof.
"""
        )

        items = extract_explicit_source_items(section)

        self.assertEqual([item["label"] for item in items], ["Theorem 1.1", "Corollary 1.1.1"])
        self.assertEqual(items[0]["content"], "THEOREM 1.1. First source statement.")
        self.assertEqual(items[0]["proof"], "First source proof. \\( \\parallel \\)")

    def test_grounding_replaces_llm_rewrites_with_source_text(self) -> None:
        section = make_section(
            """## Test Section

THEOREM 1.1. First source statement.

Proof. First source proof. \\( \\parallel \\)

Corollary 1.1.1. Source corollary statement with formula
\\[
x+y=z.
\\]

Proof. Source corollary proof with details.
"""
        )
        current_items = [
            {
                "index": 1,
                "label": "Theorem 1.1",
                "env": "thm",
                "number_components": ["1", "1"],
                "context": section.context.as_json(),
                "content": "THEOREM 1.1. First source statement.",
                "dependencies": [],
                "proof": "First source proof. \\( \\parallel \\)",
            }
        ]
        audit_payload = {
            "audit_markdown": "missing corollary",
            "patch_candidate": {
                "section_id": "section01",
                "overall_assessment": "major repair",
                "actions": [
                    {
                        "action": "add",
                        "target_label": None,
                        "anchor_position": "after",
                        "anchor_target_label": "Theorem 1.1",
                        "provisional_label": "Corollary 1.1.1",
                        "env": "cor",
                        "reason": "missing explicit corollary",
                        "content_excerpt": "Corollary 1.1.1.",
                        "field_updates_note": None,
                        "candidate_item": None,
                    }
                ],
                "open_questions": [],
            },
            "repaired_items": [
                current_items[0],
                {
                    "index": 2,
                    "label": "Corollary 1.1.1",
                    "env": "cor",
                    "number_components": ["1", "1", "1"],
                    "context": section.context.as_json(),
                    "content": "Corollary 1.1.1. Rewritten summary.",
                    "dependencies": ["Theorem 1.1"],
                    "proof": "Invented proof summary.",
                },
            ],
        }

        grounded = ground_audit_repair_payload(section, current_items, audit_payload)
        corollary = grounded["repaired_items"][1]

        self.assertIn("Source corollary statement with formula", corollary["content"])
        self.assertIn("x+y=z.", corollary["content"])
        self.assertEqual(corollary["proof"], "Source corollary proof with details.")
        self.assertEqual(corollary["dependencies"], ["Theorem 1.1"])
        self.assertEqual(grounded["source_grounding"]["items_accepted_from_source"], 2)

    def test_grounding_can_keep_llm_proof_boundary_when_text_is_source_backed(self) -> None:
        section = make_section(
            """## Test Section

Corollary 1.2. Source corollary statement.

Proof. Exact source proof sentence.

Later commentary before the next item should not be forced into the proof.
"""
        )
        current_items: list[dict[str, object]] = []
        audit_payload = {
            "audit_markdown": "missing corollary",
            "patch_candidate": {
                "section_id": "section01",
                "overall_assessment": "major repair",
                "actions": [],
                "open_questions": [],
            },
            "repaired_items": [
                {
                    "index": 1,
                    "label": "Corollary 1.2",
                    "env": "cor",
                    "number_components": ["1", "2"],
                    "context": section.context.as_json(),
                    "content": "Corollary 1.2. Source corollary statement.",
                    "dependencies": [],
                    "proof": "Exact source proof sentence.",
                }
            ],
        }

        grounded = ground_audit_repair_payload(section, current_items, audit_payload)

        self.assertEqual(grounded["repaired_items"][0]["proof"], "Exact source proof sentence.")

    def test_source_span_for_ordered_tokens_recovers_exact_source_spacing(self) -> None:
        source = (
            "Any \\( {C}_{i} \\) which is empty can be omitted without changing the convex hull, "
            "and every other \\( {C}_{i} \\) has \\( {0}^{ + }{C}_{i} = \\{ 0\\} \\) .\n\n"
            "Later commentary."
        )
        candidate = (
            "Any \\( {C}_{i} \\) which is empty can be omitted without changing the convex hull, "
            "and every other \\( {C}_{i} \\) has \\( {0}^{+}{C}_{i}=\\{0\\} \\)."
        )

        span = source_span_for_ordered_tokens(source, candidate)

        self.assertEqual(
            span,
            "Any \\( {C}_{i} \\) which is empty can be omitted without changing the convex hull, "
            "and every other \\( {C}_{i} \\) has \\( {0}^{ + }{C}_{i} = \\{ 0\\} \\)",
        )


if __name__ == "__main__":
    unittest.main()
