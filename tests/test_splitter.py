from __future__ import annotations

import unittest

from md2json_api.splitter import split_markdown_document, split_markdown_text


class SplitterTests(unittest.TestCase):
    def test_chapter_letter_sections_ignore_theorem_heading(self) -> None:
        text = """## Chapter 13 First order differentiation

## A Ultratangent space

### 13.1. Theorem.

Statement.

## B Length property

13.2. Lemma. Statement.
"""
        sections = split_markdown_text(text, "chapter13.md")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].context.chapter, "Chapter 13. First order differentiation")
        self.assertEqual(sections[0].context.section_number, "13.A")
        self.assertEqual(sections[1].context.section_number, "13.B")
        self.assertIn("13.1. Theorem", sections[0].text)

    def test_numeric_sections(self) -> None:
        text = """# Chapter 1 Algebra

## 1.1 Groups

Definition 1.1 text

## 1.2 Rings

Theorem 1.2 text
"""
        sections = split_markdown_text(text, "book.md")
        self.assertEqual([s.context.section_number for s in sections], ["1.1", "1.2"])

    def test_paper_sections_exclude_references_but_keep_appendix(self) -> None:
        text = """# Paper Title

## Abstract

No extraction here.

## 1 Introduction

Intro prose.

### 1.1 Problem statement

Theorem 1.1. Something true.

## 2 Upper bounds

Theorem 2.1. A bound.

### 2.1 Uniform matroids

Proposition 2.2. A proposition.

## References

[1] A reference.

## A Proof of Theorem 2.1

Proof. Details.
"""
        plan = split_markdown_document(text, "paper.md")
        self.assertEqual(
            [s.context.section_number for s in plan.sections],
            ["1", "1.1", "2", "2.1", "A"],
        )
        self.assertNotIn("References", plan.sections[3].text)
        self.assertIn("Proof. Details.", plan.sections[4].text)
        self.assertIn("[1] A reference.", plan.back_matter)

    def test_chinese_chapter_synthetic_first_section(self) -> None:
        text = """## 第一章 域的赋值

## 阅读提示

定义 1.1.1 非空集上的滤子意谓...

命题 1.1.4 完备化具有泛性质.

### 1.2 Krull 赋值与完备化

定义 1.2.3 全序交换群意谓...
"""
        plan = split_markdown_document(text, "chapter10.md")
        self.assertEqual([s.context.section_number for s in plan.sections], ["1.1", "1.2"])
        self.assertEqual(plan.sections[0].context.chapter, "Chapter 1. 域的赋值")
        self.assertIn("created a synthetic first section", " ".join(plan.warnings))


if __name__ == "__main__":
    unittest.main()
