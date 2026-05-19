from __future__ import annotations

import unittest

from md2json_api.structure import looks_like_bare_section_candidate


class StructurePlannerTests(unittest.TestCase):
    def test_bare_section_candidate_rejects_body_enumerators(self) -> None:
        self.assertTrue(looks_like_bare_section_candidate("1.1 滤子", chapter_number="1"))
        self.assertFalse(
            looks_like_bare_section_candidate(
                "1. 首先,对任意 \\( A \\) 上的滤子基 \\( \\mathfrak{B} \\) 定义",
                chapter_number="1",
            )
        )
        self.assertFalse(
            looks_like_bare_section_candidate(
                "F. 1 若 \\( A, B \\in \\mathfrak{F} \\) ,则 \\( A \\cap B \\in \\mathfrak{F} \\)",
                chapter_number="1",
            )
        )
        self.assertFalse(
            looks_like_bare_section_candidate(
                "6 给定了素数 \\( p \\) ,对域 \\( \\mathbb{Q} \\) 的任意元素可以考虑赋值",
                chapter_number="1",
            )
        )


if __name__ == "__main__":
    unittest.main()
