#!/usr/bin/env python3
"""Run a side-effect-free general route/risk projection shadow report."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

from candidate_projection import project_optional


ROUTES = {
    "question": "DIRECT",
    "review": "REVIEW_ONLY",
    "research": "PLAN_ONLY",
    "operations": "DIRECT_WORK",
    "bug": "NEW_TASK",
    "change": "NEW_TASK",
}

HIGH_RISK_TERMS = {
    "auth": "authorization/authentication",
    "permission": "authorization/authentication",
    "tenant": "tenant isolation",
    "secret": "secret handling",
    "token": "token handling",
    "migration": "migration/schema",
    "delete": "data deletion",
    "schema": "schema/integrity",
    "offline": "offline behavior",
    "queue": "queue/replay",
    "sync": "synchronization",
    "device": "device lifecycle",
    "deployment": "deployment/infrastructure",
    "production": "production configuration",
    "security": "security-sensitive behavior",
}


def _text(request: dict[str, Any]) -> str:
    pieces = [
        str(request.get("rawText", "")),
        str(request.get("normalizedObjective", "")),
        str(request.get("requestType", "")),
    ]
    return " ".join(pieces).lower()


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|token|secret|cookie|dsn|api[-_ ]?key)\s*[:=]\s*\S+"
)
_BEARER_VALUE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+")


def _safe_text(value: Any) -> str:
    text = " ".join(str(value).split())
    text = _BEARER_VALUE.sub("authorization: Bearer [REDACTED]", text)
    text = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", text
    )
    return text[:500]


def _safe_request(request: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in request.items():
        if str(key).startswith("_"):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = _safe_text(value) if isinstance(value, str) else value
        elif isinstance(value, (list, tuple)):
            safe[key] = [
                _safe_text(item) if isinstance(item, str) else item
                for item in value
            ]
    return safe


def _request_values(request: Mapping[str, Any], key: str) -> list[str]:
    value = request.get(key, ())
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


_COMPLEXITY_RANK = {"LIGHTWEIGHT": 0, "NORMAL": 1, "COUPLED": 2}
_RISK_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def project_general(request: dict[str, Any]) -> dict[str, Any]:
    text = _text(request)
    request_type = str(request.get("requestType", "")).lower()
    route = ROUTES.get(request_type)
    if route is None:
        if any(word in text for word in ("what is", "explain", "where is", "how does")):
            route = "DIRECT"
        elif any(word in text for word in ("review", "audit")):
            route = "REVIEW_ONLY"
        elif any(word in text for word in ("plan", "design")):
            route = "PLAN_ONLY"
        else:
            route = "NEW_TASK"

    reasons = sorted({
        reason
        for term, reason in HIGH_RISK_TERMS.items()
        if re.search(rf"\b{re.escape(term)}\b", text)
    })
    coupled_terms = {
        "migration", "offline", "queue", "sync", "device", "deployment",
        "production", "tenant", "schema",
    }
    coupled = any(term in text for term in coupled_terms)
    if coupled:
        complexity = "COUPLED"
    elif route in {"NEW_TASK", "REVIEW_ONLY", "DIRECT_WORK"}:
        complexity = "NORMAL"
    else:
        complexity = "LIGHTWEIGHT"

    high_risk = any(reason in reasons for reason in (
        "authorization/authentication",
        "tenant isolation",
        "secret handling",
        "token handling",
        "security-sensitive behavior",
        "data deletion",
        "migration/schema",
        "production configuration",
    )) or (
        {"offline behavior", "queue/replay"}.issubset(reasons)
        or {"synchronization", "queue/replay"}.issubset(reasons)
        or "device lifecycle" in reasons
    )
    if high_risk:
        risk = "HIGH"
    elif reasons:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    return {
        "authority": "buildcrew",
        "route": route,
        "complexity": complexity,
        "riskTier": risk,
        "riskReasons": reasons,
        "requiredCapabilities": _request_values(request, "requiredCapabilities"),
        "requiredCoverage": _request_values(request, "requiredCoverage"),
        "candidateStatus": "CANDIDATE_UNAVAILABLE",
        "sideEffects": [],
    }


def compare_projection(general: dict[str, Any], candidate: dict[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {
            "status": "CANDIDATE_UNAVAILABLE",
            "differences": [],
            "candidate": None,
        }
    candidate_status = candidate.get("status", "AVAILABLE")
    if candidate_status in {"CANDIDATE_UNAVAILABLE", "INVALID"}:
        return {
            "status": candidate_status,
            "differences": [],
            "candidate": candidate,
        }

    differences = []
    for field in ("route", "complexity", "riskTier"):
        if candidate.get(field) != general.get(field):
            differences.append({
                "field": field,
                "general": general.get(field),
                "candidate": candidate.get(field),
            })
    return {
        "status": "COMPARED",
        "differences": differences,
        "candidate": candidate,
    }


def reconcile_projection(
    general: Mapping[str, Any], candidate: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Keep general authoritative while admitting only monotonic candidate floors."""

    final = {
        "route": general.get("route"),
        "complexity": general.get("complexity"),
        "riskTier": general.get("riskTier"),
        "riskReasons": sorted(set(general.get("riskReasons", ()))),
        "requiredCapabilities": sorted(set(general.get("requiredCapabilities", ()))),
        "requiredCoverage": sorted(set(general.get("requiredCoverage", ()))),
    }
    conflicts: list[dict[str, Any]] = []
    status = "CANDIDATE_UNAVAILABLE"
    if candidate is not None:
        status = str(candidate.get("status", "AVAILABLE"))
    if status == "AVAILABLE" and candidate is not None:
        route_conflict = candidate.get("route") != final["route"]
        if route_conflict:
            conflicts.append({
                "field": "route",
                "authority": "general",
                "general": final["route"],
                "candidate": candidate.get("route"),
            })
        candidate_complexity = str(candidate.get("complexity", "LIGHTWEIGHT"))
        if _COMPLEXITY_RANK.get(candidate_complexity, -1) > _COMPLEXITY_RANK[final["complexity"]]:
            final["complexity"] = candidate_complexity
        candidate_risk = str(candidate.get("riskTier", "LOW"))
        if _RISK_RANK.get(candidate_risk, -1) > _RISK_RANK[final["riskTier"]]:
            final["riskTier"] = candidate_risk
        final["riskReasons"] = sorted(
            set(final["riskReasons"]) | set(candidate.get("riskReasons", ()))
        )
        final["requiredCapabilities"] = sorted(
            set(final["requiredCapabilities"]) | set(candidate.get("requiredCapabilities", ()))
        )
        final["requiredCoverage"] = sorted(
            set(final["requiredCoverage"]) | set(candidate.get("requiredCoverage", ()))
        )
        if route_conflict:
            status = "CONFLICT"
    elif status == "INVALID":
        conflicts.append({
            "field": "candidateProjection",
            "authority": "general",
            "reason": "invalid candidate projection is ignored and blocks hybrid coverage",
        })
    return {
        "status": status,
        "conflicts": conflicts,
        "final": final,
    }


def check_expected(final: Mapping[str, Any], expected: Mapping[str, Any] | None) -> dict[str, Any]:
    if expected is None:
        return {"checked": False, "passed": True, "mismatches": []}
    mismatches = []
    for field in ("route", "complexity", "riskTier"):
        if field in expected and expected[field] != final.get(field):
            mismatches.append({
                "field": field,
                "expected": expected[field],
                "observed": final.get(field),
            })
    return {"checked": True, "passed": not mismatches, "mismatches": mismatches}


def load_cases(path: Path) -> list[dict[str, Any]]:
    files = [path] if path.is_file() else sorted(path.rglob("*.json"))
    cases = []
    for file in files:
        value = json.loads(file.read_text(encoding="utf-8"))
        value["_sourceFile"] = file.name
        cases.append(value)
    return cases


def build_report(
    cases: list[dict[str, Any]], candidate_root: Path | None = None
) -> dict[str, Any]:
    results = []
    for case in cases:
        request = case.get("request", case)
        general = project_general(request)
        candidate = (
            project_optional(candidate_root, request)
            if candidate_root is not None
            else case.get("candidateProjection")
        )
        if candidate is not None:
            general = dict(general)
            general["candidateStatus"] = candidate.get("status", "AVAILABLE")
        comparison = compare_projection(general, candidate)
        reconciliation = reconcile_projection(general, candidate)
        results.append({
            "name": case.get("name", case.get("_sourceFile", "unnamed")),
            "request": _safe_request(request),
            "general": general,
            "comparison": comparison,
            "reconciliation": reconciliation,
            "expectation": check_expected(
                reconciliation["final"], case.get("expected")
            ),
        })
    return {
        "authority": "buildcrew",
        "sideEffects": [],
        "candidateSource": "configured" if candidate_root is not None else "optional",
        "caseCount": len(results),
        "expectationsPassed": all(item["expectation"]["passed"] for item in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--candidate-root",
        type=Path,
        help="Optional teammate candidate project root or its skills/autonomous-engineering directory",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_report(load_cases(args.corpus), candidate_root=args.candidate_root)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
