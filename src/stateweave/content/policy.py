"""Small, runtime-neutral content inspection protocol and safe baseline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|password|passwd|secret|access[_-]?token|bearer)"
    r"\s*(?::|=|\bis\b)\s*['\"]?[A-Za-z0-9_./+=-]{8,}"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
PROMPT_INJECTION = re.compile(
    r"(?i)(?:ignore|disregard|override)\s+(?:all\s+)?"
    r"(?:previous|prior|system|developer)"
    r"\s+(?:instructions?|messages?)|(?:reveal|print)\s+(?:the\s+)?system\s+prompt"
)


@dataclass(frozen=True)
class ContentFinding:
    code: str
    severity: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }


class ContentInspector(Protocol):
    """Project-selectable inspection boundary; implementations perform no effects."""

    def inspect(
        self,
        payload: Any,
        *,
        phase: str,
    ) -> Iterable[ContentFinding]: ...


@dataclass(frozen=True)
class BaselineContentInspector:
    """Detect obvious secrets and instruction-shaped untrusted content."""

    max_nodes: int = 10000
    max_text_characters: int = 1_000_000

    def inspect(self, payload: Any, *, phase: str) -> Iterable[ContentFinding]:
        del phase
        findings: list[ContentFinding] = []
        pending: list[tuple[str, Any]] = [("$", payload)]
        node_count = 0
        text_characters = 0
        while pending:
            path, value = pending.pop()
            node_count += 1
            if node_count > self.max_nodes:
                findings.append(
                    ContentFinding(
                        "inspection_limit",
                        "block",
                        "$",
                        "content exceeds the bounded inspection node limit",
                    )
                )
                break
            if isinstance(value, dict):
                for key in sorted(value, reverse=True, key=str):
                    pending.append((f"{path}.{key}", value[key]))
                continue
            if isinstance(value, list):
                for index in range(len(value) - 1, -1, -1):
                    pending.append((f"{path}[{index}]", value[index]))
                continue
            if not isinstance(value, str):
                continue
            text_characters += len(value)
            if text_characters > self.max_text_characters:
                findings.append(
                    ContentFinding(
                        "inspection_limit",
                        "block",
                        "$",
                        "content exceeds the bounded inspection text limit",
                    )
                )
                break
            if PRIVATE_KEY.search(value) or SECRET_ASSIGNMENT.search(value):
                findings.append(
                    ContentFinding(
                        "possible_secret",
                        "block",
                        path,
                        "content resembles credential material; value was not echoed",
                    )
                )
            if PROMPT_INJECTION.search(value):
                findings.append(
                    ContentFinding(
                        "instruction_shaped_content",
                        "warning",
                        path,
                        "content resembles an instruction to override host authority",
                    )
                )
        unique = {
            (item.code, item.severity, item.path, item.message): item
            for item in findings
        }
        return tuple(unique[key] for key in sorted(unique))


def inspect_content(
    payload: Any,
    *,
    phase: str,
    inspector: ContentInspector | None = None,
) -> tuple[ContentFinding, ...]:
    """Run the supplied policy hook, or the conservative built-in baseline."""

    selected = inspector or BaselineContentInspector()
    findings = tuple(selected.inspect(payload, phase=phase))
    for finding in findings:
        if finding.severity not in {"warning", "block"}:
            raise ValueError(
                f"content inspector returned unsupported severity {finding.severity!r}"
            )
    return tuple(
        sorted(
            findings,
            key=lambda item: (item.severity, item.code, item.path, item.message),
        )
    )
