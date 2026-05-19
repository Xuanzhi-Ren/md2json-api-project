from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import MarkdownSection, SectionContext


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
CHAPTER_RE = re.compile(
    r"^(?:chapter|part)\s+([A-Za-z0-9IVXLCDM]+)(?:[\.:])?\s*(.*?)\s*$",
    re.I,
)
CHINESE_CHAPTER_RE = re.compile(r"^第([一二三四五六七八九十百零〇两\d]+)章\s*(.*?)\s*$")
NUMERIC_SECTION_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[\.)])?\s+(.+?)\s*$")
LETTER_SECTION_RE = re.compile(r"^([A-Z])(?:[\.)])?\s+(.+?)\s*$")
APPENDIX_SECTION_RE = re.compile(r"^Appendix\s+([A-Z])(?:[\.:])?\s*(.*?)\s*$", re.I)
NUMBERED_ITEM_RE = re.compile(
    r"^(?:"
    r"(?:Definition|Theorem|Corollary|Proposition|Lemma|Example|Exercise|Remark|"
    r"Algorithm|Assumption|Claim|Conjecture|Problem|Question|Notation)"
    r"|定义|定理|命题|引理|推论|例|练习|注记|算法|假设|断言|猜想|问题|记号"
    r")\s*([A-Z]?\d+(?:\.\d+)*)",
    re.I,
)
RESULT_HEADING_RE = re.compile(
    r"^(?:"
    r"Definition|Theorem|Corollary|Proposition|Lemma|Example|Exercise|Remark|"
    r"Algorithm|Assumption|Claim|Conjecture|Notation|Proof|"
    r"定义|定理|命题|引理|推论|例|练习|注记|算法|假设|断言|猜想|记号|证明"
    r")\b",
    re.I,
)
BACK_MATTER_RE = re.compile(
    r"^(?:references|bibliography|acknowledgements?|funding|author contributions?|"
    r"data availability|conflict of interest|competing interests|index|subject index)\b",
    re.I,
)
FRONT_MATTER_RE = re.compile(r"^(?:abstract|contents|table of contents|preface|foreword)\b", re.I)


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    line: int


@dataclass(frozen=True)
class SectionStart:
    line: int
    section_number: str
    title: str
    chapter: str
    chapter_number: str
    heading_level: int | None
    source_heading: str
    synthetic: bool = False


@dataclass(frozen=True)
class SplitPlan:
    sections: list[MarkdownSection]
    front_matter: str = ""
    back_matter: str = ""
    warnings: list[str] = field(default_factory=list)


def split_markdown_file(path: Path) -> list[MarkdownSection]:
    return split_markdown_document(path.read_text(encoding="utf-8"), source_name=path.name).sections


def split_markdown_text(text: str, source_name: str = "input.md") -> list[MarkdownSection]:
    return split_markdown_document(text, source_name=source_name).sections


def split_markdown_document(text: str, source_name: str = "input.md") -> SplitPlan:
    lines = text.splitlines()
    source_stem = Path(source_name).stem
    headings = list(_iter_headings(lines))
    title = _detect_document_title(headings) or source_stem
    chapter = title
    chapter_number = ""
    starts: list[SectionStart] = []
    excluded_starts: list[int] = []
    warnings: list[str] = []

    for heading in headings:
        if is_back_matter_heading(heading.title):
            excluded_starts.append(heading.line)
            continue

        parsed_chapter = parse_chapter_heading(heading.title)
        if parsed_chapter is not None:
            chapter_number, chapter = parsed_chapter
            continue

        if FRONT_MATTER_RE.match(heading.title.strip()):
            continue

        parsed_section = parse_section_heading(heading.title, chapter_number=chapter_number)
        if parsed_section is None:
            continue
        section_number, section_title = parsed_section
        starts.append(
            SectionStart(
                line=heading.line,
                section_number=section_number,
                title=section_title,
                chapter=chapter,
                chapter_number=chapter_number,
                heading_level=heading.level,
                source_heading=heading.title,
            )
        )

    if not starts:
        body_end_line = min(excluded_starts) if excluded_starts else len(lines) + 1
        context = SectionContext(
            chapter=chapter,
            chapter_number=chapter_number,
            section=chapter,
            section_number=chapter_number or "1",
        )
        body = _join_lines(lines, 1, body_end_line).strip()
        back = _join_lines(lines, body_end_line, len(lines) + 1).strip()
        return SplitPlan(
            sections=[
                MarkdownSection(
                    index=1,
                    context=context,
                    text=body,
                    start_line=1,
                    end_line=min(body_end_line - 1, len(lines)),
                    heading_level=None,
                    source_heading=chapter,
                )
            ],
            back_matter=back,
            warnings=["no explicit section headings found; converted the body as one section"],
        )

    front_text = _join_lines(lines, 1, starts[0].line)
    front_matter = front_text.strip()
    synthetic_presection = _make_synthetic_presection(
        front_text,
        first_start=starts[0],
        fallback_chapter=starts[0].chapter,
        fallback_chapter_number=starts[0].chapter_number,
    )
    if synthetic_presection is not None:
        starts.insert(0, synthetic_presection)
        front_matter = ""
        warnings.append(
            "created a synthetic first section from material before the first explicit section heading"
        )

    sections: list[MarkdownSection] = []
    boundary_lines = sorted([start.line for start in starts] + excluded_starts + [len(lines) + 1])
    for local_index, start in enumerate(starts, start=1):
        next_line = _next_boundary_after(start.line, boundary_lines)
        end_line = min(next_line - 1, len(lines))
        while end_line >= start.line and not lines[end_line - 1].strip():
            end_line -= 1
        chunk = _join_lines(lines, start.line, next_line).strip()
        context = SectionContext(
            chapter=start.chapter,
            chapter_number=start.chapter_number,
            section=f"{start.section_number}. {start.title}".strip(),
            section_number=start.section_number,
        )
        if start.synthetic:
            context = SectionContext(
                chapter=start.chapter,
                chapter_number=start.chapter_number,
                section=start.title,
                section_number=start.section_number,
            )
        sections.append(
            MarkdownSection(
                index=local_index,
                context=context,
                text=chunk,
                start_line=start.line,
                end_line=end_line,
                heading_level=start.heading_level,
                source_heading=start.source_heading,
                kind="body",
            )
        )

    back_matter = _collect_excluded_blocks(lines, starts, excluded_starts)
    _append_structure_warnings(sections, warnings)
    return SplitPlan(sections=sections, front_matter=front_matter, back_matter=back_matter, warnings=warnings)


def _iter_headings(lines: list[str]) -> list[Heading]:
    headings: list[Heading] = []
    for line_number, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if not match:
            continue
        headings.append(Heading(level=len(match.group(1)), title=_clean_title(match.group(2)), line=line_number))
    return headings


def _detect_document_title(headings: list[Heading]) -> str | None:
    for heading in headings:
        title = heading.title.strip()
        if not title:
            continue
        if parse_chapter_heading(title) is not None:
            continue
        if parse_section_heading(title, chapter_number="") is not None:
            continue
        if is_back_matter_heading(title) or FRONT_MATTER_RE.match(title):
            continue
        if heading.level == 1:
            return title
    return None


def parse_chapter_heading(title: str) -> tuple[str, str] | None:
    text = _clean_title(title)
    match = CHAPTER_RE.match(text)
    if match:
        number = match.group(1).strip()
        rest = match.group(2).strip()
        return number, f"Chapter {number}" + (f". {rest}" if rest else "")
    zh = CHINESE_CHAPTER_RE.match(text)
    if zh:
        number = _chinese_number_to_int(zh.group(1))
        rest = zh.group(2).strip()
        return number, f"Chapter {number}" + (f". {rest}" if rest else "")
    return None


def parse_section_heading(title: str, *, chapter_number: str) -> tuple[str, str] | None:
    text = _clean_title(title)
    if not text or is_back_matter_heading(text) or RESULT_HEADING_RE.match(text):
        return None
    appendix = APPENDIX_SECTION_RE.match(text)
    if appendix:
        number = appendix.group(1)
        rest = appendix.group(2).strip() or f"Appendix {number}"
        return number, rest
    numeric = NUMERIC_SECTION_RE.match(text)
    if numeric:
        number = numeric.group(1)
        rest = numeric.group(2).strip()
        if RESULT_HEADING_RE.match(rest):
            return None
        return number, rest
    letter = LETTER_SECTION_RE.match(text)
    if letter and not _looks_like_sentence(text):
        number = letter.group(1)
        rest = letter.group(2).strip()
        if _is_result_heading_title(rest) and not rest.lower().startswith("proof of"):
            return None
        if chapter_number:
            number = f"{chapter_number}.{number}"
        return number, rest
    return None


def is_back_matter_heading(title: str) -> bool:
    return BACK_MATTER_RE.match(_clean_title(title)) is not None


def _is_result_heading_title(title: str) -> bool:
    text = _clean_title(title)
    if not RESULT_HEADING_RE.match(text):
        return False
    return NUMBERED_ITEM_RE.match(text) is not None or re.match(r"^(?:Proof|证明)\b", text, re.I) is not None


def _make_synthetic_presection(
    front_text: str,
    *,
    first_start: SectionStart,
    fallback_chapter: str,
    fallback_chapter_number: str,
) -> SectionStart | None:
    if not front_text.strip() or not _has_extractable_marker(front_text):
        return None
    inferred = _infer_section_number_from_items(front_text)
    if not inferred:
        inferred = _previous_section_number(first_start.section_number) or "pre"
    title = f"{inferred}. Inferred pre-section material"
    return SectionStart(
        line=1,
        section_number=inferred,
        title=title,
        chapter=fallback_chapter,
        chapter_number=fallback_chapter_number,
        heading_level=None,
        source_heading=title,
        synthetic=True,
    )


def _infer_section_number_from_items(text: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("#").strip()
        match = NUMBERED_ITEM_RE.match(line)
        if not match:
            continue
        number = match.group(1).strip()
        pieces = number.split(".")
        if len(pieces) >= 2:
            return ".".join(pieces[:-1])
    return None


def _previous_section_number(section_number: str) -> str | None:
    pieces = section_number.split(".")
    if not pieces or not pieces[-1].isdigit():
        return None
    previous = int(pieces[-1]) - 1
    if previous <= 0:
        return None
    return ".".join([*pieces[:-1], str(previous)])


def _has_extractable_marker(text: str) -> bool:
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("#").strip()
        if NUMBERED_ITEM_RE.match(line):
            return True
        if RESULT_HEADING_RE.match(line):
            return True
    return False


def _append_structure_warnings(sections: list[MarkdownSection], warnings: list[str]) -> None:
    seen: set[str] = set()
    for section in sections:
        key = section.context.section_number
        if key in seen:
            warnings.append(f"duplicate section number detected: {key}")
        seen.add(key)
        if len(section.text) > 45000:
            warnings.append(
                f"section {section.index:02d} is very long ({len(section.text)} chars); consider smaller chunks"
            )


def _join_lines(lines: list[str], start_line: int, end_line_exclusive: int) -> str:
    start = max(start_line, 1)
    end = max(min(end_line_exclusive, len(lines) + 1), start)
    return "\n".join(lines[start - 1 : end - 1])


def _next_boundary_after(line: int, boundary_lines: list[int]) -> int:
    for boundary in boundary_lines:
        if boundary > line:
            return boundary
    return boundary_lines[-1] if boundary_lines else line + 1


def _collect_excluded_blocks(lines: list[str], starts: list[SectionStart], excluded_starts: list[int]) -> str:
    if not excluded_starts:
        return ""
    boundaries = sorted([start.line for start in starts] + [len(lines) + 1])
    blocks: list[str] = []
    for excluded_start in sorted(excluded_starts):
        end_line = _next_boundary_after(excluded_start, boundaries)
        block = _join_lines(lines, excluded_start, end_line).strip()
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip())


def _looks_like_sentence(text: str) -> bool:
    return text.endswith((".", "?", "!", ";", ":")) and len(text) > 80


def _chinese_number_to_int(raw: str) -> str:
    text = raw.strip()
    if text.isdigit():
        return text
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return "10"
    if "十" in text:
        left, _, right = text.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return str(tens * 10 + ones)
    if text in digits:
        return str(digits[text])
    return text
