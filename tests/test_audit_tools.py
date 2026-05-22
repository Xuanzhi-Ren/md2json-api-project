from __future__ import annotations

import unittest

from md2json_api.audit_tools import AuditSourceToolExecutor
from md2json_api.models import MarkdownSection, SectionContext


def _section() -> MarkdownSection:
    return MarkdownSection(
        index=1,
        context=SectionContext(
            chapter="Test notes",
            chapter_number="",
            section="1. Test section",
            section_number="1",
        ),
        text=(
            "### 1. Test section\n\n"
            "Theorem 1. Every finite subgroup of $K^\\times$ is cyclic.\n\n"
            "Proof. Let $G$ be a finite subgroup and choose an element of maximal order.\n\n"
            "Lemma 2. If $x \\in U$, then $x+y \\in U$ for all $y \\in U$.\n"
        ),
        start_line=10,
        end_line=16,
        heading_level=3,
        source_heading="1. Test section",
    )


class AuditSourceToolTests(unittest.TestCase):
    def test_list_source_item_labels_records_llm_labels_without_hard_mining(self) -> None:
        executor = AuditSourceToolExecutor(_section(), [])

        result = executor.execute("list_source_item_labels", {"items": [], "notes": "no labels declared"})

        self.assertEqual(result["declared_labels"], [])
        self.assertEqual(result["current_labels"], [])
        self.assertIn("model-declared", result["tool_note"])

    def test_build_repaired_items_copies_llm_selected_source_spans(self) -> None:
        executor = AuditSourceToolExecutor(_section(), [])
        executor.execute(
            "list_source_item_labels",
            {
                "items": [
                    {
                        "label": "Theorem 1",
                        "env": "thm",
                        "number_components": ["1"],
                        "anchor_text": "Theorem 1.",
                        "reason": "explicit theorem label in source",
                    },
                    {
                        "label": "Lemma 2",
                        "env": "lemma",
                        "number_components": ["2"],
                        "anchor_text": "Lemma 2.",
                        "reason": "explicit lemma label in source",
                    },
                ],
                "notes": "model-declared labels",
            },
        )

        result = executor.execute(
            "build_repaired_items",
            {
                "audit_markdown": "Two explicit source items repaired from spans.",
                "overall_assessment": "major repair",
                "actions": [],
                "open_questions": [],
                "items": [
                    {
                        "label": "Theorem 1",
                        "env": "thm",
                        "number_components": ["1"],
                        "dependencies": [],
                        "content_span": {
                            "start_anchor": "Theorem 1.",
                            "end_anchor": "Proof.",
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": True,
                            "include_end": False,
                        },
                        "proof_span": {
                            "start_anchor": "Proof.",
                            "end_anchor": "Lemma 2.",
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": False,
                            "include_end": False,
                        },
                        "preserve_current_label": None,
                        "source_order_anchor": "Theorem 1.",
                        "reason": "copy theorem and proof from source spans",
                    },
                    {
                        "label": "Lemma 2",
                        "env": "lemma",
                        "number_components": ["2"],
                        "dependencies": [],
                        "content_span": {
                            "start_anchor": "Lemma 2.",
                            "end_anchor": None,
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": True,
                            "include_end": False,
                        },
                        "proof_span": None,
                        "preserve_current_label": None,
                        "source_order_anchor": "Lemma 2.",
                        "reason": "copy lemma from source span",
                    },
                ],
            },
        )

        items = result["repaired_items"]
        self.assertEqual([item["label"] for item in items], ["Theorem 1", "Lemma 2"])
        self.assertEqual(items[0]["content"], "Theorem 1. Every finite subgroup of $K^\\times$ is cyclic.")
        self.assertEqual(
            items[0]["proof"],
            "Let $G$ be a finite subgroup and choose an element of maximal order.",
        )
        self.assertEqual(
            items[1]["content"],
            "Lemma 2. If $x \\in U$, then $x+y \\in U$ for all $y \\in U$.",
        )
        self.assertEqual(result["tool_validation"]["declared_labels_count"], 2)

    def test_preserved_item_with_non_source_backed_text_is_repaired_from_declared_anchors(self) -> None:
        section = MarkdownSection(
            index=9,
            context=SectionContext(
                chapter="Complete book",
                chapter_number="",
                section="9. Some Closedness Criteria",
                section_number="9",
            ),
            text=(
                "Corollary 9.2.1. Let \\( {f}_{1} \\) satisfy \\( {z}_{1}\\right) \\leq 0.\n\n"
                "Proof. Apply Theorem 9.2. ||\n\n"
                "COROLLARY 9.2.2. Let \\( {f}_{2} \\) satisfy another condition."
            ),
            start_line=100,
            end_line=105,
            heading_level=2,
            source_heading="Some Closedness Criteria",
        )
        current_items = [
            {
                "index": 1,
                "label": "Corollary 9.2.1",
                "env": "cor",
                "number_components": ["9", "2", "1"],
                "context": section.context.as_json(),
                "content": "Corollary 9.2.1. Let \\( {f}_{1} \\) satisfy \\( {{z}_{1}}\\right) \\leq 0.",
                "dependencies": ["Theorem 9.2"],
                "proof": "Apply Theorem 9.2. ||",
            },
            {
                "index": 2,
                "label": "Corollary 9.2.2",
                "env": "cor",
                "number_components": ["9", "2", "2"],
                "context": section.context.as_json(),
                "content": "COROLLARY 9.2.2. Let \\( {f}_{2} \\) satisfy another condition.",
                "dependencies": [],
                "proof": None,
            },
        ]
        executor = AuditSourceToolExecutor(section, current_items)

        result = executor.execute(
            "build_repaired_items",
            {
                "audit_markdown": "Preserve declared items.",
                "overall_assessment": "no change",
                "actions": [],
                "open_questions": [],
                "items": [
                    {
                        "label": "Corollary 9.2.1",
                        "env": "cor",
                        "number_components": ["9", "2", "1"],
                        "dependencies": ["Theorem 9.2"],
                        "content_span": None,
                        "proof_span": None,
                        "preserve_current_label": "Corollary 9.2.1",
                        "source_order_anchor": "Corollary 9.2.1.",
                        "reason": "preserve item using source anchors if current text is not source-backed",
                    },
                    {
                        "label": "Corollary 9.2.2",
                        "env": "cor",
                        "number_components": ["9", "2", "2"],
                        "dependencies": [],
                        "content_span": None,
                        "proof_span": None,
                        "preserve_current_label": "Corollary 9.2.2",
                        "source_order_anchor": "COROLLARY 9.2.2.",
                        "reason": "unchanged next item",
                    },
                ],
            },
        )

        items = result["repaired_items"]
        self.assertEqual([item["label"] for item in items], ["Corollary 9.2.1", "Corollary 9.2.2"])
        self.assertIn("\\( {z}_{1}\\right)", items[0]["content"])
        self.assertNotIn("{{z}_{1}}", items[0]["content"])
        self.assertEqual(items[0]["proof"], "Apply Theorem 9.2. ||")
        self.assertEqual(result["tool_validation"]["warnings"], [])


if __name__ == "__main__":
    unittest.main()
