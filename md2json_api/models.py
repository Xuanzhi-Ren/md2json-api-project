from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOWED_ENVS = (
    "def",
    "thm",
    "prop",
    "lemma",
    "cor",
    "remark",
    "example",
    "exercise",
    "algorithm",
    "assumption",
    "claim",
    "conjecture",
    "problem",
    "question",
    "notation",
)

ENV_ALIASES = {
    "def": "def",
    "defn": "def",
    "definition": "def",
    "definitions": "def",
    "定义": "def",
    "thm": "thm",
    "theorem": "thm",
    "定理": "thm",
    "prop": "prop",
    "proposition": "prop",
    "命题": "prop",
    "lemma": "lemma",
    "引理": "lemma",
    "cor": "cor",
    "corollary": "cor",
    "推论": "cor",
    "remark": "remark",
    "remarks": "remark",
    "注": "remark",
    "注记": "remark",
    "example": "example",
    "examples": "example",
    "例": "example",
    "exercise": "exercise",
    "exercises": "exercise",
    "练习": "exercise",
    "algorithm": "algorithm",
    "算法": "algorithm",
    "assumption": "assumption",
    "假设": "assumption",
    "claim": "claim",
    "断言": "claim",
    "conjecture": "conjecture",
    "猜想": "conjecture",
    "problem": "problem",
    "问题": "problem",
    "question": "question",
    "notation": "notation",
    "记号": "notation",
}

ENV_DISPLAY = {
    "def": "Definition",
    "thm": "Theorem",
    "prop": "Proposition",
    "lemma": "Lemma",
    "cor": "Corollary",
    "remark": "Remark",
    "example": "Example",
    "exercise": "Exercise",
    "algorithm": "Algorithm",
    "assumption": "Assumption",
    "claim": "Claim",
    "conjecture": "Conjecture",
    "problem": "Problem",
    "question": "Question",
    "notation": "Notation",
}


@dataclass(frozen=True)
class SectionContext:
    chapter: str
    chapter_number: str
    section: str
    section_number: str

    def as_json(self) -> dict[str, str]:
        return {
            "chapter": self.chapter,
            "section": self.section,
            "chapter_number": self.chapter_number,
            "section_number": self.section_number,
        }


@dataclass(frozen=True)
class MarkdownSection:
    index: int
    context: SectionContext
    text: str
    start_line: int
    end_line: int
    heading_level: int | None = None
    source_heading: str = ""
    kind: str = "body"


@dataclass(frozen=True)
class ConversionResult:
    source_file: Path
    out_dir: Path
    sections_written: int
    items_total: int
    files: list[dict[str, Any]]
