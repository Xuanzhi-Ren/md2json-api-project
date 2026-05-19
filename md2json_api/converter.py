from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .audit_repairer import (
    AzureChatSectionAuditRepairer,
    MockSectionAuditRepairer,
    NoopSectionAuditRepairer,
    OpenAISectionAuditRepairer,
)
from .azure_extractor import AzureChatSectionExtractor
from .local_extractor import LocalSectionExtractor
from .mock_extractor import MockApiSectionExtractor
from .models import ALLOWED_ENVS, ENV_ALIASES, ENV_DISPLAY, ConversionResult, MarkdownSection
from .openai_extractor import OpenAISectionExtractor
from .quality import build_quality_report
from .splitter import split_markdown_document
from .structure import (
    build_structure_candidates,
    needs_structure_planner,
    split_plan_from_structure_plan,
    write_structure_planner_artifacts,
)
from .structure_planner import (
    AzureChatStructurePlanner,
    MockStructurePlanner,
    NoopStructurePlanner,
    OpenAIStructurePlanner,
)
from .writers import write_outputs


class SectionExtractor(Protocol):
    def extract_section(self, section: MarkdownSection) -> list[dict[str, Any]]:
        ...


class SectionAuditRepairer(Protocol):
    def audit_repair_section(self, section: MarkdownSection, current_items: list[dict[str, Any]]) -> dict[str, Any]:
        ...


class StructurePlanner(Protocol):
    def plan_document(
        self,
        *,
        source_name: str,
        source_text: str,
        hard_plan: Any,
        prompt_profile: str,
    ) -> dict[str, Any] | None:
        ...


@dataclass(frozen=True)
class ConverterConfig:
    backend: str = "openai"
    model: str = "gpt-5.2"
    api_key: str | None = None
    base_url: str | None = None
    azure_endpoint: str | None = None
    azure_api_version: str = "2024-10-21"
    max_output_tokens: int | None = None
    prompt_profile: str = "auto"
    audit_mode: str = "auto"
    structure_mode: str = "auto"
    resume: bool = False


class MarkdownJsonConverter:
    def __init__(self, config: ConverterConfig) -> None:
        self.config = config
        self.structure_planner = self._build_structure_planner(config)
        self.extractor = self._build_extractor(config)
        self.auditor = self._build_auditor(config)

    def convert(self, input_md: Path, out_dir: Path | None = None) -> ConversionResult:
        input_md = input_md.expanduser().resolve()
        if out_dir is None:
            out_dir = input_md.with_name(f"{input_md.stem}_json")
        else:
            out_dir = out_dir.expanduser().resolve()

        if hasattr(self.extractor, "set_trace_dir"):
            trace_name = "mock_api_calls" if self.config.backend == "mock" else "api_calls"
            trace_dir = out_dir / trace_name
            if not self.config.resume:
                cleanup_trace_dir(trace_dir)
            self.extractor.set_trace_dir(trace_dir)
        if hasattr(self.auditor, "set_trace_dir"):
            trace_name = "mock_audit_api_calls" if self.config.backend == "mock" else "audit_api_calls"
            trace_dir = out_dir / trace_name
            if not self.config.resume:
                cleanup_trace_dir(trace_dir)
            self.auditor.set_trace_dir(trace_dir)
        if hasattr(self.structure_planner, "set_trace_dir"):
            trace_name = "mock_structure_api_call" if self.config.backend == "mock" else "structure_api_call"
            trace_dir = out_dir / trace_name
            if not self.config.resume:
                cleanup_structure_trace_dir(trace_dir)
            self.structure_planner.set_trace_dir(trace_dir)

        source_text = input_md.read_text(encoding="utf-8")
        hard_split_plan = split_markdown_document(source_text, source_name=input_md.name)
        structure_candidates = build_structure_candidates(source_text)
        structure_plan: dict[str, Any] | None = None
        structure_used = should_use_structure_planner(self.config, source_text, hard_split_plan)
        if structure_used:
            structure_plan = self.structure_planner.plan_document(
                source_name=input_md.name,
                source_text=source_text,
                hard_plan=hard_split_plan,
                prompt_profile=self.config.prompt_profile,
            )
        split_plan = (
            split_plan_from_structure_plan(
                source_text=source_text,
                source_name=input_md.name,
                plan=structure_plan,
                fallback_plan=hard_split_plan,
            )
            if structure_plan is not None
            else hard_split_plan
        )
        sections = split_plan.sections
        all_items: list[dict[str, Any]] = []
        section_items: list[list[dict[str, Any]]] = []
        initial_section_items: list[list[dict[str, Any]]] = []
        audit_results: list[dict[str, Any] | None] = []

        for section in sections:
            raw_items = self.extractor.extract_section(section)
            initial_normalized = normalize_items(raw_items, section, global_start=1)
            initial_section_items.append(initial_normalized)
            audit_result: dict[str, Any] | None = None
            repaired_raw_items: list[dict[str, Any]] = initial_normalized
            if audit_enabled(self.config):
                audit_result = self.auditor.audit_repair_section(section, initial_normalized)
                repaired_raw_items = audit_result.get("repaired_items") or []
            audit_results.append(audit_result)
            normalized_local = normalize_items(repaired_raw_items, section, global_start=len(all_items) + 1)
            section_items.append(normalized_local)
            for item in normalized_local:
                global_item = copy.deepcopy(item)
                global_item["index"] = len(all_items) + 1
                all_items.append(global_item)

        result = write_outputs(
            source_file=input_md,
            out_dir=out_dir,
            sections=sections,
            section_items=section_items,
            all_items=all_items,
            front_matter=split_plan.front_matter,
            back_matter=split_plan.back_matter,
            split_warnings=split_plan.warnings,
            initial_section_items=initial_section_items if audit_enabled(self.config) else None,
            audit_results=audit_results if audit_enabled(self.config) else None,
            quality_report=build_quality_report(
                source_file=input_md,
                sections=sections,
                section_items=section_items,
                all_items=all_items,
                split_warnings=split_plan.warnings,
            ),
        )
        write_structure_planner_artifacts(
            out_dir,
            candidates=structure_candidates,
            plan=structure_plan,
            mode=self.config.structure_mode,
            used=structure_used and structure_plan is not None,
        )
        return result

    @staticmethod
    def _build_structure_planner(config: ConverterConfig) -> StructurePlanner:
        if not structure_planner_possible(config):
            return NoopStructurePlanner()
        if config.backend == "mock":
            return MockStructurePlanner(model=config.model)
        if config.backend == "openai":
            return OpenAIStructurePlanner(
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
                max_output_tokens=config.max_output_tokens,
            )
        if config.backend == "azure":
            if not config.azure_endpoint:
                raise ValueError("Azure backend requires azure_endpoint.")
            return AzureChatStructurePlanner(
                model=config.model,
                azure_endpoint=config.azure_endpoint,
                api_version=config.azure_api_version,
                api_key=config.api_key,
                max_output_tokens=config.max_output_tokens,
            )
        return NoopStructurePlanner()

    @staticmethod
    def _build_extractor(config: ConverterConfig) -> SectionExtractor:
        if config.backend == "local":
            return LocalSectionExtractor()
        if config.backend == "mock":
            return MockApiSectionExtractor(model=config.model, prompt_profile=config.prompt_profile)
        if config.backend == "openai":
            return OpenAISectionExtractor(
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
                max_output_tokens=config.max_output_tokens,
                prompt_profile=config.prompt_profile,
            )
        if config.backend == "azure":
            if not config.azure_endpoint:
                raise ValueError("Azure backend requires azure_endpoint.")
            return AzureChatSectionExtractor(
                model=config.model,
                azure_endpoint=config.azure_endpoint,
                api_version=config.azure_api_version,
                api_key=config.api_key,
                max_output_tokens=config.max_output_tokens,
                prompt_profile=config.prompt_profile,
            )
        raise ValueError(f"Unknown backend: {config.backend}")

    @staticmethod
    def _build_auditor(config: ConverterConfig) -> SectionAuditRepairer:
        if not audit_enabled(config):
            return NoopSectionAuditRepairer()
        if config.backend == "mock":
            return MockSectionAuditRepairer(model=config.model, prompt_profile=config.prompt_profile)
        if config.backend == "openai":
            return OpenAISectionAuditRepairer(
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
                max_output_tokens=config.max_output_tokens,
                prompt_profile=config.prompt_profile,
            )
        if config.backend == "azure":
            if not config.azure_endpoint:
                raise ValueError("Azure backend requires azure_endpoint.")
            return AzureChatSectionAuditRepairer(
                model=config.model,
                azure_endpoint=config.azure_endpoint,
                api_version=config.azure_api_version,
                api_key=config.api_key,
                max_output_tokens=config.max_output_tokens,
                prompt_profile=config.prompt_profile,
            )
        return NoopSectionAuditRepairer()


def normalize_items(raw_items: list[dict[str, Any]], section: MarkdownSection, global_start: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for local_index, raw in enumerate(raw_items, start=1):
        env = normalize_env(raw.get("env"))
        if env not in ALLOWED_ENVS:
            env = "remark"
        ctx = section.context.as_json()
        label = build_label(env, ctx, local_index)
        item = {
            "index": local_index,
            "label": label,
            "env": env,
            "number_components": normalize_number_components(raw.get("number_components", [])),
            "context": ctx,
            "content": str(raw.get("content", "")).strip(),
            "dependencies": normalize_dependencies(raw.get("dependencies", [])),
            "proof": normalize_proof(raw.get("proof")),
        }
        if item["content"]:
            normalized.append(item)
    return normalized


def audit_enabled(config: ConverterConfig) -> bool:
    mode = (config.audit_mode or "auto").strip().lower()
    if mode == "off":
        return False
    if mode == "llm":
        return config.backend in {"openai", "azure", "mock"}
    if mode == "auto":
        return config.backend in {"openai", "azure", "mock"}
    return False


def structure_planner_possible(config: ConverterConfig) -> bool:
    mode = (config.structure_mode or "auto").strip().lower()
    return mode != "hard" and config.backend in {"openai", "azure", "mock"}


def should_use_structure_planner(config: ConverterConfig, source_text: str, hard_plan: Any) -> bool:
    mode = (config.structure_mode or "auto").strip().lower()
    if mode == "hard":
        return False
    if config.backend not in {"openai", "azure", "mock"}:
        return False
    if mode == "llm":
        return True
    if mode == "auto":
        return needs_structure_planner(source_text, hard_plan)
    return False


def normalize_env(value: Any) -> str:
    raw = str(value or "").strip()
    if raw in ALLOWED_ENVS:
        return raw
    return ENV_ALIASES.get(raw.lower(), "remark")


def build_label(env: str, context: dict[str, str], local_index: int) -> str:
    chapter_number = str(context.get("chapter_number") or "").strip()
    section_number = str(context.get("section_number") or "").strip()
    if chapter_number and section_number:
        number_prefix = f"{chapter_number}-{section_number}"
    else:
        number_prefix = chapter_number or section_number or "section"
    return f"{ENV_DISPLAY[env]} {number_prefix}-{local_index}"


def normalize_number_components(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for part in value:
        text = str(part).strip()
        if not text:
            continue
        output.append(text)
    return output


def normalize_dependencies(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_proof(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def cleanup_trace_dir(trace_dir: Path) -> None:
    if not trace_dir.exists():
        return
    for path in trace_dir.glob("section*.json"):
        path.unlink()


def cleanup_structure_trace_dir(trace_dir: Path) -> None:
    if not trace_dir.exists():
        return
    for name in ["call.json", "request.json", "response.json"]:
        path = trace_dir / name
        if path.exists():
            path.unlink()
