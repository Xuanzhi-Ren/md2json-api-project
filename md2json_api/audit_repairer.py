from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .models import MarkdownSection
from .prompts import build_audit_repair_prompt, build_audit_repair_system_prompt
from .schema import (
    chat_audit_repair_json_schema_response_format,
    responses_audit_repair_json_schema_format,
)


class NoopSectionAuditRepairer:
    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return _noop_payload(section, current_items)


class MockSectionAuditRepairer(NoopSectionAuditRepairer):
    def __init__(self, *, model: str = "mock-api-worker", trace_dir: Path | None = None, prompt_profile: str = "auto") -> None:
        self.model = model
        self.trace_dir = trace_dir
        self.prompt_profile = prompt_profile

    def set_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir

    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_audit_repair_system_prompt(self.prompt_profile, section)},
                {"role": "user", "content": build_audit_repair_prompt(section, current_items, self.prompt_profile)},
            ],
            "response_format": chat_audit_repair_json_schema_response_format(),
        }
        response_payload = _noop_payload(section, current_items)
        self._write_trace(section, request_payload, json.dumps(response_payload, ensure_ascii=False), response_payload)
        return response_payload

    def _write_trace(
        self,
        section: MarkdownSection,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(
            self.trace_dir,
            section,
            provider_shape="mock_azure_chat_completions",
            request_payload=request_payload,
            response_text=response_text,
            response_payload=response_payload,
        )


class OpenAISectionAuditRepairer:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        max_output_tokens: int | None = None,
        trace_dir: Path | None = None,
        prompt_profile: str = "auto",
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_output_tokens = max_output_tokens
        self.trace_dir = trace_dir
        self.prompt_profile = prompt_profile
        self._client = None

    def set_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "The openai package is not installed. Run: python3 -m pip install -r requirements.txt"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            kwargs["timeout"] = 180
            self._client = OpenAI(**kwargs)
        return self._client

    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cached = _read_cached_audit_response(self.trace_dir, section)
        if cached is not None:
            return cached
        request: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": build_audit_repair_system_prompt(self.prompt_profile, section)},
                {"role": "user", "content": build_audit_repair_prompt(section, current_items, self.prompt_profile)},
            ],
            "text": {"format": responses_audit_repair_json_schema_format()},
        }
        if self.max_output_tokens:
            request["max_output_tokens"] = self.max_output_tokens

        response = _with_retries(lambda: self.client.responses.create(**request))
        output_text = getattr(response, "output_text", None) or _collect_response_text(response)
        payload = _parse_audit_payload(output_text, provider="OpenAI")
        self._write_trace(section, request, output_text, payload, usage=_response_usage(response))
        return payload

    def _write_trace(
        self,
        section: MarkdownSection,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(
            self.trace_dir,
            section,
            provider_shape="openai_responses",
            request_payload=request_payload,
            response_text=response_text,
            response_payload=response_payload,
            usage=usage,
        )


class AzureChatSectionAuditRepairer:
    def __init__(
        self,
        *,
        model: str,
        azure_endpoint: str,
        api_version: str,
        api_key: str | None = None,
        max_output_tokens: int | None = None,
        trace_dir: Path | None = None,
        prompt_profile: str = "auto",
    ) -> None:
        self.model = model
        self.azure_endpoint = azure_endpoint
        self.api_version = api_version
        self.api_key = api_key
        self.max_output_tokens = max_output_tokens
        self.trace_dir = trace_dir
        self.prompt_profile = prompt_profile
        self._client = None

    def set_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import AzureOpenAI
            except ModuleNotFoundError:
                self._client = False
                return self._client
            kwargs: dict[str, Any] = {
                "azure_endpoint": self.azure_endpoint,
                "api_version": self.api_version,
                "timeout": 180,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            self._client = AzureOpenAI(**kwargs)
        return self._client

    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cached = _read_cached_audit_response(self.trace_dir, section)
        if cached is not None:
            return cached
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_audit_repair_system_prompt(self.prompt_profile, section)},
                {"role": "user", "content": build_audit_repair_prompt(section, current_items, self.prompt_profile)},
            ],
            "response_format": chat_audit_repair_json_schema_response_format(),
        }
        if self.max_output_tokens:
            request["max_tokens"] = self.max_output_tokens

        usage: dict[str, Any] | None = None
        if self.client is False:
            output_text = _with_retries(lambda: self._audit_repair_via_rest(request))
        else:
            response = _with_retries(lambda: self.client.chat.completions.create(**request))
            output_text = response.choices[0].message.content or ""
            usage = _response_usage(response)
        payload = _parse_audit_payload(output_text, provider="Azure OpenAI")
        self._write_trace(section, request, output_text, payload, usage=usage)
        return payload

    def _audit_repair_via_rest(self, request_payload: dict[str, Any]) -> str:
        if not self.api_key:
            raise RuntimeError("Azure REST fallback requires an API key.")
        endpoint = self.azure_endpoint.rstrip("/")
        deployment = urllib.parse.quote(self.model, safe="")
        api_version = urllib.parse.quote(self.api_version, safe="")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        body = json.dumps(request_payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Azure OpenAI audit HTTP {exc.code}: {error_body[:1000]}") from exc
        return payload["choices"][0]["message"].get("content") or ""

    def _write_trace(
        self,
        section: MarkdownSection,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(
            self.trace_dir,
            section,
            provider_shape="azure_chat_completions",
            request_payload=request_payload,
            response_text=response_text,
            response_payload=response_payload,
            usage=usage,
        )


def _noop_payload(section: MarkdownSection, current_items: list[dict[str, Any]]) -> dict[str, Any]:
    section_id = f"section{section.index:02d}_{section.context.section_number}"
    return {
        "audit_markdown": (
            f"Section {section_id}: {section.context.section}\n\n"
            "Short verdict: no change\n\n"
            f"Current JSON summary: {len(current_items)} item(s).\n\n"
            "Findings: mock audit did not perform semantic review.\n\n"
            "Compact action summary: no actions."
        ),
        "patch_candidate": {
            "section_id": section_id,
            "overall_assessment": "no change",
            "actions": [],
            "open_questions": [],
        },
        "repaired_items": current_items,
    }


def _parse_audit_payload(output_text: str, *, provider: str) -> dict[str, Any]:
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider} audit response was not valid JSON: {output_text[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{provider} audit response was not an object: {payload!r}")
    if not isinstance(payload.get("audit_markdown"), str):
        raise RuntimeError(f"{provider} audit response missing audit_markdown.")
    patch = payload.get("patch_candidate")
    if not isinstance(patch, dict) or not isinstance(patch.get("actions"), list):
        raise RuntimeError(f"{provider} audit response missing patch_candidate actions.")
    if not isinstance(payload.get("repaired_items"), list):
        raise RuntimeError(f"{provider} audit response missing repaired_items.")
    return payload


def _read_cached_audit_response(trace_dir: Path | None, section: MarkdownSection) -> dict[str, Any] | None:
    if trace_dir is None:
        return None
    path = trace_dir / f"section{section.index:02d}_response.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        return _parse_audit_payload(json.dumps(payload, ensure_ascii=False), provider="cached audit")
    except RuntimeError:
        return None


def _write_trace(
    trace_dir: Path,
    section: MarkdownSection,
    *,
    provider_shape: str,
    request_payload: dict[str, Any],
    response_text: str,
    response_payload: dict[str, Any],
    usage: dict[str, Any] | None = None,
) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    stem = f"section{section.index:02d}"
    combined = {
        "section_index": section.index,
        "context": section.context.as_json(),
        "provider_shape": provider_shape,
        "request": request_payload,
        "response_text": response_text,
        "response_json": response_payload,
        "usage": usage,
    }
    (trace_dir / f"{stem}.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (trace_dir / f"{stem}_request.json").write_text(
        json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (trace_dir / f"{stem}_response.json").write_text(
        json.dumps(response_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _collect_response_text(response: Any) -> str:
    pieces: list[str] = []
    for output in getattr(response, "output", []) or []:
        for content in getattr(output, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                pieces.append(text)
    return "".join(pieces)


def _with_retries(fn, *, attempts: int = 3, delay: float = 5.0):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                raise
            time.sleep(delay * attempt)
    raise last_exc


def _response_usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    output: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            output[key] = value
    return output or None
