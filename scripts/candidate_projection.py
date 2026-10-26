#!/usr/bin/env python3
"""Optional, source-only projection adapter for the teammate candidate.

The adapter is deliberately optional.  Without an explicit candidate project
root it returns CANDIDATE_UNAVAILABLE.  With one, it invokes only the
candidate's documented read-only CLI projections and rejects any output that
claims legacy/runtime execution.  It never installs, activates, creates a
lane, starts an agent, or writes candidate state.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROJECTION_VERSION = "candidate-projection-v1"
AVAILABLE = "AVAILABLE"
CANDIDATE_UNAVAILABLE = "CANDIDATE_UNAVAILABLE"
INVALID = "INVALID"

_TIER_TO_RISK = {"T0": "LOW", "T1": "LOW", "T2": "MEDIUM", "T3": "HIGH"}
_COMPLEXITY_TO_HYBRID = {
    "SMALL": "LIGHTWEIGHT",
    "MEDIUM": "NORMAL",
    "LARGE": "NORMAL",
    "COUPLED": "COUPLED",
}
_RISK_TERMS = {
    "api": "api_contract",
    "auth": "auth_session",
    "authorization": "auth_session",
    "background": "background_native",
    "branch": "cross_module",
    "contract": "api_contract",
    "delete": "destructive",
    "device": "background_native",
    "destructive": "destructive",
    "migration": "migration",
    "native": "background_native",
    "offline": "offline_sync",
    "persistence": "persistence",
    "pii": "pii",
    "production": "production_runtime",
    "queue": "queue_replay",
    "secret": "secrets",
    "sync": "offline_sync",
    "token": "auth_session",
}
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer\s+\S+|"
    r"(?:password|token|secret|cookie|dsn|api[-_ ]?key)\s*[:=]\s*\S+|"
    r"-----BEGIN [^-]+ PRIVATE KEY-----)"
)

Runner = Callable[[Sequence[str]], Mapping[str, Any]]


class CandidateProjectionError(ValueError):
    """Raised when a supplied candidate cannot produce a safe projection."""


def _source_files(root: Path) -> list[Path]:
    selected: list[Path] = []
    for relative in (
        Path("src"),
        Path("schemas"),
        Path("skills") / "autonomous-engineering",
        Path("README.md"),
        Path("AGENTS.md"),
        Path("docs") / "IMPLEMENTATION_STATUS.md",
        Path("pyproject.toml"),
    ):
        path = root / relative
        if path.is_file():
            selected.append(path)
        elif path.is_dir():
            selected.extend(
                item
                for item in path.rglob("*")
                if item.is_file()
                and ".git" not in item.parts
                and "__pycache__" not in item.parts
                and item.suffix not in {".pyc", ".pyo"}
            )
    return sorted(set(selected), key=lambda item: item.relative_to(root).as_posix())


def source_fingerprint(root: Path) -> str:
    """Hash the candidate source model without relying on Git metadata."""

    digest = hashlib.sha256()
    for path in _source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def resolve_candidate_root(value: str | Path) -> Path | None:
    """Accept either the candidate project root or its skill directory."""

    supplied = Path(value).expanduser().resolve(strict=False)
    if (supplied / "src" / "autonomous_engineering").is_dir():
        return supplied
    if (
        supplied.name == "autonomous-engineering"
        and supplied.parent.name == "skills"
        and (supplied.parent.parent / "src" / "autonomous_engineering").is_dir()
    ):
        return supplied.parent.parent
    return None


def _text(request: Mapping[str, Any]) -> str:
    return " ".join(
        str(request.get(key, ""))
        for key in ("rawText", "normalizedObjective", "requestType")
    ).strip()


def _contains_credential_material(value: str) -> bool:
    return bool(_CREDENTIAL_PATTERN.search(value))


def infer_risk_signals(request: Mapping[str, Any]) -> tuple[str, ...]:
    supplied = request.get("riskSignals", ())
    if supplied:
        if not isinstance(supplied, (list, tuple)) or any(
            not isinstance(value, str) or not value.strip() for value in supplied
        ):
            raise CandidateProjectionError("riskSignals must be a list of non-empty strings")
        return tuple(sorted(set(value.strip() for value in supplied)))
    folded = _text(request).casefold()
    return tuple(
        sorted(
            {
                signal
                for term, signal in _RISK_TERMS.items()
                if re.search(rf"\b{re.escape(term)}\b", folded)
            }
        )
    )


def _request_type(request: Mapping[str, Any]) -> str:
    value = str(request.get("requestType", "change")).strip().casefold()
    return value or "change"


def _route_floor(request: Mapping[str, Any]) -> str:
    supplied = str(request.get("candidateRouteFloor", "")).strip().upper()
    if supplied and supplied not in _TIER_TO_RISK:
        raise CandidateProjectionError("candidateRouteFloor is not a supported tier")
    if supplied in _TIER_TO_RISK:
        return supplied
    return "T0" if _request_type(request) in {"question", "review", "research"} else "T1"


def _scope_paths(request: Mapping[str, Any]) -> tuple[str, ...]:
    supplied = request.get("scopePaths", ())
    if supplied:
        if not isinstance(supplied, (list, tuple)) or any(
            not isinstance(value, str) or not value.strip() for value in supplied
        ):
            raise CandidateProjectionError("scopePaths must be a list of non-empty strings")
        return tuple(sorted(set(value.strip().replace("\\", "/") for value in supplied)))
    return ("src/",)


def _task_id(request: Mapping[str, Any]) -> str:
    supplied = str(request.get("taskId", "")).strip()
    if supplied:
        return supplied
    return "candidate-projection-" + hashlib.sha256(_text(request).encode("utf-8")).hexdigest()[:20]


def _repository_identity(request: Mapping[str, Any]) -> str:
    value = str(request.get("repositoryIdentity", "candidate-projection")).strip()
    return value or "candidate-projection"


def _empty_projection(status: str, reason: str, fingerprint: str) -> dict[str, Any]:
    return {
        "schemaVersion": PROJECTION_VERSION,
        "sourceFingerprint": fingerprint,
        "route": None,
        "complexity": "LIGHTWEIGHT" if status != INVALID else "COUPLED",
        "riskTier": "LOW" if status != INVALID else "CRITICAL",
        "riskReasons": [reason],
        "knowledgeScope": "NONE",
        "requiredCapabilities": [],
        "requiredCoverage": [],
        "status": status,
    }


class CandidateProjector:
    """Project candidate decisions while keeping all lifecycle authority local."""

    def __init__(self, root: Path, runner: Runner | None = None) -> None:
        self.root = root
        self._runner = runner
        self._source_fingerprint = source_fingerprint(root)
        self._source_only_checked = False

    def _command(self, arguments: Sequence[str]) -> Mapping[str, Any]:
        if self._runner is not None:
            return self._runner(arguments)
        sandbox = shutil.which("bwrap")
        if sandbox is None:
            raise CandidateProjectionError("candidate projection sandbox unavailable")
        state_root = self.root.parent / ".candidate-projection-state"
        mapped_arguments = tuple(
            "/candidate" if value == str(self.root)
            else "/tmp/candidate-projection-state" if value == str(state_root)
            else value
            for value in arguments
        )
        command = [
            sandbox,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/etc", "/etc",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--dir", "/tmp/home",
            "--dir", "/tmp/candidate-projection-state",
            "--ro-bind", str(self.root), "/candidate",
            "--chdir", "/candidate",
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "PYTHONPATH", "/candidate/src",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "HOME", "/tmp/home",
            "--setenv", "TMPDIR", "/tmp",
            "--setenv", "LANG", "C.UTF-8",
            "--",
            sys.executable,
            "-m",
            "autonomous_engineering",
            *mapped_arguments,
            "--format",
            "json",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                env={"PATH": "/usr/bin:/bin"},
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CandidateProjectionError("candidate projection command could not run") from error
        if completed.returncode != 0:
            if completed.stderr.startswith("bwrap:"):
                raise CandidateProjectionError("candidate projection sandbox unavailable")
            raise CandidateProjectionError("candidate projection command returned a failure")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CandidateProjectionError("candidate projection returned invalid JSON") from error
        if not isinstance(value, dict):
            raise CandidateProjectionError("candidate projection must be a JSON object")
        return value

    def _ensure_source_only(self) -> None:
        if self._source_only_checked:
            return
        state_root = self.root.parent / ".candidate-projection-state"
        status = self._command(
            (
                "status",
                "--source-root",
                str(self.root),
                "--state-root",
                str(state_root),
            )
        )
        if (
            status.get("mode") != "SOURCE_ONLY"
            or
            status.get("active") is not False
            or status.get("installed") is not False
            or status.get("legacyRuntime") != "UNTOUCHED"
        ):
            raise CandidateProjectionError("candidate is not source-only")
        self._source_only_checked = True

    def project(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise CandidateProjectionError("request must be an object")
        prompt = _text(request)
        if not prompt:
            raise CandidateProjectionError("request has no projection prompt")
        if _contains_credential_material(prompt):
            raise CandidateProjectionError("candidate projection prompt contains credential material")
        self._ensure_source_only()
        task_id = _task_id(request)
        risk_signals = infer_risk_signals(request)

        classify = self._command(("classify", "--prompt", prompt))
        if classify.get("invokesLegacyRuntime") is not False:
            raise CandidateProjectionError("candidate classification claims legacy execution")

        route = str(classify.get("route", ""))
        capabilities: set[str] = set()
        coverage: set[str] = set()
        if route in {"DIRECT", "BLOCKED", "DIRECT_WORK"}:
            # The candidate intentionally has no requirement packet for these
            # routes, so do not manufacture one just to obtain a complexity
            # value.  This preserves its source-only route semantics.
            complexity = "NORMAL" if route == "DIRECT_WORK" else "LIGHTWEIGHT"
            selection_reasons: set[str] = {"candidate route has no diagnosis packet"}
        else:
            diagnosis_arguments: list[str] = [
                "diagnosis-dispatch-plan",
                "--task-id",
                task_id,
                "--repository-identity",
                _repository_identity(request),
                "--prompt",
                prompt,
            ]
            for path in _scope_paths(request):
                diagnosis_arguments.extend(("--scope-path", path))
            for signal in risk_signals:
                diagnosis_arguments.extend(("--risk-signal", signal))
            project_capabilities = request.get("projectCapabilities", ()) or ()
            if isinstance(project_capabilities, (str, bytes)) or not isinstance(
                project_capabilities, (list, tuple)
            ):
                raise CandidateProjectionError("projectCapabilities must be a list of strings")
            for capability in project_capabilities:
                if not isinstance(capability, str) or not capability.strip():
                    raise CandidateProjectionError("projectCapabilities must contain strings")
                diagnosis_arguments.extend(("--project-capability", capability.strip()))
            diagnosis = self._command(tuple(diagnosis_arguments))
            if diagnosis.get("mode") != "SOURCE_ONLY" or diagnosis.get("executed") is not False:
                raise CandidateProjectionError("candidate diagnosis projection is not source-only")
            selection = diagnosis.get("selection")
            dispatch_plan = diagnosis.get("dispatchPlan")
            if not isinstance(selection, Mapping) or not isinstance(dispatch_plan, Mapping):
                raise CandidateProjectionError("candidate diagnosis projection is incomplete")
            complexity = _COMPLEXITY_TO_HYBRID.get(str(selection.get("complexity", "")))
            if complexity is None:
                raise CandidateProjectionError("candidate complexity projection is invalid")
            selection_reasons = set(str(value) for value in selection.get("reasons", ()))
            for wave in dispatch_plan.get("waves", ()):
                if not isinstance(wave, Mapping):
                    continue
                for packet in wave.get("packets", ()):
                    if not isinstance(packet, Mapping):
                        continue
                    capabilities.update(str(value) for value in packet.get("requiredCapabilities", ()))
                    coverage.update(str(value) for value in packet.get("requiredEvidence", ()))

        tier_arguments: list[str] = [
            "explain-tier",
            "--task-id",
            task_id,
            "--route-floor",
            _route_floor(request),
            "--policy-floor",
            str(request.get("candidatePolicyFloor", "T0")).upper(),
        ]
        for signal in risk_signals:
            tier_arguments.extend(("--risk-signal", signal))
        tier = self._command(tuple(tier_arguments))
        tier_value = str(tier.get("tier", ""))
        if tier_value not in _TIER_TO_RISK:
            raise CandidateProjectionError("candidate tier projection is invalid")

        knowledge_scope = str(classify.get("knowledgeScope", "NONE"))
        if knowledge_scope not in {"NONE", "WORKFLOW_ONLY", "TARGETED_REPOSITORY", "CROSS_REPOSITORY", "EXTERNAL"}:
            raise CandidateProjectionError("candidate knowledge scope projection is invalid")

        reasons = set(selection_reasons)
        reasons.update(str(value) for value in tier.get("reasons", ()))
        reasons.update(f"risk_signal:{signal}" for signal in risk_signals)
        return {
            "schemaVersion": PROJECTION_VERSION,
            "sourceFingerprint": self._source_fingerprint,
            "route": classify.get("route"),
            "complexity": complexity,
            "riskTier": _TIER_TO_RISK[tier_value],
            "riskReasons": sorted(reasons),
            "knowledgeScope": knowledge_scope,
            "requiredCapabilities": sorted(capabilities),
            "requiredCoverage": sorted(coverage),
            "status": AVAILABLE,
        }


def project_optional(
    candidate_root: str | Path | None,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an explicit unavailable/invalid projection instead of guessing."""

    if candidate_root is None:
        return _empty_projection(
            CANDIDATE_UNAVAILABLE,
            "candidate source was not supplied",
            "unavailable",
        )
    root = resolve_candidate_root(candidate_root)
    if root is None:
        return _empty_projection(
            INVALID,
            "supplied candidate source root is invalid",
            "invalid",
        )
    try:
        return CandidateProjector(root).project(request)
    except (CandidateProjectionError, OSError, ValueError) as error:
        return _empty_projection(INVALID, str(error), source_fingerprint(root))
