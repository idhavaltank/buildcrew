from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.activation import CandidateActivationController, validate_activation_receipt  # noqa: E402
from engine.context_gate import enforce_project_context_gate, validate_enforced_gate  # noqa: E402
from engine.evidence_observability import build_event  # noqa: E402
from engine.installation import InstallationController, validate_installation_receipt  # noqa: E402
from engine.model_selection import select_model_assignments  # noqa: E402
from engine.native_agent_bridge import NativeAgentBridge, unavailable_native_manifest  # noqa: E402
from engine.observability_store import JsonlEvidenceSink, ObservabilityController  # noqa: E402
from engine.provider_bridge import ModelProviderBridge, unavailable_provider_manifest  # noqa: E402
from engine.qa_adapters import AndroidQAAdapter, BrowserQAAdapter, unavailable_manifest  # noqa: E402
from engine.qa_runner import AdapterRegistry, run_qa_workflow, validate_qa_runtime_run  # noqa: E402
from engine.rag_bridge import RAGProviderBridge, unavailable_rag_manifest  # noqa: E402
from engine.project_context import discover_project_context  # noqa: E402
from engine.policy_resolver import resolve_policy  # noqa: E402


class BrowserTransport:
    def probe(self, request):
        return {"status": "AVAILABLE", "runtimeIdentity": request["targetIdentity"]}

    def run(self, request):
        return {
            "status": "PASS",
            "targetIdentity": request["targetIdentity"],
            "stepResults": [{"stepId": step_id, "status": "PASS", "evidenceRefs": [f"browser:{step_id}"]} for step_id in request["stepIds"]],
            "artifactRefs": ["artifact:browser:run"],
            "blockers": [],
        }


class ProviderTransport:
    def execute(self, request):
        return {"status": "EXECUTED", "model": request["model"], "checkpoint": {"sequence": 1, "state": "FINAL"}, "evidenceRefs": ["provider:execution"]}


class NativeTransport:
    def spawn(self, request):
        return {"status": "STARTED", "checkpoints": ["native:start"], "blockers": []}


class RAGTransport:
    def retrieve(self, request):
        return {"status": "RETRIEVED", "records": [{"recordId": "rag-1", "freshness": "current"}], "blockers": []}

    def writeback(self, request):
        return {"status": "ACKNOWLEDGED", "acknowledged": True, "blockers": []}


class ActivationHost:
    def snapshot(self, candidate):
        return {"active": "v1"}

    def canary(self, candidate, observation):
        return {"status": "PASS"}

    def activate(self, candidate):
        return {"status": "PASS"}

    def rollback(self, snapshot):
        return {"status": "PASS"}

    def retire(self, candidate):
        return {"status": "PASS"}


class InstallationHost:
    def stage(self, request):
        return {"status": "PASS"}

    def switch(self, request):
        return {"status": "PASS", "oneController": True}

    def rollback(self, request):
        return {"status": "PASS"}

    def retire(self, request):
        return {"status": "PASS"}


def qa_inputs():
    source = "a" * 64
    workflow = {
        "schemaVersion": "qa-workflow-map-v1",
        "workflowId": "workflow-1",
        "requestId": "request-1",
        "taskId": "task-1",
        "sourceFingerprint": source,
        "objective": "Run the approved smoke workflow",
        "steps": [{"stepId": "web-open", "title": "Open web", "target": "WEB", "entryPoint": "/", "action": "open", "expected": "page visible", "evidenceRequired": ["screenshot"]}],
        "unresolved": [],
        "status": "READY",
        "generatedAt": "2026-08-25T10:00:00Z",
    }
    packet = {
        "schemaVersion": "qa-execution-packet-v1",
        "packetId": "packet-1",
        "requestId": "request-1",
        "workflowId": "workflow-1",
        "taskId": "task-1",
        "sourceFingerprint": source,
        "targetAllowlist": {"repository": "repo-1", "webTargets": ["web-target"], "androidTargets": [], "testDataScope": ["synthetic"]},
        "requiredCapabilities": ["browser"],
        "requiredEvidence": ["screenshot"],
        "stepIds": ["web-open"],
        "timeoutSeconds": 30,
        "isolation": {"externalState": "DISPOSABLE_ONLY", "network": "ALLOWLISTED", "credentials": "NONE"},
        "mode": "REQUIRED",
        "status": "READY",
        "issuedAt": "2026-08-25T10:00:00Z",
        "expiresAt": "2026-08-25T11:00:00Z",
    }
    return workflow, packet


class HostIntegrationTests(unittest.TestCase):
    def test_unavailable_browser_and_android_are_explicit_and_runner_is_fail_closed(self):
        workflow, packet = qa_inputs()
        registry = AdapterRegistry()
        registry.register(BrowserQAAdapter(unavailable_manifest(adapter_id="browser", kind="browser")))
        registry.register(AndroidQAAdapter(unavailable_manifest(adapter_id="android", kind="android_emulator")))
        result = run_qa_workflow(workflow, packet, registry)
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["live"])
        self.assertTrue(validate_qa_runtime_run(result)["valid"], validate_qa_runtime_run(result)["errors"])
        self.assertEqual("BLOCKED", result["qaReceipt"]["status"])

    def test_flutter_workflow_requires_a_matching_registered_adapter(self):
        workflow, packet = qa_inputs()
        workflow["steps"][0]["target"] = "FLUTTER"
        android = unavailable_manifest(adapter_id="android", kind="android_emulator")
        android.update({
            "status": "AVAILABLE",
            "version": "1.0.0",
            "capabilities": ["launch", "screenshot", "evidence"],
            "targetAllowlist": ["android_emulator-target"],
        })
        registry = AdapterRegistry()
        registry.register(AndroidQAAdapter(android, BrowserTransport()))
        result = run_qa_workflow(workflow, packet, registry)
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["live"])
        self.assertEqual([], result["adapterReceipts"])
        self.assertTrue(any("no registered adapter for flutter" in blocker for blocker in result["blockers"]))

    def test_injected_browser_transport_can_produce_host_bound_receipt(self):
        workflow, packet = qa_inputs()
        registry = AdapterRegistry()
        manifest = unavailable_manifest(adapter_id="browser", kind="browser")
        manifest.update({"status": "AVAILABLE", "version": "1.0.0", "capabilities": ["launch", "navigate", "evidence"], "targetAllowlist": ["web-target"]})
        registry.register(BrowserQAAdapter(manifest, BrowserTransport()))
        registry.register(AndroidQAAdapter(unavailable_manifest(adapter_id="android", kind="android_emulator")))
        result = run_qa_workflow(workflow, packet, registry)
        self.assertEqual("PASS", result["status"])
        self.assertTrue(result["live"])
        self.assertEqual("HOST_BOUND", result["adapterReceipts"][0]["hostExecution"]["proofLevel"])

    def test_context_gate_allows_valid_sidecars_and_blocks_changed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"dependencies": {"react": "18.0.0"}}), encoding="utf-8")
            discovered = discover_project_context(root, targets=["."], task_type="NEW_TASK", acceptance_criteria=["Search works"])
            context = discovered["contextMap"]
            policy = resolve_policy({"requestType": "NEW_TASK", "targetPaths": ["."], "acceptanceCriteria": ["Search works"]}, context)
            intake = {
                "taskId": "task-context",
                "repositoryId": context["repository"]["repositoryId"],
                "sourceFingerprint": context["fingerprints"]["sourceFingerprint"],
                "contextFingerprint": context["fingerprints"]["contextFingerprint"],
                "taskScopeFingerprint": context["fingerprints"]["taskScopeFingerprint"],
                "requestType": "NEW_TASK",
                "normalizedObjective": "Validate context",
                "targetPaths": ["."],
                "acceptanceCriteria": ["Search works"],
            }
            allowed = enforce_project_context_gate(intake, context, discovered["receipt"], policy)
            self.assertEqual("ALLOW", allowed["decision"])
            self.assertTrue(validate_enforced_gate(allowed)["valid"], validate_enforced_gate(allowed)["errors"])
            changed = dict(intake, sourceFingerprint="b" * 64)
            blocked = enforce_project_context_gate(changed, context, discovered["receipt"], policy)
            self.assertIn(blocked["decision"], {"BLOCKED", "INVALID"})

    def test_provider_and_native_bridges_reject_missing_host_without_silent_model_promotion(self):
        fixture = json.loads((PACKAGE_ROOT / "tests/fixtures/model-selection/normal-feature.json").read_text(encoding="utf-8"))
        policy = json.loads((PACKAGE_ROOT / "config/model-selection-v1.json").read_text(encoding="utf-8"))
        selection = select_model_assignments(fixture["taskFacts"], fixture["hostCatalog"], policy)
        worker = next(item for item in selection["assignments"] if item["role"] == "worker")
        unavailable = ModelProviderBridge(unavailable_provider_manifest())
        result = unavailable.execute(worker, expected_policy=policy)
        self.assertEqual("CAPABILITY_UNAVAILABLE", result["status"])
        self.assertFalse(result["receipt"]["live"])
        manifest = unavailable_provider_manifest(provider_id="provider", host_id=worker["hostId"])
        manifest.update({"status": "AVAILABLE", "authState": "AUTHENTICATED", "version": "1.0.0"})
        manifest["models"] = [{"modelId": model, "reasoningEfforts": efforts, "status": "AVAILABLE"} for model, efforts in fixture["hostCatalog"]["availableModels"].items()]
        executed = ModelProviderBridge(manifest, ProviderTransport()).execute(worker, expected_policy=policy)
        self.assertEqual("EXECUTED", executed["status"])
        native_manifest = unavailable_native_manifest(bridge_id="native", host_id=worker["hostId"])
        native_manifest.update({"status": "AVAILABLE", "version": "1.0.0"})
        slot = {"canonicalName": "Skill Engineer", "owner": "AE", "controller": "AE", "role": "worker", "scope": {"scopeId": worker["scope"]["scopeId"], "paths": ["src/feature.py"], "authority": "AE"}, "resourceLimit": {"maxSeconds": 120, "maxOutputTokens": 1000}, "stopCondition": "receipt", "taskId": worker["taskId"], "sourceFingerprint": worker["sourceFingerprint"]}
        native = NativeAgentBridge(native_manifest, NativeTransport()).spawn(worker, slot, expected_policy=policy)
        self.assertEqual("STARTED", native["status"])

    def test_rag_bridge_preserves_source_first_and_requires_explicit_writeback(self):
        request = {"taskId": "task-rag", "sourceFingerprint": "a" * 64, "projectId": "project", "repository": "repo", "branchOrTree": "tree", "accessScope": ["project:project:read"], "tenantId": "tenant", "query": "bounded lookup", "taskType": "bug"}
        unavailable = RAGProviderBridge(unavailable_rag_manifest())
        result = unavailable.retrieve(request)
        self.assertEqual("unavailable", result["outcome"])
        self.assertFalse(result["receipt"]["live"])
        manifest = unavailable_rag_manifest(provider_id="rag", host_id="host")
        manifest.update({"status": "AVAILABLE", "authState": "AUTHENTICATED", "version": "1.0.0"})
        bridge = RAGProviderBridge(manifest, RAGTransport())
        retrieved = bridge.retrieve(request)
        self.assertEqual("matched", retrieved["outcome"])
        denied = bridge.writeback(request, authorized=False)
        self.assertEqual("blocked", denied["outcome"])
        acknowledged = bridge.writeback(request, authorized=True)
        self.assertEqual("acknowledged", acknowledged["outcome"])

    def test_activation_installation_and_observability_have_explicit_host_boundaries(self):
        candidate = {"candidateId": "v2-candidate", "sourceFingerprint": "a" * 64}
        unavailable = CandidateActivationController().activate(candidate, approval=None)
        self.assertEqual("CAPABILITY_UNAVAILABLE", unavailable["status"])
        self.assertTrue(validate_activation_receipt(unavailable["receipt"])["valid"])
        approval = {"approvalId": "approval-1", "approvedBy": "owner", "scope": "ACTIVE_SKILL_HOST", "canaryWindow": "10m", "rollbackTrigger": "error-rate"}
        activated = CandidateActivationController(ActivationHost()).activate(candidate, approval=approval)
        self.assertEqual("ACTIVATED", activated["status"])
        with tempfile.TemporaryDirectory() as directory:
            install = InstallationController(InstallationHost(), protected_roots=[PACKAGE_ROOT]).install(candidate, target_root=directory, platform="linux", approval={"approvalId": "approval-1", "approvedBy": "owner", "scope": "ACTIVE_SKILL_HOST"})
            self.assertEqual("INSTALLED", install["status"])
            self.assertTrue(validate_installation_receipt(install["receipt"])["valid"])
            event = build_event("TASK", "task-observe", "PASS", source_fingerprint="a" * 64, details={"check": "safe"}, sequence=0)
            controller = ObservabilityController(JsonlEvidenceSink(directory, protected_roots=[PACKAGE_ROOT]))
            first = controller.publish(event)
            second = controller.publish(event)
            self.assertEqual("PERSISTED", first["status"])
            self.assertEqual("DEDUPLICATED", second["status"])
            self.assertEqual("PASS", controller.recovery()["status"])


if __name__ == "__main__":
    unittest.main()
