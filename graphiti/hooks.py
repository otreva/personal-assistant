"""Pre-ingestion episode transformation hooks (redaction & summarisation)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from typing import Any, Mapping, MutableMapping, Sequence

from .config import GraphitiConfig
from .episodes import Episode


@dataclass(slots=True)
class RedactionRule:
    """Compiled regex rule for replacing sensitive values."""

    pattern: re.Pattern[str]
    replacement: str
    name: str

    @classmethod
    def from_pattern(
        cls, pattern: str, replacement: str, *, name: str | None = None
    ) -> "RedactionRule":
        compiled = re.compile(pattern)
        return cls(compiled, replacement, name or pattern)

    def apply(self, value: str) -> tuple[str, int]:
        """Apply the rule to *value* returning the redacted text and match count."""

        updated, count = self.pattern.subn(self.replacement, value)
        return updated, count


class RedactionPipeline:
    """Apply a list of redaction rules across nested payloads."""

    def __init__(self, rules: Sequence[RedactionRule] | None = None) -> None:
        self._rules: tuple[RedactionRule, ...] = tuple(rules or ())

    def enabled(self) -> bool:
        return bool(self._rules)

    def apply_text(self, value: str | None) -> tuple[str | None, MutableMapping[str, int]]:
        if value is None:
            return None, {}
        total: dict[str, int] = {}
        redacted = value
        for rule in self._rules:
            redacted, count = rule.apply(redacted)
            if count:
                total[rule.name] = total.get(rule.name, 0) + count
        return redacted, total

    def apply_structure(
        self, payload: Any
    ) -> tuple[Any, MutableMapping[str, int]]:
        stats: dict[str, int] = {}

        def _apply(value: Any) -> Any:
            nonlocal stats
            if isinstance(value, str):
                updated, counts = self.apply_text(value)
                for key, count in counts.items():
                    stats[key] = stats.get(key, 0) + count
                return updated
            if isinstance(value, Mapping):
                return {key: _apply(val) for key, val in value.items()}
            if isinstance(value, list):
                return [_apply(item) for item in value]
            if isinstance(value, tuple):  # preserve tuple semantics
                return tuple(_apply(item) for item in value)
            if isinstance(value, set):
                return {_apply(item) for item in value}
            return value

        return _apply(payload), stats


@dataclass(frozen=True)
class SummarizationResult:
    summary: str
    original_length: int
    summary_length: int
    sentences_used: int


class HeuristicSummarizer:
    """Simple length-based summariser that keeps the leading sentences."""

    def __init__(
        self,
        *,
        threshold: int,
        max_chars: int,
        sentence_count: int,
    ) -> None:
        self._threshold = max(0, threshold)
        self._max_chars = max(1, max_chars)
        self._sentence_count = max(1, sentence_count)

    def summarise(self, text: str | None) -> SummarizationResult | None:
        if text is None:
            return None
        original_length = len(text)
        normalised = text.strip()
        if original_length <= self._threshold or not normalised:
            return None
        sentences = _split_sentences(normalised)
        chosen = sentences[: self._sentence_count]
        if not chosen:
            chosen = [normalised[: self._max_chars]]
        summary = " ".join(chosen).strip()
        if len(summary) > self._max_chars:
            summary = _truncate(summary, self._max_chars)
        return SummarizationResult(
            summary=summary,
            original_length=original_length,
            summary_length=len(summary),
            sentences_used=len(chosen),
        )


def _split_sentences(text: str) -> list[str]:
    pattern = re.compile(r"(?<=[.!?])\s+")
    parts = pattern.split(text)
    return [part.strip() for part in parts if part.strip()]


def _truncate(text: str, limit: int) -> str:
    truncated = text[: limit - 1].rstrip()
    if not truncated:
        return text[:limit]
    if len(truncated) < len(text):
        return truncated + "\u2026"
    return truncated


class EpisodeProcessor:
    """Composite transformation pipeline for incoming episodes."""

    def __init__(self, config: GraphitiConfig) -> None:
        self._config = config
        self._redactor = self._build_redactor(config)
        self._summariser = self._build_summariser(config)

    def process(self, episode: Episode) -> Episode:
        metadata: MutableMapping[str, Any] = dict(episode.metadata)
        processing_meta: MutableMapping[str, Any] = dict(
            metadata.get("graphiti_processing", {})
            if isinstance(metadata.get("graphiti_processing"), Mapping)
            else {}
        )

        text = episode.text
        json_payload = dict(episode.json) if isinstance(episode.json, Mapping) else episode.json

        if self._redactor and self._redactor.enabled():
            text, text_counts = self._redactor.apply_text(text)
            json_payload, json_counts = self._redactor.apply_structure(json_payload)
            metadata, meta_counts = self._redactor.apply_structure(metadata)
            aggregated = _merge_counts(text_counts, json_counts, meta_counts)
            if aggregated:
                processing_meta["redactions"] = {
                    "rules": dict(aggregated),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

        if self._summariser:
            result = self._summariser.summarise(text)
            if result is not None:
                text = result.summary
                processing_meta["summarisation"] = {
                    "strategy": self._config.summarization_strategy,
                    "original_length": result.original_length,
                    "summary_length": result.summary_length,
                    "sentences_used": result.sentences_used,
                }

        if processing_meta:
            metadata["graphiti_processing"] = processing_meta

        return replace(episode, text=text, json=json_payload, metadata=dict(metadata))

    def _build_redactor(self, config: GraphitiConfig) -> RedactionPipeline | None:
        rules: list[RedactionRule] = []
        for pattern, replacement in config.redaction_rules:
            try:
                rules.append(
                    RedactionRule.from_pattern(
                        pattern,
                        replacement or "[REDACTED]",
                        name=pattern,
                    )
                )
            except re.error:
                continue
        if config.redaction_rules_path:
            rules.extend(_load_rules_from_path(config.redaction_rules_path))
        if not rules:
            return None
        return RedactionPipeline(rules)

    def _build_summariser(self, config: GraphitiConfig) -> HeuristicSummarizer | None:
        if config.summarization_strategy.lower() not in {"heuristic", "auto"}:
            return None
        if config.summarization_threshold <= 0:
            return None
        return HeuristicSummarizer(
            threshold=config.summarization_threshold,
            max_chars=config.summarization_max_chars,
            sentence_count=config.summarization_sentence_count,
        )


def _load_rules_from_path(path: str) -> list[RedactionRule]:
    rules: list[RedactionRule] = []
    try:
        from pathlib import Path

        raw = Path(path)
        if not raw.exists():
            return []
        content = raw.read_text(encoding="utf-8")
        data = _parse_rule_document(content)
        for entry in data:
            try:
                pattern = entry["pattern"]
                replacement = entry.get("replacement", "[REDACTED]")
                name = entry.get("name")
                if pattern:
                    rules.append(
                        RedactionRule.from_pattern(pattern, replacement, name=name)
                    )
            except (KeyError, TypeError, re.error):
                continue
    except Exception:  # pragma: no cover - defensive filesystem handling
        return []
    return rules


def _parse_rule_document(content: str) -> list[Mapping[str, str]]:
    try:
        import json

        data = json.loads(content)
    except Exception:
        data = _parse_simple_yaml(content)
    if isinstance(data, list):
        result: list[Mapping[str, str]] = []
        for entry in data:
            if isinstance(entry, Mapping) and "pattern" in entry:
                result.append(entry)  # type: ignore[arg-type]
        return result
    return []


def _parse_simple_yaml(content: str) -> list[Mapping[str, str]]:
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            if current:
                items.append(current)
            current = {}
            line = line[1:].strip()
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if current is None:
                current = {}
            current[key] = value
    if current:
        items.append(current)
    return items


def _merge_counts(*counters: Mapping[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for counter in counters:
        for key, value in counter.items():
            merged[key] = merged.get(key, 0) + int(value)
    return merged


__all__ = [
    "EpisodeProcessor",
    "HeuristicSummarizer",
    "RedactionPipeline",
    "RedactionRule",
    "SummarizationResult",
]

