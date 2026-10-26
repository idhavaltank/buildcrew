import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from rag_contracts import (
    budget_for_profile,
    fetch_knowledge,
    find_duplicate_lesson,
    freshness_state,
    invalidate_knowledge,
    learning_metrics,
    promote_lesson,
    propose_lesson,
    record_knowledge_feedback,
    record_learning_writeback,
    retrieve_knowledge,
    validate_lesson,
)


NOW = "2026-08-18T12:00:00Z"


def lesson(record_id="rag-001", **overrides):
    record = {
        "schemaVersion": "rag-record-v1",
        "id": record_id,
        "claim": "The sync worker must acknowledge a replay after processing.",
        "kind": "failure_pattern",
        "scope": "repo",
        "memoryTier": "dynamic_durable",
        "projectId": "general",
        "repository": "backend",
        "branchOrTree": "tree-current",
        "accessScope": ["project:general:read"],
        "fingerprints": {"symbol": "ReplayWorker", "path": "apps/api/sync.py", "taskType": "bug"},
        "status": "verified",
        "confidence": "high",
        "canonicalKey": "sync-replay-ack",
        "contentHash": "a" * 64,
        "evidence": [{
            "repository": "backend", "commitOrTree": "tree-current", "path": "apps/api/sync.py",
            "symbol": "ReplayWorker", "check": "python3 -m unittest tests.test_sync", "sourceType": "test",
        }],
        "observedAt": "2026-08-01T00:00:00Z",
        "lastVerifiedAt": "2026-08-10T00:00:00Z",
        "reviewAt": "2026-09-01T00:00:00Z",
        "expiresAt": None,
        "contradictionGroup": None,
        "sensitivity": "none",
    }
    record.update(overrides)
    return record


def retrieve(records, **kwargs):
    request = {
        "query": "ReplayWorker sync replay",
        "project_id": "general",
        "repository": "backend",
        "branch_or_tree": "tree-current",
        "access_scope": ["project:general:read"],
        "fingerprints": {"symbol": "ReplayWorker"},
        "task_type": "bug",
        "now": NOW,
    }
    request.update(kwargs)
    return retrieve_knowledge(records, **request)


class RagContractTests(unittest.TestCase):
    def test_schema_declares_the_provider_neutral_record_boundary(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "rag-record-v1.schema.json"
        schema = json.loads(schema_path.read_text())
        self.assertEqual("rag-record-v1", schema["properties"]["schemaVersion"]["const"])
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("accessScope", schema["required"])
        self.assertIn("evidence", schema["required"])
        self.assertIn("memoryTier", schema["required"])

    def test_memory_tier_and_tenant_filters_are_fail_closed(self):
        tenant_a = lesson("rag-tenant-a", tenantId="tenant-a")
        tenant_b = lesson("rag-tenant-b", tenantId="tenant-b")
        policy = lesson("rag-policy", memoryTier="stable_policy", scope="project")
        result = retrieve([tenant_a, tenant_b, policy], tenant_id="tenant-a", memory_tiers=["dynamic_durable"])
        self.assertEqual(["rag-tenant-a"], [card["recordId"] for card in result["cards"]])
        self.assertEqual("tenant-a", result["tenantApplied"])
        self.assertEqual(["dynamic_durable"], result["memoryTiersApplied"])

    def test_user_correction_is_explicit_authority_after_revalidation(self):
        correction = lesson(
            "rag-correction",
            kind="correction",
            claim="The user explicitly corrected the replay acknowledgement rule.",
            evidence=[{
                "repository": "backend", "commitOrTree": "tree-current", "path": "user-direction",
                "symbol": "correction", "check": "user confirmation", "sourceType": "user",
            }],
        )
        result = retrieve([correction])
        self.assertEqual("explicit_user_direction", result["executionAuthority"])
        self.assertEqual("explicit_user_direction", result["cards"][0]["authority"])

    def test_sanitized_verified_lesson_is_valid(self):
        result = validate_lesson(lesson())
        self.assertTrue(result["valid"], result["errors"])

    def test_raw_prompt_and_pii_are_rejected_or_redacted_without_echoing_them(self):
        unsafe = lesson(claim="raw prompt: user@example.com asks to fix sync")
        rejected = validate_lesson(unsafe)
        redacted = validate_lesson(unsafe, redact=True)
        self.assertFalse(rejected["valid"])
        self.assertEqual("rejected", rejected["outcome"])
        self.assertEqual("redacted", redacted["outcome"])
        self.assertNotIn("user@example.com", " ".join(rejected["errors"]))

    def test_secret_transcript_patch_and_unknown_fields_are_rejected(self):
        for field, value in (("rawPrompt", "safe words"), ("transcript", "safe words"), ("patch", "safe words"), ("claim", "token=secret-value"), ("claim", "customer_id=BuildCrew-123"), ("claim", "diff --git a/sync.py b/sync.py")):
            candidate = lesson()
            candidate[field] = value
            self.assertFalse(validate_lesson(candidate)["valid"], field)

    def test_access_and_project_repository_filters_run_before_cards(self):
        unauthorized = lesson("rag-no-access", accessScope=["tenant:other:read"])
        other_project = lesson("rag-other-project", projectId="other")
        result = retrieve([lesson(), unauthorized, other_project])
        self.assertEqual("matched", result["outcome"])
        self.assertEqual(["rag-001"], [card["recordId"] for card in result["cards"]])

    def test_exact_fingerprint_precedes_lexical_match(self):
        exact = lesson("rag-exact")
        lexical = lesson("rag-lexical", fingerprints={"path": "apps/api/sync.py", "taskType": "bug"}, confidence="high")
        result = retrieve([lexical, exact])
        self.assertEqual("rag-exact", result["cards"][0]["recordId"])
        self.assertEqual("exact_fingerprint", result["cards"][0]["match"])

    def test_budgets_are_bounded_by_card_count_and_token_budget(self):
        records = [lesson(f"rag-{index}", claim="ReplayWorker " + ("sync " * 80), contentHash=f"{index:064x}") for index in range(4)]
        result = retrieve(records, max_records=99, token_budget=100)
        self.assertLessEqual(len(result["cards"]), 8)
        self.assertLessEqual(result["budget"]["tokensUsed"], 100)
        self.assertEqual({"maxRecords": 5, "tokenBudget": 2000}, budget_for_profile("normal"))

    def test_duplicate_records_are_suppressed_and_retrieval_metrics_are_exposed(self):
        duplicate = lesson("rag-copy", contentHash="a" * 64)
        result = retrieve([lesson(), duplicate])
        self.assertEqual(["rag-001"], [card["recordId"] for card in result["cards"]])
        self.assertEqual(1, result["metrics"]["duplicateExcluded"])
        self.assertEqual(1, result["metrics"]["cardsReturned"])
        self.assertGreaterEqual(result["metrics"]["latencyMs"], 0)

    def test_stale_record_is_only_a_lead_and_expired_or_rejected_records_are_hidden(self):
        stale = lesson("rag-stale", branchOrTree="tree-old")
        expired = lesson("rag-expired", expiresAt="2026-08-01T00:00:00Z")
        rejected = lesson("rag-rejected", status="rejected")
        result = retrieve([stale, expired, rejected])
        self.assertEqual(["rag-stale"], [card["recordId"] for card in result["cards"]])
        self.assertEqual("stale", result["cards"][0]["freshness"]["state"])
        self.assertEqual("do_not_use", result["cards"][0]["usability"])
        self.assertEqual("hidden", freshness_state(expired, current_tree="tree-current", now=NOW))

    def test_conflicts_remain_visible_and_current_source_wins(self):
        first = lesson("rag-a", contradictionGroup="sync-ack", claim="Ack before processing.")
        second = lesson("rag-b", contradictionGroup="sync-ack", claim="Ack after processing.", contentHash="b" * 64)
        result = retrieve([first, second])
        self.assertEqual("current_source", result["executionAuthority"])
        self.assertEqual("current_source_wins", result["conflicts"][0]["resolution"])
        self.assertTrue(all(card["usability"] == "do_not_use" for card in result["cards"]))

    def test_provider_unavailable_is_explicit_source_first_fallback(self):
        result = retrieve(None, provider_available=False)
        self.assertEqual("unavailable", result["outcome"])
        self.assertTrue(result["sourceFirst"])
        self.assertEqual("not_claimed", result["providerIntegration"])
        self.assertEqual([], result["cards"])

    def test_sensitive_or_under_scoped_request_is_invalid_instead_of_widened(self):
        sensitive = retrieve([lesson()], query="email user@example.com")
        missing_scope = retrieve_knowledge([lesson()], query="ReplayWorker", project_id="general", repository="backend", branch_or_tree="tree-current", access_scope=[])
        self.assertEqual("invalid", sensitive["outcome"])
        self.assertEqual("invalid", missing_scope["outcome"])

    def test_candidate_write_requires_permission_and_never_auto_verifies(self):
        denied = propose_lesson(lesson(), candidate_write_authorized=False)
        proposed = propose_lesson(lesson(), candidate_write_authorized=True)
        self.assertEqual("blocked", denied["outcome"])
        self.assertEqual("candidate", proposed["outcome"])
        self.assertEqual("candidate", proposed["candidate"]["status"])

    def test_candidate_deduplication_does_not_claim_a_new_write(self):
        candidate = lesson("rag-new", status="candidate")
        duplicate = find_duplicate_lesson([lesson()], candidate)
        result = propose_lesson(candidate, candidate_write_authorized=True, existing_records=[lesson()])
        self.assertEqual("rag-001", duplicate["recordId"])
        self.assertEqual("duplicate", result["outcome"])
        self.assertEqual("rag-001", result["duplicateOf"])
        self.assertEqual("none", result["learning"]["state"])

    def test_promotion_requires_authority_reviewer_and_valid_evidence(self):
        candidate = propose_lesson(lesson(), candidate_write_authorized=True)["candidate"]
        no_authority = promote_lesson(candidate, evidence=candidate["evidence"], reviewer="reviewer", promotion_authorized=False, now=NOW)
        no_evidence = promote_lesson(candidate, evidence=[], reviewer="reviewer", promotion_authorized=True, now=NOW)
        verified = promote_lesson(candidate, evidence=candidate["evidence"], reviewer="reviewer", promotion_authorized=True, now=NOW)
        self.assertEqual("blocked", no_authority["outcome"])
        self.assertEqual("needs_review", no_evidence["outcome"])
        self.assertEqual("verified", verified["outcome"])
        self.assertEqual("verified", verified["record"]["status"])

    def test_promotion_evidence_must_match_the_candidate_repository_and_tree(self):
        candidate = propose_lesson(lesson(), candidate_write_authorized=True)["candidate"]
        unrelated = deepcopy(candidate["evidence"])
        unrelated[0]["commitOrTree"] = "tree-other"
        result = promote_lesson(candidate, evidence=unrelated, reviewer="reviewer", promotion_authorized=True, now=NOW)
        self.assertEqual("needs_review", result["outcome"])

    def test_fetch_knowledge_authorizes_record_id_without_lexical_lookup(self):
        record = lesson("rag-private-id", claim="A claim without its stable identifier.")
        fetched = fetch_knowledge([record], record_id="rag-private-id", project_id="general", repository="backend", branch_or_tree="tree-current", access_scope=["project:general:read"])
        denied = fetch_knowledge([record], record_id="rag-private-id", project_id="general", repository="backend", branch_or_tree="tree-current", access_scope=["tenant:other:read"])
        self.assertEqual("matched", fetched["outcome"])
        self.assertEqual("rag-private-id", fetched["record"]["recordId"])
        self.assertEqual("denied_or_not_found", denied["outcome"])

    def test_feedback_invalidation_and_writeback_failures_are_honest(self):
        denied_feedback = record_knowledge_feedback(record_id="rag-001", outcome="helpful", evidence=[{}], feedback_authorized=False)
        invalid_feedback = record_knowledge_feedback(record_id="rag-001", outcome="helpful", evidence=[{"claim": "token=secret"}], feedback_authorized=True)
        failed_write = record_learning_writeback(state="failed", provider_acknowledged=False, record_id="rag-001")
        invalidated = invalidate_knowledge(lesson(), reason="current source disproves the lesson", supersedes=None, invalidation_authorized=True)
        self.assertEqual("denied", denied_feedback["outcome"])
        self.assertEqual("redacted", invalid_feedback["outcome"])
        self.assertEqual("blocked", failed_write["outcome"])
        self.assertEqual("blocked", failed_write["learning"]["state"])
        self.assertEqual(1, failed_write["metrics"]["rejectionCount"])
        self.assertEqual("needs_review", invalidated["outcome"])

    def test_learning_metrics_do_not_expose_record_content(self):
        metrics = learning_metrics(["candidate", "verified", "rejected", "blocked", "none"])
        self.assertEqual({
            "observed": 5,
            "candidate": 1,
            "promoted": 1,
            "needsReview": 0,
            "rejected": 1,
            "blocked": 1,
            "none": 1,
        }, metrics)

    def test_unauthorized_candidate_is_not_retrievable(self):
        candidate = lesson(status="candidate")
        result = retrieve([candidate])
        self.assertEqual("no_relevant_memory", result["outcome"])


if __name__ == "__main__":
    unittest.main()
