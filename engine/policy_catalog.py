#!/usr/bin/env python3
"""Versioned, explicit policy rules for project-context resolution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

POLICY_VERSION = "policy-bundle-v1"


def _rule(
    rule_id: str,
    scope: str,
    title: str,
    requirement: str,
    enforcement: str,
    source: str,
    source_ref: str,
    *,
    applies_to: Iterable[str] = ("**",),
    risk_signals: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    evidence: Iterable[str] = (),
    conflicts_with: Iterable[str] = (),
    condition: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "ruleId": rule_id,
        "scope": scope,
        "title": title,
        "requirement": requirement,
        "enforcement": enforcement,
        "source": source,
        "sourceRef": source_ref,
        "appliesTo": list(applies_to),
        "riskSignals": list(risk_signals),
        "requiredCapabilities": list(capabilities),
        "requiredEvidence": list(evidence),
        "conflictsWith": list(conflicts_with),
    }
    if condition:
        value["condition"] = condition
    return value


COMMON_RULES = [
    _rule("V3-AUTH-001", "COMMON", "Single lifecycle authority", "Exactly one lifecycle controller owns intake, routing, mutation coordination, closure, and activation.", "BLOCK", "v3_builtin", "catalog:v3/common", risk_signals=("authority",), capabilities=("authority binding",), evidence=("controller status",)),
    _rule("V3-AUTH-002", "COMMON", "Inert installation", "Installing or discovering a skill must not activate, retire, migrate, or redirect another controller.", "BLOCK", "v3_builtin", "catalog:v3/common", risk_signals=("activation",), capabilities=("feature switch inspection",), evidence=("activation receipt",)),
    _rule("V3-CONTEXT-001", "COMMON", "Relevant context first", "Relevant project context must be discovered and selected before substantive mutation.", "REQUIRED", "v3_builtin", "catalog:v3/common", capabilities=("context discovery",), evidence=("current context-discovery receipt",)),
    _rule("V3-CONTEXT-002", "COMMON", "Task-scoped context", "Only task-relevant documents may be loaded into active context unless a justified expansion is recorded.", "REQUIRED", "v3_builtin", "catalog:v3/common", capabilities=("source selection",), evidence=("selection plan", "source references")),
    _rule("V3-SAFETY-001", "COMMON", "No sensitive context", "Secrets, credentials, tokens, private keys, PII, customer data, and personal runtime state must not enter repository docs, packets, logs, or learning records.", "BLOCK", "v3_builtin", "catalog:v3/common", risk_signals=("security", "privacy"), capabilities=("privacy scan",), evidence=("privacy scan result",)),
    _rule("V3-SAFETY-002", "COMMON", "Authorized destructive operations", "Destructive Git, database, service, hook, state, branch, worktree, or activation operations require explicit current authorization.", "BLOCK", "v3_builtin", "catalog:v3/common", risk_signals=("data loss", "authority"), capabilities=("authorization review",), evidence=("authorized operation record",)),
    _rule("V3-SCOPE-001", "COMMON", "Explicit mutation scope", "Every mutation has an explicit file or symbol allowlist and exclusion list.", "BLOCK", "v3_builtin", "catalog:v3/common", capabilities=("scope ledger",), evidence=("minimal-change ledger",)),
    _rule("V3-SCOPE-002", "COMMON", "Preserve unrelated changes", "Unrelated dirty changes and generated or local environment files are preserved.", "REQUIRED", "v3_builtin", "catalog:v3/common", capabilities=("worktree status review",), evidence=("before/after status", "scope review")),
    _rule(
        "V3-REUSE-001",
        "COMMON",
        "Repository-first reuse gate",
        "Before every actionable code or source mutation, perform a bounded search for an existing component, hook, client, service, contract, utility, script, configuration, schema, or established pattern and record REUSE, EXTEND, NEW, or DOCUMENTED_EXCEPTION before editing.",
        "BLOCK",
        "v3_builtin",
        "catalog:v3/common",
        capabilities=("bounded reuse discovery",),
        evidence=("repository-first reuse assessment",),
        condition="actionable code or source mutations only; direct answers, read-only checks, runtime-only validation, and documentation-only edits do not require this gate",
    ),
    _rule("V3-AGENT-001", "COMMON", "Bounded agents", "Every real normal or coupled agent has a canonical name, role, bounded packet, stop condition, and evidence contract.", "REQUIRED", "v3_builtin", "catalog:v3/common", capabilities=("agent dispatch",), evidence=("dispatch packet", "terminal receipt")),
    _rule("V3-AGENT-002", "COMMON", "One writer per scope", "One writer owns each mutation scope; read-only agents never receive writer or activation authority.", "BLOCK", "v3_builtin", "catalog:v3/common", risk_signals=("authority",), capabilities=("writer ownership",), evidence=("writer lease", "scope ledger")),
    _rule("V3-RISK-001", "COMMON", "Monotonic risk", "Risk and complexity are monotonic; a candidate or caller hint cannot lower a verified floor.", "BLOCK", "v3_builtin", "catalog:v3/common", capabilities=("risk reconciliation",), evidence=("reconciliation decision",)),
    _rule("V3-KNOW-001", "COMMON", "Revalidate knowledge", "Retrieved knowledge is a hypothesis until revalidated against current source, tests, runtime, or explicit user direction.", "REQUIRED", "v3_builtin", "catalog:v3/common", capabilities=("source revalidation",), evidence=("evidence card", "revalidation result")),
    _rule("V3-KNOW-002", "COMMON", "Scoped retrieval", "Knowledge retrieval is filtered by project, repository, tree, tenant, status, freshness, and access before exposure.", "REQUIRED", "v3_builtin", "catalog:v3/common", risk_signals=("privacy",), capabilities=("retrieval scope filtering",), evidence=("retrieval scope receipt",)),
    _rule("V3-EVIDENCE-001", "COMMON", "Bound evidence", "Receipts are bound to task, packet, source or diff fingerprint, role, runner, evidence, terminal state, timestamp, and expiry.", "BLOCK", "v3_builtin", "catalog:v3/common", capabilities=("receipt validation",), evidence=("validated receipt",)),
    _rule("V3-EVIDENCE-002", "COMMON", "Missing is not pass", "Missing, malformed, timed-out, stale, or non-terminal evidence is MISSING_COVERAGE, never PASS.", "BLOCK", "v3_builtin", "catalog:v3/common", capabilities=("verification matrix",), evidence=("verification matrix",)),
    _rule("V3-EVIDENCE-003", "COMMON", "Honest capabilities", "Runtime capability limitations are reported honestly; no browser, device, agent, or deployment result is simulated.", "REQUIRED", "v3_builtin", "catalog:v3/common", risk_signals=("runtime",), capabilities=("capability inspection",), evidence=("capability receipt",)),
    _rule("V3-CLOSE-001", "COMMON", "Separate closure claims", "Implementation, static checks, review, regression, tests, runtime, QA, publication, activation, worklog, and learning remain separate claims.", "REQUIRED", "v3_builtin", "catalog:v3/common", capabilities=("closure matrix",), evidence=("closure matrix", "worklog")),
    _rule("V3-CLOSE-002", "COMMON", "Fail-closed closure", "Required not_run, fail, blocked, unverified, or MISSING_COVERAGE rows prevent closure.", "BLOCK", "v3_builtin", "catalog:v3/common", capabilities=("verification matrix",), evidence=("verification matrix",)),
    _rule("V3-QA-001", "COMMON", "Visible advisory QA", "Web and mobile QA findings remain visible and source-bound but cannot silently mutate implementation closure.", "ADVISORY", "v3_builtin", "catalog:v3/common", capabilities=("QA receipt",), evidence=("QA receipt",)),
    _rule("V3-ACT-001", "COMMON", "Explicit activation", "Skill or controller activation requires explicit feature switch, parity evidence, host compatibility, rollback, and exactly-one-controller proof.", "BLOCK", "v3_builtin", "catalog:v3/common", risk_signals=("activation",), capabilities=("promotion review", "rollback review"), evidence=("promotion receipt", "rollback receipt")),
    _rule("V3-OBS-001", "COMMON", "Explicit termination state", "Worker timeout, interruption, crash, shutdown, and running states are distinguished from completed.", "REQUIRED", "v3_builtin", "catalog:v3/common", capabilities=("runtime observation",), evidence=("termination record",)),
]

SHARED_APPLICATION_RULES = [
    _rule("APP-CONTRACT-001", "SHARED_APPLICATION", "Shared contract compatibility", "Shared API request, response, error, identifier, and pagination semantics remain compatible for all declared consumers.", "REQUIRED", "shared_profile", "catalog:shared_application", risk_signals=("contract",), capabilities=("contract diff",), evidence=("contract diff", "consumer checks")),
    _rule("APP-AUTH-001", "SHARED_APPLICATION", "Cross-platform authorization", "Authentication, tenant isolation, authorization, and permission behavior remain aligned across web, mobile, and backend.", "BLOCK", "shared_profile", "catalog:shared_application", risk_signals=("authorization", "tenant", "security"), capabilities=("permission-path tracing",), evidence=("permission-path tests", "contract evidence")),
    _rule("APP-DATA-001", "SHARED_APPLICATION", "Entity semantics", "Entity identifiers, validation rules, nullability, and server-side ownership remain explicit and consistent.", "REQUIRED", "shared_profile", "catalog:shared_application", risk_signals=("data integrity",), capabilities=("schema/source trace",), evidence=("schema trace", "contract evidence")),
    _rule("APP-DATA-002", "SHARED_APPLICATION", "Authoritative persistence", "A client must not claim a successful mutation before the authoritative persistence path or approved local-first checkpoint succeeds.", "REQUIRED", "shared_profile", "catalog:shared_application", risk_signals=("persistence",), capabilities=("persistence trace",), evidence=("persistence or checkpoint evidence",)),
    _rule("APP-RETRY-001", "SHARED_APPLICATION", "Retry idempotency", "Retried or replayed mutations must not create unintended duplicates; operation identity and server behavior must be explicit where retries exist.", "BLOCK", "shared_profile", "catalog:shared_application", risk_signals=("queue", "data integrity"), capabilities=("idempotency analysis",), evidence=("idempotency or replay test",)),
    _rule("APP-ERROR-001", "SHARED_APPLICATION", "Actionable errors", "Error categories remain actionable and are not flattened into a misleading success or generic failure.", "REQUIRED", "shared_profile", "catalog:shared_application", capabilities=("error mapping trace",), evidence=("error mapping test",)),
    _rule("APP-FLAG-001", "SHARED_APPLICATION", "Controlled rollout", "Feature-flag and staged-rollout behavior is preserved; live gate changes require explicit approval.", "REQUIRED", "shared_profile", "catalog:shared_application", risk_signals=("activation",), capabilities=("flag source review",), evidence=("flag source", "rollout evidence")),
    _rule("APP-COMPAT-001", "SHARED_APPLICATION", "Consumer compatibility", "Existing consumers, public routes, and supported sessions are considered before changing shared behavior.", "REQUIRED", "shared_profile", "catalog:shared_application", capabilities=("regression inventory",), evidence=("regression inventory",)),
    _rule("APP-CROSS-001", "SHARED_APPLICATION", "Separate platform evidence", "A cross-platform acceptance criterion produces separate web and mobile evidence where both platforms are in scope.", "REQUIRED", "shared_profile", "catalog:shared_application", risk_signals=("cross-platform",), capabilities=("cross-platform verification",), evidence=("web receipt", "mobile receipt")),
    _rule("APP-PRIVACY-001", "SHARED_APPLICATION", "Application-data privacy", "Sensitive application or workspace data is not placed into prompts, logs, receipts, or learning records without authorized redaction.", "BLOCK", "shared_profile", "catalog:shared_application", risk_signals=("privacy", "security"), capabilities=("privacy scan",), evidence=("privacy scan",)),
]

WEB_RULES = [
    _rule("WEB-FLOW-001", "WEB", "Rendered flow trace", "Trace the changed user flow through the rendered result, not only the helper function.", "REQUIRED", "platform_profile", "catalog:web", capabilities=("rendered flow trace",), evidence=("source path", "rendered-state test")),
    _rule("WEB-CONTRACT-001", "WEB", "Approved client boundary", "Use the approved API contract/client boundary and do not hand-edit generated artifacts.", "REQUIRED", "platform_profile", "catalog:web", risk_signals=("contract",), capabilities=("contract source review",), evidence=("contract source", "generation check")),
    _rule("WEB-STATE-001", "WEB", "State invalidation", "Preserve query, cache, form, and invalidation behavior for affected callers.", "REQUIRED", "platform_profile", "catalog:web", capabilities=("state-flow trace",), evidence=("focused state test", "regression test")),
    _rule("WEB-A11Y-001", "WEB", "Accessible controls", "Preserve keyboard, focus, semantic label, and screen-reader behavior for changed controls.", "REQUIRED", "platform_profile", "catalog:web", capabilities=("accessibility review",), evidence=("accessibility check",)),
    _rule("WEB-RESP-001", "WEB", "Responsive behavior", "Verify responsive behavior at supported layout boundaries when the change affects layout.", "REQUIRED", "platform_profile", "catalog:web", risk_signals=("browser",), capabilities=("browser verification",), evidence=("responsive runtime evidence",)),
    _rule("WEB-RUNTIME-001", "WEB", "Real browser evidence", "Browser behavior claims require a real browser capability or remain NOT_VERIFIED or BLOCKED.", "REQUIRED", "platform_profile", "catalog:web", risk_signals=("browser",), capabilities=("browser runtime",), evidence=("browser receipt",)),
    _rule("WEB-TEST-001", "WEB", "Targeted web tests", "Use targeted unit, component, or browser tests tied to the changed path.", "REQUIRED", "platform_profile", "catalog:web", capabilities=("focused web tests",), evidence=("test command and result",)),
]

MOBILE_RULES = [
    _rule("MOB-FLOW-001", "MOBILE", "Mobile flow trace", "Trace the actual screen or event through state, repository, persistence, sync, lifecycle, and render boundaries.", "REQUIRED", "platform_profile", "catalog:mobile", capabilities=("mobile flow trace",), evidence=("source path", "flow trace")),
    _rule("MOB-LOCAL-001", "MOBILE", "Offline persistence", "For an offline-capable feature, persist the accepted operation locally before transport; connectivity controls draining, not whether the operation exists.", "REQUIRED", "platform_profile", "catalog:mobile", risk_signals=("offline", "persistence"), capabilities=("local persistence analysis",), evidence=("database test", "queue test"), condition="only when the feature is offline-capable"),
    _rule("MOB-QUEUE-001", "MOBILE", "Replayable queue", "Queue payloads contain all data required for replay and retain durable status or checkpoint information.", "REQUIRED", "platform_profile", "catalog:mobile", risk_signals=("queue",), capabilities=("queue analysis",), evidence=("queue schema", "replay test")),
    _rule("MOB-RETRY-001", "MOBILE", "Retry classification", "Retry classification distinguishes transient, terminal, and user-action-required failures.", "REQUIRED", "platform_profile", "catalog:mobile", risk_signals=("retry",), capabilities=("failure matrix",), evidence=("failure matrix", "targeted test")),
    _rule("MOB-IDEMPOTENCY-001", "MOBILE", "Backend replay proof", "Replay identity alone is not proof of exactly-once behavior; backend persistence and original-result behavior must be verified.", "REQUIRED", "platform_profile", "catalog:mobile", risk_signals=("data integrity", "queue"), capabilities=("backend replay verification",), evidence=("backend contract", "runtime evidence")),
    _rule("MOB-MIGRATE-001", "MOBILE", "Safe migrations", "Schema changes preserve existing data and use explicit migrations; no destructive reset is a default recovery.", "REQUIRED", "platform_profile", "catalog:mobile", risk_signals=("migration", "data loss"), capabilities=("migration inspection",), evidence=("migration test", "review")),
    _rule("MOB-LIFECYCLE-001", "MOBILE", "Lifecycle behavior", "Background or foreground transitions, process death, reconnect, and native lifecycle behavior are considered when applicable.", "REQUIRED", "platform_profile", "catalog:mobile", risk_signals=("device", "lifecycle"), capabilities=("lifecycle verification",), evidence=("lifecycle or device evidence",)),
    _rule("MOB-RUNTIME-001", "MOBILE", "Real device evidence", "Tests alone do not prove production-device behavior.", "REQUIRED", "platform_profile", "catalog:mobile", risk_signals=("device",), capabilities=("device runtime",), evidence=("emulator or device receipt",)),
    _rule("MOB-PRIVACY-001", "MOBILE", "Mobile privacy", "Sensitive mobile runtime configuration and user data remain outside logs, plans, and packets.", "BLOCK", "platform_profile", "catalog:mobile", risk_signals=("privacy", "security"), capabilities=("privacy scan",), evidence=("privacy scan",)),
]

BACKEND_RULES = [
    _rule("API-CONTRACT-001", "BACKEND", "Contract-first API", "Request, response, error, and generated DTO behavior must follow the approved contract source.", "REQUIRED", "platform_profile", "catalog:backend", risk_signals=("contract",), capabilities=("contract validation",), evidence=("contract validation", "targeted endpoint test")),
    _rule("API-AUTH-001", "BACKEND", "Authorize before access", "Authorization must be evaluated before sensitive data access.", "BLOCK", "platform_profile", "catalog:backend", risk_signals=("authorization", "security"), capabilities=("authorization trace",), evidence=("permission-path test",)),
    _rule("API-VALIDATION-001", "BACKEND", "Boundary validation", "Request validation occurs at the route or controller boundary.", "REQUIRED", "platform_profile", "catalog:backend", capabilities=("request-boundary review",), evidence=("validation test",)),
    _rule("API-CONSISTENCY-001", "BACKEND", "Write consistency", "Multi-write operations, queue effects, and external side effects have explicit transaction or consistency behavior.", "REQUIRED", "platform_profile", "catalog:backend", risk_signals=("persistence", "queue", "data integrity"), capabilities=("transaction analysis",), evidence=("consistency test",)),
    _rule("API-RATE-001", "BACKEND", "Rate limiting", "Rate limiting and trust-proxy behavior are verified for exposed endpoints where the repository uses them.", "REQUIRED", "platform_profile", "catalog:backend", risk_signals=("security",), capabilities=("rate-limit verification",), evidence=("429 or middleware evidence",)),
    _rule("API-MIGRATE-001", "BACKEND", "Migration rollback", "Schema changes include migration and rollback evidence.", "REQUIRED", "platform_profile", "catalog:backend", risk_signals=("migration", "data loss"), capabilities=("migration inspection",), evidence=("migration test", "rollback evidence")),
]

FLUTTER_RULES = [
    _rule("FLUTTER-TEST-001", "FLUTTER", "Flutter test boundary", "Flutter changes use focused unit, widget, integration, or device checks appropriate to the changed lifecycle boundary.", "REQUIRED", "platform_profile", "catalog:flutter", capabilities=("focused Flutter tests",), evidence=("Flutter test command and result",)),
]

MEDIA_RULES = [
    _rule("MEDIA-PRIVACY-001", "PROJECT", "Media privacy", "Recording, microphone, camera, and system-audio privacy rules remain scoped to media-enabled project areas.", "BLOCK", "shared_profile", "catalog:media", risk_signals=("privacy", "security"), capabilities=("media privacy review",), evidence=("media privacy evidence",)),
    _rule("MEDIA-PLAYBACK-001", "PROJECT", "Media playback compatibility", "Public playback and media timestamp behavior remain compatible with the declared media contract.", "REQUIRED", "shared_profile", "catalog:media", risk_signals=("contract",), capabilities=("media contract review",), evidence=("playback compatibility evidence",)),
]

CATALOGS = {
    "v3_common": COMMON_RULES,
    "shared_application": SHARED_APPLICATION_RULES,
    "web": WEB_RULES,
    "mobile": MOBILE_RULES,
    "backend": BACKEND_RULES,
    "flutter": FLUTTER_RULES,
    "media": MEDIA_RULES,
}


def all_rules() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for rules in CATALOGS.values():
        result.extend(deepcopy(rules))
    return result


def rules_for_profiles(profiles: Iterable[str]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for profile in profiles:
        for rule in CATALOGS.get(profile, ()):
            if rule["ruleId"] not in seen:
                selected.append(deepcopy(rule))
                seen.add(rule["ruleId"])
    return selected


def explain_rule(rule_id: str) -> dict[str, Any] | None:
    for rule in all_rules():
        if rule["ruleId"] == rule_id:
            return rule
    return None
