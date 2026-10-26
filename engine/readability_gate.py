"""Language-aware, source-only human-readability assessment.

This gate checks changed source before closure without claiming that code was
written by a human. It reports maintainability findings using only paths,
line numbers, rules, and suggestions; source content never appears in the
assessment result.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "readability-assessment-v1"
STATUS_VALUES = {"PASS", "NEEDS_REVIEW", "BLOCKED"}
SEVERITIES = {"ERROR", "WARNING", "INFO"}
POLICY_KEYS = {
    "maxLineLength",
    "maxFunctionLines",
    "maxComplexity",
    "complexityCommentThreshold",
    "maxNestingDepth",
}
DEFAULT_POLICY = {
    "maxLineLength": 120,
    "maxFunctionLines": 80,
    "maxComplexity": 10,
    "complexityCommentThreshold": 7,
    "maxNestingDepth": 4,
}

LANGUAGE_BY_SUFFIX = {
    ".c": "C", ".cc": "C++", ".cpp": "C++", ".cs": "C#", ".dart": "Dart",
    ".ex": "Elixir", ".exs": "Elixir", ".go": "Go", ".h": "C", ".hpp": "C++",
    ".java": "Java", ".js": "JavaScript", ".jsx": "JavaScript", ".json": "JSON",
    ".kt": "Kotlin", ".m": "Objective-C", ".mm": "Objective-C++", ".php": "PHP",
    ".py": "Python", ".rb": "Ruby", ".rs": "Rust", ".scala": "Scala",
    ".scss": "SCSS", ".sh": "Shell", ".sql": "SQL", ".svelte": "Svelte",
    ".swift": "Swift", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".vue": "Vue", ".xml": "XML", ".yaml": "YAML", ".yml": "YAML",
    ".css": "CSS", ".html": "HTML",
}
COMMENT_PATTERN = re.compile(r"^\s*(?:#|//|/\*|\*|<!--|-->)")
PLACEHOLDER_PATTERN = re.compile(r"\b(?:TODO|FIXME|HACK)\b\s*[:\-]\s*(?:implement|finish|remove|fix|later)\b", re.I)
GENERIC_COMPLEXITY_PATTERN = re.compile(r"\b(?:if|for|while|catch|switch|case|try|with)\b|&&|\|\||\?(?!=)")
PYTHON_CONTROL_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)


class ReadabilityGateError(ValueError):
    """Raised when a readability assessment cannot safely be created."""


@dataclass(frozen=True)
class ReadabilityPolicy:
    max_line_length: int = DEFAULT_POLICY["maxLineLength"]
    max_function_lines: int = DEFAULT_POLICY["maxFunctionLines"]
    max_complexity: int = DEFAULT_POLICY["maxComplexity"]
    complexity_comment_threshold: int = DEFAULT_POLICY["complexityCommentThreshold"]
    max_nesting_depth: int = DEFAULT_POLICY["maxNestingDepth"]

    def __post_init__(self) -> None:
        for name in (
            "max_line_length",
            "max_function_lines",
            "max_complexity",
            "complexity_comment_threshold",
            "max_nesting_depth",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ReadabilityGateError(f"{name} must be a positive integer")
        if self.complexity_comment_threshold > self.max_complexity:
            raise ReadabilityGateError("complexity_comment_threshold cannot exceed max_complexity")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ReadabilityPolicy":
        if value is None:
            return cls()
        unknown = set(value) - POLICY_KEYS
        if unknown:
            raise ReadabilityGateError("readability policy contains unknown fields: " + ", ".join(sorted(map(str, unknown))))
        defaults = DEFAULT_POLICY.copy()
        defaults.update(value)
        return cls(
            max_line_length=defaults["maxLineLength"],
            max_function_lines=defaults["maxFunctionLines"],
            max_complexity=defaults["maxComplexity"],
            complexity_comment_threshold=defaults["complexityCommentThreshold"],
            max_nesting_depth=defaults["maxNestingDepth"],
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "maxLineLength": self.max_line_length,
            "maxFunctionLines": self.max_function_lines,
            "maxComplexity": self.max_complexity,
            "complexityCommentThreshold": self.complexity_comment_threshold,
            "maxNestingDepth": self.max_nesting_depth,
        }


def _safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReadabilityGateError("file path must be a non-empty string")
    path = PurePosixPath(value.strip())
    if path.is_absolute() or ".." in path.parts:
        raise ReadabilityGateError("file path must be repository-relative")
    return path.as_posix()


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise ReadabilityGateError(f"{label} must be non-empty text up to 200 characters")
    return value.strip()


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _language(path: str, explicit: Any) -> tuple[str, bool]:
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise ReadabilityGateError("language must be non-empty text when supplied")
        normalized = explicit.strip()
        return normalized, normalized.casefold() in {item.casefold() for item in LANGUAGE_BY_SUFFIX.values()}
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix in LANGUAGE_BY_SUFFIX:
        return LANGUAGE_BY_SUFFIX[suffix], True
    return "Unknown", False


def _finding(path: str, rule: str, severity: str, line: int, message: str, suggestion: str) -> dict[str, Any]:
    if severity not in SEVERITIES:
        raise ReadabilityGateError("unsupported readability finding severity")
    identity = _fingerprint({"path": path, "rule": rule, "line": line, "message": message})[7:]
    return {
        "findingId": f"readability-{identity[:20]}",
        "path": path,
        "line": max(0, line),
        "rule": rule,
        "severity": severity,
        "message": message,
        "suggestion": suggestion,
    }


def _comment_lines(lines: Sequence[str]) -> set[int]:
    return {index for index, line in enumerate(lines, start=1) if COMMENT_PATTERN.search(line)}


def _python_function_metrics(tree: ast.AST, lines: Sequence[str], policy: ReadabilityPolicy, path: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def complexity(node: ast.AST) -> int:
        value = 1
        for child in ast.walk(node):
            if isinstance(child, PYTHON_CONTROL_NODES):
                value += 1
            elif isinstance(child, ast.BoolOp):
                value += max(0, len(child.values) - 1)
        return value

    def nesting(node: ast.AST, current: int = 0) -> int:
        nested_nodes = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)
        children = [child for child in ast.iter_child_nodes(node)]
        values = [nesting(child, current + 1 if isinstance(node, nested_nodes) else current) for child in children]
        return max([current, *values])

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        line_count = max(1, end - start + 1)
        if line_count > policy.max_function_lines:
            findings.append(_finding(path, "long-function", "WARNING", start, "Function exceeds the configured line limit.", "Split the function around clear responsibilities or reuse an existing helper."))
        function_complexity = complexity(node)
        if function_complexity > policy.max_complexity:
            findings.append(_finding(path, "high-complexity", "WARNING", start, "Function exceeds the configured complexity limit.", "Simplify the control flow, extract a focused helper, or document the business rule."))
        if nesting(node) > policy.max_nesting_depth:
            findings.append(_finding(path, "deep-nesting", "WARNING", start, "Function exceeds the configured nesting-depth limit.", "Use guard clauses, early returns, or a focused abstraction to reduce nesting."))
        function_lines = lines[max(0, start - 1):end]
        has_explanation = bool(_comment_lines(function_lines)) or (
            bool(node.body)
            and isinstance(node.body[0], ast.Expr)
            and isinstance(getattr(node.body[0], "value", None), ast.Constant)
            and isinstance(node.body[0].value.value, str)
        )
        if function_complexity >= policy.complexity_comment_threshold and not has_explanation:
            findings.append(_finding(path, "non-obvious-logic-needs-explanation", "WARNING", start, "Non-obvious control flow has no nearby explanation.", "Add a concise comment or docstring explaining why the rule exists, not what each line does."))
    return findings


def analyze_source_file(file: Mapping[str, Any], *, policy: ReadabilityPolicy | None = None) -> dict[str, Any]:
    """Analyze one changed source file without returning its contents."""

    if not isinstance(file, Mapping):
        raise ReadabilityGateError("changed file must be an object")
    allowed = {"path", "content", "language"}
    unknown = set(file) - allowed
    if unknown:
        raise ReadabilityGateError("changed file contains unknown fields: " + ", ".join(sorted(map(str, unknown))))
    path = _safe_path(file.get("path"))
    content = file.get("content")
    if not isinstance(content, str):
        raise ReadabilityGateError(f"{path}: content must be text")
    if "\x00" in content:
        raise ReadabilityGateError(f"{path}: NUL characters are not allowed")
    selected_policy = policy or ReadabilityPolicy()
    language, supported = _language(path, file.get("language"))
    lines = content.splitlines()
    findings: list[dict[str, Any]] = []

    if not supported:
        findings.append(_finding(path, "language-coverage", "INFO", 0, "Language-specific checks are unavailable for this file.", "Provide a host language adapter or explicitly review the file with the generic checks."))

    for index, line in enumerate(lines, start=1):
        if line.rstrip(" \t") != line:
            findings.append(_finding(path, "trailing-whitespace", "WARNING", index, "Line has trailing whitespace.", "Remove trailing spaces and tabs."))
        if len(line) > selected_policy.max_line_length:
            findings.append(_finding(path, "line-too-long", "WARNING", index, "Line exceeds the configured readability limit.", "Wrap the expression or extract a named value/helper."))
        if PLACEHOLDER_PATTERN.search(line):
            findings.append(_finding(path, "unfinished-placeholder", "WARNING", index, "The changed code contains an implementation placeholder.", "Finish the behavior or record the missing work as an explicit task."))

    if language.casefold() == "python":
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as exc:
            findings.append(_finding(path, "syntax-error", "ERROR", int(exc.lineno or 0), "The Python source does not parse.", "Fix the syntax error before review or closure."))
        else:
            findings.extend(_python_function_metrics(tree, lines, selected_policy, path))
    else:
        complexity = len(GENERIC_COMPLEXITY_PATTERN.findall(content))
        comment_present = bool(_comment_lines(lines))
        if complexity > selected_policy.max_complexity:
            findings.append(_finding(path, "high-complexity", "WARNING", 1, "The file contains a high number of control-flow signals.", "Simplify the flow or ask for a language-specific review."))
        if complexity >= selected_policy.complexity_comment_threshold and not comment_present:
            findings.append(_finding(path, "non-obvious-logic-needs-explanation", "WARNING", 1, "Non-obvious control flow has no detected comment or documentation.", "Add a concise explanation of the business rule or provide a language-specific review."))

    status = "PASS"
    if any(item["severity"] == "ERROR" for item in findings):
        status = "BLOCKED"
    elif findings:
        status = "NEEDS_REVIEW"
    return {
        "path": path,
        "language": language,
        "status": status,
        "findingCount": len(findings),
        "findings": findings,
    }


def build_readability_assessment(
    *,
    task_id: str,
    source_fingerprint: str,
    changed_files: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a source-only readability assessment for changed files."""

    task = _safe_id(task_id, "task_id")
    source = _safe_id(source_fingerprint, "source_fingerprint")
    selected_policy = ReadabilityPolicy.from_mapping(policy)
    if not isinstance(changed_files, Sequence) or isinstance(changed_files, (str, bytes)):
        raise ReadabilityGateError("changed_files must be an array")

    file_results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if not changed_files:
        findings.append(_finding("[assessment]", "missing-changed-files", "ERROR", 0, "No changed source files were supplied for readability review.", "Provide the bounded changed-file set before closure."))
    for file in changed_files:
        try:
            result = analyze_source_file(file, policy=selected_policy)
        except ReadabilityGateError as exc:
            path = str(file.get("path", "[unknown]")) if isinstance(file, Mapping) else "[unknown]"
            safe_path = path if path.startswith("[") else "[invalid-path]"
            result = {
                "path": safe_path,
                "language": "Unknown",
                "status": "BLOCKED",
                "findingCount": 1,
                "findings": [_finding(safe_path, "invalid-input", "ERROR", 0, str(exc), "Provide a valid repository-relative changed-file record.")],
            }
        file_results.append(result)
        findings.extend(result["findings"])

    status = "PASS"
    if any(item["severity"] == "ERROR" for item in findings):
        status = "BLOCKED"
    elif findings:
        status = "NEEDS_REVIEW"
    assessment = {
        "schemaVersion": SCHEMA_VERSION,
        "assessmentId": _fingerprint({"taskId": task, "sourceFingerprint": source, "policy": selected_policy.as_dict(), "files": file_results}),
        "taskId": task,
        "sourceFingerprint": source,
        "policy": selected_policy.as_dict(),
        "files": file_results,
        "findings": findings,
        "status": status,
        "humanAuthorship": "NOT_DETERMINABLE",
        "sourceOnly": True,
        "liveExecution": False,
        "sideEffects": [],
    }
    validation = validate_readability_assessment(assessment)
    if not validation["valid"]:
        raise ReadabilityGateError("generated assessment failed validation: " + "; ".join(validation["errors"]))
    return assessment


def validate_readability_assessment(value: Any) -> dict[str, Any]:
    """Validate the redacted assessment contract."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["readabilityAssessment: expected object"]}
    required = {
        "schemaVersion", "assessmentId", "taskId", "sourceFingerprint", "policy", "files", "findings",
        "status", "humanAuthorship", "sourceOnly", "liveExecution", "sideEffects",
    }
    errors.extend(f"readabilityAssessment: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"readabilityAssessment: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"readabilityAssessment.schemaVersion: expected {SCHEMA_VERSION}")
    for key in ("assessmentId", "taskId", "sourceFingerprint"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"readabilityAssessment.{key}: expected non-empty string")
    if value.get("status") not in STATUS_VALUES:
        errors.append("readabilityAssessment.status: unsupported value")
    if value.get("humanAuthorship") != "NOT_DETERMINABLE":
        errors.append("readabilityAssessment.humanAuthorship: must be NOT_DETERMINABLE")
    if value.get("sourceOnly") is not True:
        errors.append("readabilityAssessment.sourceOnly: must be true")
    if value.get("liveExecution") is not False:
        errors.append("readabilityAssessment.liveExecution: must be false")
    if value.get("sideEffects") != []:
        errors.append("readabilityAssessment.sideEffects: must be empty")
    policy = value.get("policy")
    if not isinstance(policy, Mapping) or set(policy) != POLICY_KEYS:
        errors.append("readabilityAssessment.policy: expected exact policy fields")
    if not isinstance(value.get("files"), list):
        errors.append("readabilityAssessment.files: expected array")
    if not isinstance(value.get("findings"), list):
        errors.append("readabilityAssessment.findings: expected array")
    for index, finding in enumerate(value.get("findings", [])):
        if not isinstance(finding, Mapping):
            errors.append(f"readabilityAssessment.findings[{index}]: expected object")
            continue
        expected = {"findingId", "path", "line", "rule", "severity", "message", "suggestion"}
        errors.extend(f"readabilityAssessment.findings[{index}]: missing field {key}" for key in sorted(expected - set(finding)))
        errors.extend(f"readabilityAssessment.findings[{index}]: unknown field {key}" for key in sorted(set(finding) - expected))
        if finding.get("severity") not in SEVERITIES:
            errors.append(f"readabilityAssessment.findings[{index}].severity: unsupported value")
        if not isinstance(finding.get("line"), int) or isinstance(finding.get("line"), bool) or finding["line"] < 0:
            errors.append(f"readabilityAssessment.findings[{index}].line: expected integer >= 0")
    return {"valid": not errors, "errors": errors}


__all__ = [
    "DEFAULT_POLICY",
    "ReadabilityGateError",
    "ReadabilityPolicy",
    "analyze_source_file",
    "build_readability_assessment",
    "validate_readability_assessment",
]
