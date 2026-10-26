from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.model_selection import (  # noqa: E402
    ASSIGNMENT_SCHEMA_VERSION,
    MODEL_SELECTION_SCHEMA_VERSION,
    ModelSelectionError,
    assignment_is_current,
    canonical_json,
    explain_selection,
    policy_fingerprint,
    select_model_assignments,
    validate_assignment,
    validate_policy,
    validate_task_facts,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "model-selection"
CONFIG_PATH = PACKAGE_ROOT / "config" / "model-selection-v1.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_policy() -> dict:
    return load_json(CONFIG_PATH)


def load_case(name: str) -> tuple[dict, dict]:
    fixture = load_json(FIXTURE_ROOT / name)
    return fixture["taskFacts"], fixture["hostCatalog"]


class ModelSelectionContractTests(unittest.TestCase):
    def test_policy_fixture_is_strict_and_fingerprint_is_self_consistent(self):
        policy = load_policy()

        result = validate_policy(policy)

        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(policy["schemaVersion"], MODEL_SELECTION_SCHEMA_VERSION)
        self.assertEqual(policy["policyFingerprint"], policy_fingerprint(policy))
        self.assertEqual(policy["workerDefault"], {"model": "model-fast", "reasoningEffort": "medium"})
        self.assertEqual(
            {rule["taskClasses"][0] for rule in policy["workerRules"] if len(rule["taskClasses"]) == 1},
            {"lightweight", "small", "normal", "final-audit"},
        )
        self.assertEqual(policy["maximumParallelism"], 4)
        self.assertEqual(
            {rule["model"] for rule in policy["coordinatorRules"]},
            {"model-balanced", "model-advanced"},
        )

    def test_policy_declares_bounded_model_fallback_rules(self):
        policy = load_policy()

        normal_rule = next(rule for rule in policy["fallbackRules"] if rule["ruleId"] == "normal-worker-fast_model-to-balanced_model")
        high_risk_rule = next(rule for rule in policy["fallbackRules"] if rule["ruleId"] == "high-risk-worker-advanced-to-balanced")

        self.assertTrue(normal_rule["enabled"])
        self.assertTrue(normal_rule["automatic"])
        self.assertEqual(normal_rule["fromModel"], "model-fast")
        self.assertEqual(normal_rule["toModel"], "model-balanced")
        self.assertEqual(normal_rule["reasoningEffort"], "high")
        self.assertEqual(normal_rule["maxScopes"], 1)
        self.assertEqual(normal_rule["maxLifetimeSeconds"], 3600)
        self.assertFalse(high_risk_rule["automatic"])
        self.assertEqual(high_risk_rule["fromModel"], "model-advanced")
        self.assertEqual(high_risk_rule["toModel"], "model-balanced")

    def test_policy_fingerprint_is_stable_for_mapping_order(self):
        policy = load_policy()
        reordered = {key: policy[key] for key in reversed(list(policy))}

        self.assertEqual(canonical_json(policy), canonical_json(reordered))
        self.assertEqual(policy_fingerprint(policy), policy_fingerprint(reordered))

    def test_direct_question_produces_no_assignment(self):
        facts, host_catalog = load_case("direct-question.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "DIRECT_NO_ASSIGNMENT")
        self.assertEqual(result["assignments"], [])
        self.assertNotIn("model", result)
        self.assertNotIn("resolvedModel", result)

    def test_plan_only_route_produces_no_assignment(self):
        facts, host_catalog = load_case("direct-question.json")
        facts["route"] = "PLAN_ONLY"

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "DIRECT_NO_ASSIGNMENT")
        self.assertEqual(result["assignments"], [])

    def test_small_lead_only_change_produces_no_assignment(self):
        facts, host_catalog = load_case("small-lead-only.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "DIRECT_NO_ASSIGNMENT")
        self.assertEqual(result["assignments"], [])

    def test_lightweight_worker_uses_fast_model_low(self):
        facts, host_catalog = load_case("normal-feature.json")
        facts["taskClass"] = "lightweight"
        facts["needsCoordinator"] = False
        facts["workerScopes"] = [facts["workerScopes"][0]]
        host_catalog["availableModels"]["model-fast"] = ["low"]

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["assignments"][0]["resolvedModel"], "model-fast")
        self.assertEqual(result["assignments"][0]["reasoningEffort"], "low")

    def test_small_worker_uses_fast_model_medium(self):
        facts, host_catalog = load_case("normal-feature.json")
        facts["taskClass"] = "small"
        facts["needsCoordinator"] = False
        facts["workerScopes"] = [facts["workerScopes"][0]]
        host_catalog["availableModels"]["model-fast"] = ["medium"]

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(result["assignments"][0]["resolvedModel"], "model-fast")
        self.assertEqual(result["assignments"][0]["reasoningEffort"], "medium")

    def test_normal_worker_uses_bounded_advanced_model_fallback_when_balanced_model_is_unavailable(self):
        facts, host_catalog = load_case("normal-feature.json")
        facts["needsCoordinator"] = False
        facts["workerScopes"] = [facts["workerScopes"][0]]
        host_catalog["availableModels"].pop("model-balanced")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "FALLBACK_EVIDENCED")
        assignment = result["assignments"][0]
        self.assertEqual(assignment["desiredModel"], "model-balanced")
        self.assertEqual(assignment["resolvedModel"], "model-advanced")
        self.assertEqual(assignment["reasoningEffort"], "max")
        self.assertIn("fallback_policy:normal-worker-balanced_model-to-advanced_model", assignment["overrideEvidence"]["evidence"])
        self.assertTrue(validate_assignment(assignment, expected_policy=load_policy())["valid"])

    def test_normal_work_uses_balanced_model_for_coordinator_and_workers(self):
        facts, host_catalog = load_case("normal-feature.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "SELECTED")
        self.assertEqual(len(result["assignments"]), 3)
        coordinator = result["assignments"][0]
        workers = result["assignments"][1:]
        self.assertEqual(coordinator["role"], "normal-coordinator")
        self.assertEqual(coordinator["resolvedModel"], "model-balanced")
        self.assertEqual(coordinator["reasoningEffort"], "high")
        self.assertTrue(all(item["role"] == "worker" for item in workers))
        self.assertTrue(all(item["desiredModel"] == "model-balanced" for item in workers))
        self.assertTrue(all(item["resolvedModel"] == "model-balanced" for item in workers))
        self.assertTrue(all(item["reasoningEffort"] == "high" for item in workers))
        self.assertEqual({item["role"] for item in result["assignments"]}, {"normal-coordinator", "worker"})
        self.assertTrue(all(item["policyFingerprint"] == result["policyFingerprint"] for item in result["assignments"]))
        self.assertTrue(all(item["taskId"] == facts["taskId"] for item in result["assignments"]))

    def test_coupled_security_work_uses_advanced_model_for_coordinator_and_workers(self):
        facts, host_catalog = load_case("coupled-security.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "SELECTED")
        coordinator = next(item for item in result["assignments"] if item["role"] == "high-risk-coordinator")
        worker = next(item for item in result["assignments"] if item["role"] == "worker")
        self.assertEqual(coordinator["resolvedModel"], "model-advanced")
        self.assertEqual(coordinator["reasoningEffort"], "max")
        self.assertEqual(worker["resolvedModel"], "model-advanced")
        self.assertEqual(worker["reasoningEffort"], "max")
        self.assertNotEqual(coordinator["role"], worker["role"])

    def test_final_audit_uses_advanced_model_reviewer_and_fast_model_only_for_delegated_evidence(self):
        facts, host_catalog = load_case("final-audit.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "SELECTED")
        reviewer = next(item for item in result["assignments"] if item["role"] == "final-audit")
        evidence_worker = next(item for item in result["assignments"] if item["role"] == "worker")
        self.assertEqual(reviewer["resolvedModel"], "model-advanced")
        self.assertEqual(evidence_worker["resolvedModel"], "model-fast")
        self.assertEqual(evidence_worker["scope"]["authority"], "read")

    def test_missing_fast_model_uses_policy_approved_balanced_model_fallback(self):
        facts, host_catalog = load_case("fast-model-unavailable.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "FALLBACK_EVIDENCED")
        self.assertEqual(len(result["assignments"]), 1)
        assignment = result["assignments"][0]
        self.assertEqual(assignment["status"], "FALLBACK_EVIDENCED")
        self.assertEqual(assignment["desiredModel"], "model-fast")
        self.assertEqual(assignment["resolvedModel"], "model-balanced")
        self.assertEqual(assignment["reasoningEffort"], "high")
        self.assertEqual(assignment["capabilityStatus"], "FALLBACK_APPROVED")
        evidence = assignment["overrideEvidence"]
        self.assertEqual(evidence["origin"], "host_policy")
        self.assertEqual(evidence["model"], "model-balanced")
        self.assertEqual(evidence["reasoningEffort"], "high")
        self.assertEqual(evidence["taskId"], facts["taskId"])
        self.assertEqual(evidence["scopeId"], "worker-1")
        self.assertIn("fast_model_unavailable", evidence["evidence"])
        self.assertIn("fallback_policy:normal-worker-fast_model-to-balanced_model", evidence["evidence"])
        self.assertNotEqual(assignment["resolvedModel"], "model-advanced")

    def test_selection_rejects_task_expired_at_validation_time(self):
        facts, host_catalog = load_case("fast-model-unavailable.json")
        facts["validationTime"] = facts["expiresAt"]

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["assignments"], [])
        self.assertTrue(any("validationtime" in error.lower() for error in result["errors"]))

    def test_missing_fast_model_without_policy_target_remains_unavailable(self):
        facts, host_catalog = load_case("fast-model-unavailable.json")
        host_catalog["availableModels"] = {"model-advanced": ["max"]}

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "CAPABILITY_UNAVAILABLE")
        assignment = result["assignments"][0]
        self.assertEqual(assignment["status"], "CAPABILITY_UNAVAILABLE")
        self.assertIsNone(assignment["resolvedModel"])
        self.assertIsNone(assignment["reasoningEffort"])

    def test_normal_fallback_is_not_applied_beyond_one_worker_scope(self):
        facts, host_catalog = load_case("normal-feature.json")
        host_catalog["availableModels"].pop("model-balanced")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "CAPABILITY_UNAVAILABLE")
        self.assertNotIn("FALLBACK_EVIDENCED", {item["status"] for item in result["assignments"]})
        self.assertTrue(all(item["status"] != "FALLBACK_EVIDENCED" for item in result["assignments"]))

    def test_high_risk_preferred_model_failure_stays_unavailable_without_approval(self):
        facts, host_catalog = load_case("coupled-security.json")
        host_catalog["availableModels"].pop("model-advanced")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "CAPABILITY_UNAVAILABLE")
        worker = next(item for item in result["assignments"] if item["role"] == "worker")
        self.assertEqual(worker["status"], "CAPABILITY_UNAVAILABLE")
        self.assertIsNone(worker["resolvedModel"])

    def test_final_audit_never_falls_back_when_fast_model_is_unavailable(self):
        facts, host_catalog = load_case("final-audit.json")
        host_catalog["availableModels"].pop("model-fast")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "CAPABILITY_UNAVAILABLE")
        worker = next(item for item in result["assignments"] if item["role"] == "worker")
        self.assertEqual(worker["status"], "CAPABILITY_UNAVAILABLE")
        self.assertIsNone(worker["resolvedModel"])

    def test_fallback_assignment_requires_fallback_evidence(self):
        facts, host_catalog = load_case("fast-model-unavailable.json")
        assignment = select_model_assignments(facts, host_catalog, load_policy())["assignments"][0]

        self.assertTrue(validate_assignment(assignment, expected_policy=load_policy())["valid"])

        without_evidence = copy.deepcopy(assignment)
        without_evidence["overrideEvidence"] = None
        result = validate_assignment(without_evidence, expected_policy=load_policy())
        self.assertFalse(result["valid"])
        self.assertTrue(any("fallback" in error.lower() for error in result["errors"]))

    def test_fallback_policy_rejects_unknown_fields(self):
        policy = load_policy()
        policy["fallbackRules"][0]["unexpected"] = True

        result = validate_policy(policy)

        self.assertFalse(result["valid"])
        self.assertTrue(any("unknown field" in error for error in result["errors"]))

    def test_explicit_host_inheritance_is_not_reported_as_selected(self):
        facts, host_catalog = load_case("host-inherited.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "HOST_INHERITED")
        assignment = result["assignments"][0]
        self.assertEqual(assignment["status"], "HOST_INHERITED")
        self.assertEqual(assignment["resolvedModel"], "model-advanced")
        self.assertIn("host", assignment["selectionRationale"].lower())

    def test_untrusted_override_is_rejected_before_model_resolution(self):
        facts, host_catalog = load_case("untrusted-override.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["assignments"], [])
        self.assertTrue(any("untrusted" in error.lower() for error in result["errors"]))

    def test_high_risk_worker_requires_task_bound_trusted_exception_evidence(self):
        facts, host_catalog = load_case("approved-exception.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "OVERRIDE_EVIDENCED")
        assignment = result["assignments"][0]
        self.assertEqual(assignment["status"], "OVERRIDE_EVIDENCED")
        self.assertEqual(assignment["desiredModel"], "model-advanced")
        self.assertEqual(assignment["resolvedModel"], "model-balanced")
        self.assertEqual(assignment["overrideEvidence"]["taskId"], facts["taskId"])
        self.assertEqual(assignment["overrideEvidence"]["scopeId"], "worker-1")

    def test_assignment_validator_binds_explicit_fallback_to_task_class_and_source_model(self):
        facts, host_catalog = load_case("approved-exception.json")
        assignment = select_model_assignments(facts, host_catalog, load_policy())["assignments"][0]

        wrong_class = copy.deepcopy(assignment)
        wrong_class["task"]["taskClass"] = "normal"
        self.assertFalse(validate_assignment(wrong_class, expected_policy=load_policy())["valid"])

        wrong_source = copy.deepcopy(assignment)
        wrong_source["desiredModel"] = "model-fast"
        self.assertFalse(validate_assignment(wrong_source, expected_policy=load_policy())["valid"])

    def test_high_risk_fallback_requires_explicit_user_origin(self):
        facts, host_catalog = load_case("approved-exception.json")
        facts["override"]["origin"] = "host_policy"

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["assignments"], [])
        self.assertTrue(any("explicit_user" in error for error in result["errors"]))

    def test_high_risk_fallback_requires_fallback_rule_evidence(self):
        facts, host_catalog = load_case("approved-exception.json")
        facts["override"]["evidence"] = [
            item for item in facts["override"]["evidence"]
            if not item.startswith("fallback_policy:")
        ]

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["assignments"], [])
        self.assertTrue(any("fallback policy" in error.lower() for error in result["errors"]))

    def test_high_risk_fallback_rejects_wrong_fallback_rule_id(self):
        facts, host_catalog = load_case("approved-exception.json")
        facts["override"]["evidence"] = [
            "advanced_model_unavailable",
            "bounded_scope_failure",
            "fallback_policy:normal-worker-fast_model-to-balanced_model",
        ]

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["assignments"], [])
        self.assertTrue(any("fallback policy" in error.lower() for error in result["errors"]))

    def test_high_risk_fallback_rejects_duplicate_fallback_rule_ids(self):
        facts, host_catalog = load_case("approved-exception.json")
        facts["override"]["evidence"].append("fallback_policy:high-risk-worker-advanced-to-balanced")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["assignments"], [])
        self.assertTrue(any("duplicate" in error.lower() for error in result["errors"]))

    def test_assignment_validator_rejects_unbound_high_risk_fallback_evidence(self):
        facts, host_catalog = load_case("approved-exception.json")
        assignment = select_model_assignments(facts, host_catalog, load_policy())["assignments"][0]
        assignment["overrideEvidence"]["evidence"] = [
            item for item in assignment["overrideEvidence"]["evidence"]
            if not item.startswith("fallback_policy:")
        ]

        result = validate_assignment(assignment, expected_policy=load_policy())

        self.assertFalse(result["valid"])
        self.assertTrue(any("fallback policy" in error.lower() for error in result["errors"]))

    def test_assignment_validator_requires_evidence_window_to_match_assignment(self):
        facts, host_catalog = load_case("fast-model-unavailable.json")
        assignment = select_model_assignments(facts, host_catalog, load_policy())["assignments"][0]
        assignment["overrideEvidence"]["expiresAt"] = "2026-08-24T08:31:00Z"

        result = validate_assignment(assignment, expected_policy=load_policy())

        self.assertFalse(result["valid"])
        self.assertTrue(any("expiresAt" in error for error in result["errors"]))

    def test_explicit_fallback_is_rejected_when_advanced_model_is_available(self):
        facts, host_catalog = load_case("coupled-security.json")
        facts["override"] = {
            "origin": "explicit_user",
            "model": "model-balanced",
            "reasoningEffort": "high",
            "taskId": facts["taskId"],
            "scopeId": facts["workerScopes"][0]["scopeId"],
            "evidence": ["advanced_model_unavailable", "bounded_scope_failure", "fallback_policy:high-risk-worker-advanced-to-balanced"],
            "approvalId": "user-approval-available-advanced_model",
            "issuedAt": facts["issuedAt"],
            "expiresAt": facts["expiresAt"],
            "policyFingerprint": policy_fingerprint(load_policy()),
        }

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertTrue(any("preferred complexity-rule model is available" in error.lower() for error in result["errors"]))

    def test_override_with_unmatched_scope_is_rejected(self):
        facts, host_catalog = load_case("approved-exception.json")
        facts["override"]["scopeId"] = "worker-other"

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertTrue(any("scope" in error.lower() for error in result["errors"]))

    def test_final_audit_override_is_rejected_even_with_advanced_model_available(self):
        facts, host_catalog = load_case("final-audit.json")
        host_catalog["availableModels"].pop("model-fast")
        facts["override"] = {
            "origin": "explicit_user",
            "model": "model-balanced",
            "reasoningEffort": "high",
            "taskId": facts["taskId"],
            "scopeId": facts["workerScopes"][0]["scopeId"],
            "evidence": ["advanced_model_unavailable", "bounded_scope_failure", "fallback_policy:high-risk-worker-advanced-to-balanced"],
            "approvalId": "user-approval-final-audit",
            "issuedAt": facts["issuedAt"],
            "expiresAt": facts["expiresAt"],
            "policyFingerprint": policy_fingerprint(load_policy()),
        }

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertTrue(any("final-audit" in error.lower() for error in result["errors"]))

    def test_expired_override_is_rejected_at_selection_time(self):
        facts, host_catalog = load_case("approved-exception.json")
        facts["override"]["expiresAt"] = "2026-08-24T08:20:00Z"

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertTrue(any("expired" in error.lower() for error in result["errors"]))

    def test_general_fast_model_failure_does_not_authorize_worker_exception(self):
        facts, host_catalog = load_case("invalid-general-exception.json")

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["assignments"], [])
        self.assertTrue(any("evidence" in error.lower() for error in result["errors"]))

    def test_direct_route_cannot_become_assignment_through_requested_role(self):
        facts, host_catalog = load_case("direct-question.json")
        facts["requestedRoles"] = ["worker"]

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "DIRECT_NO_ASSIGNMENT")
        self.assertEqual(result["assignments"], [])

    def test_untrusted_model_and_authority_fields_are_rejected(self):
        facts, host_catalog = load_case("normal-feature.json")
        facts["requestedModel"] = "model-advanced"
        facts["authority"] = "write-anywhere"

        validation = validate_task_facts(facts)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("unknown field" in error for error in validation["errors"]))
        result = select_model_assignments(facts, host_catalog, load_policy())
        self.assertEqual(result["status"], "REJECTED")

    def test_policy_rejects_unknown_keys_and_unsupported_model_ids(self):
        policy = load_policy()
        policy["unexpected"] = True
        unknown_result = validate_policy(policy)
        self.assertFalse(unknown_result["valid"])
        self.assertTrue(any("unknown field" in error for error in unknown_result["errors"]))

        policy = load_policy()
        policy["workerDefault"]["model"] = "model-unknown"
        unsupported_result = validate_policy(policy)
        self.assertFalse(unsupported_result["valid"])
        self.assertTrue(any("unsupported model" in error for error in unsupported_result["errors"]))

    def test_policy_rejects_invalid_dynamic_worker_rule(self):
        policy = load_policy()
        policy["workerRules"][0]["reasoningEffort"] = "unsupported"

        result = validate_policy(policy)

        self.assertFalse(result["valid"])
        self.assertTrue(any("unsupported" in error.lower() for error in result["errors"]))

    def test_assignment_validator_rejects_unknown_fields_missing_expiry_and_stale_policy(self):
        facts, host_catalog = load_case("normal-feature.json")
        selected = select_model_assignments(facts, host_catalog, load_policy())
        assignment = selected["assignments"][1]

        with_unknown = copy.deepcopy(assignment)
        with_unknown["untrustedField"] = "nope"
        self.assertFalse(validate_assignment(with_unknown)["valid"])

        with_task_summary = copy.deepcopy(assignment)
        with_task_summary["task"] = {"taskId": assignment["taskId"]}
        self.assertTrue(validate_assignment(with_task_summary)["valid"])
        with_task_summary["task"]["taskId"] = "other-task"
        self.assertFalse(validate_assignment(with_task_summary)["valid"])

        without_expiry = copy.deepcopy(assignment)
        del without_expiry["expiresAt"]
        self.assertFalse(validate_assignment(without_expiry)["valid"])

        stale_policy = copy.deepcopy(assignment)
        stale_policy["policyFingerprint"] = "0" * 64
        stale_result = validate_assignment(stale_policy, expected_policy=load_policy())
        self.assertFalse(stale_result["valid"])
        self.assertTrue(any("policy" in error.lower() for error in stale_result["errors"]))

    def test_assignment_validator_rejects_worker_advanced_model_without_exception_state(self):
        facts, host_catalog = load_case("normal-feature.json")
        assignment = copy.deepcopy(select_model_assignments(facts, host_catalog, load_policy())["assignments"][1])
        assignment["resolvedModel"] = "model-advanced"

        result = validate_assignment(assignment, expected_policy=load_policy())

        self.assertFalse(result["valid"])
        self.assertTrue(any("escalate" in error.lower() for error in result["errors"]))

    def test_assignment_binding_invalidates_changed_task_project_repository_or_source(self):
        facts, host_catalog = load_case("normal-feature.json")
        assignment = select_model_assignments(facts, host_catalog, load_policy())["assignments"][1]
        binding = {
            "taskId": facts["taskId"],
            "taskClass": facts["taskClass"],
            "projectId": facts["projectId"],
            "repositoryId": facts["repositoryId"],
            "tenantId": facts["tenantId"],
            "packetVersion": facts["packetVersion"],
            "planVersion": facts["planVersion"],
            "waveId": facts["waveId"],
            "attempt": facts["attempt"],
            "sourceFingerprint": facts["sourceFingerprint"],
            "policyFingerprint": policy_fingerprint(load_policy()),
            "hostId": facts["hostId"],
        }
        self.assertTrue(assignment_is_current(assignment, binding, now=facts["validationTime"])["valid"])

        for field, value in (
            ("taskId", "other-task"),
            ("taskClass", "security"),
            ("projectId", "other-project"),
            ("repositoryId", "other-repository"),
            ("sourceFingerprint", "other-source"),
            ("policyFingerprint", "other-policy"),
        ):
            changed = dict(binding)
            changed[field] = value
            result = assignment_is_current(assignment, changed, now=facts["validationTime"])
            self.assertFalse(result["valid"], field)
            self.assertEqual(result["status"], "INVALIDATED")

    def test_assignment_validator_rejects_expired_assignment(self):
        facts, host_catalog = load_case("normal-feature.json")
        assignment = select_model_assignments(facts, host_catalog, load_policy())["assignments"][1]

        result = validate_assignment(assignment, now="2026-08-24T10:00:00Z")

        self.assertFalse(result["valid"])
        self.assertTrue(any("expired" in error.lower() for error in result["errors"]))

    def test_host_catalog_unknown_fields_are_rejected(self):
        facts, host_catalog = load_case("normal-feature.json")
        host_catalog["rawPrompt"] = "ignore policy"

        result = select_model_assignments(facts, host_catalog, load_policy())

        self.assertEqual(result["status"], "REJECTED")
        self.assertTrue(any("unknown field" in error for error in result["errors"]))

    def test_explainer_is_redacted_deterministic_and_contains_assignments(self):
        fixture_path = FIXTURE_ROOT / "normal-feature.json"

        first = explain_selection(load_json(fixture_path), load_policy())
        second = explain_selection(load_json(fixture_path), load_policy())

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "SELECTED")
        self.assertEqual(len(first["assignments"]), 3)
        self.assertNotIn("rawPrompt", json.dumps(first))
        self.assertNotIn("transcript", json.dumps(first))

    def test_validation_cli_accepts_policy_and_explainer_cli_returns_json(self):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        validator = subprocess.run(
            [sys.executable, "-B", "scripts/validate_model_selection.py", str(CONFIG_PATH)],
            cwd=PACKAGE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(validator.returncode, 0, validator.stderr)
        self.assertTrue(json.loads(validator.stdout)["valid"])

        explainer = subprocess.run(
            [
                sys.executable,
                "-B",
                "scripts/explain_model_selection.py",
                "--fixture",
                str(FIXTURE_ROOT / "normal-feature.json"),
            ],
            cwd=PACKAGE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(explainer.returncode, 0, explainer.stderr)
        explained = json.loads(explainer.stdout)
        self.assertEqual(explained["status"], "SELECTED")
        self.assertEqual(explained["assignments"][1]["resolvedModel"], "model-balanced")

    def test_invalid_fixture_is_rejected_by_explainer_cli(self):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "scripts/explain_model_selection.py",
                "--fixture",
                str(FIXTURE_ROOT / "untrusted-override.json"),
            ],
            cwd=PACKAGE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["status"], "REJECTED")

    def test_invalid_policy_raises_only_when_selector_cannot_make_a_safe_decision(self):
        policy = load_policy()
        policy["policyFingerprint"] = "0" * 64
        facts, host_catalog = load_case("normal-feature.json")

        with self.assertRaises(ModelSelectionError):
            select_model_assignments(facts, host_catalog, policy)


if __name__ == "__main__":
    unittest.main()
