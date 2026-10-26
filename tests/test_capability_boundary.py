import json
import re
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PACKAGE_ROOT / "schemas" / "capability-manifest-v1.schema.json"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "capabilities"
SPECIALIST_CATALOG_PATH = PACKAGE_ROOT / "references" / "specialist-catalog.md"
HARNESS_BOUNDARY_PATH = PACKAGE_ROOT / "references" / "harness-capability-boundary.md"

ROLE_IDS = {
    "knowledge",
    "explorer",
    "root-cause",
    "regression",
    "reviewer",
    "testing",
    "verification",
    "writer",
}

CAPABILITY_IDS = {
    "repository-documentation",
    "lint-architecture",
    "ci-gates",
    "observability",
    "specialist-profiles",
    "quality-technical-debt",
}

FORBIDDEN_KEYS = {
    "route",
    "routing",
    "model",
    "modelSelection",
    "lifecycleClosure",
    "stateRoot",
    "stateRoots",
    "credential",
    "credentials",
    "secret",
    "prompt",
    "prompts",
    "instructionUrl",
    "callback",
    "callbacks",
    "command",
    "commands",
    "executable",
    "executableContent",
    "shell",
    "script",
}

SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def load_json(path):
    return json.loads(path.read_text())


def walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


def _manual_schema_errors(document):
    """Small fallback for environments without jsonschema.

    The package stays dependency-free; when jsonschema is installed the tests
    use the real Draft 2020-12 validator below and this fallback still checks
    the contract's security-critical shape.
    """
    errors = []
    if not isinstance(document, dict):
        return ["manifest must be an object"]

    root_required = {"schemaVersion", "registry", "authority", "capabilities"}
    missing = root_required - document.keys()
    if missing:
        errors.append(f"missing root fields: {sorted(missing)}")
    if set(document) - root_required:
        errors.append("unknown root field")
    if document.get("schemaVersion") != "capability-manifest-v1":
        errors.append("wrong schema version")

    registry = document.get("registry")
    if not isinstance(registry, dict):
        errors.append("registry must be an object")
    else:
        required = {
            "registryId",
            "scope",
            "canonical",
            "owner",
            "version",
            "fingerprint",
            "enabledByDefault",
        }
        if required - registry.keys():
            errors.append("registry is missing required fields")
        if set(registry) - required:
            errors.append("unknown registry field")
        if registry.get("canonical") is not True:
            errors.append("registry must be canonical")
        if registry.get("enabledByDefault") is not False:
            errors.append("registry must be disabled by default")

    authority = document.get("authority")
    authority_required = {
        "mode",
        "controller",
        "canRoute",
        "canSelectModel",
        "canGrantWrite",
        "canCloseLifecycle",
        "canCreateState",
        "canAccessSecrets",
        "canFetchInstructions",
        "canInvokeCallbacks",
        "canRunArbitraryCode",
    }
    if not isinstance(authority, dict) or set(authority) != authority_required:
        errors.append("authority declaration is not exact")
    elif authority.get("mode") != "NON_AUTHORITATIVE" or authority.get("controller") != "AE":
        errors.append("authority declaration is not AE-owned")
    elif any(authority.get(field) is not False for field in authority_required - {"mode", "controller"}):
        errors.append("capability boundary grants authority")

    capabilities = document.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("capabilities must be a non-empty array")
        return errors

    for capability in capabilities:
        if not isinstance(capability, dict):
            errors.append("capability must be an object")
            continue
        required = {
            "capabilityId",
            "provider",
            "owner",
            "version",
            "fingerprint",
            "availability",
            "enabledByDefault",
            "status",
            "taskCategories",
            "readWriteClass",
            "scope",
            "evidenceRequirements",
            "purpose",
        }
        if required - capability.keys():
            errors.append("capability is missing required fields")
        if set(capability) - required:
            errors.append("unknown capability field")
        if capability.get("capabilityId") not in CAPABILITY_IDS:
            errors.append("unknown capability id")
        if capability.get("provider") != "Harness" or capability.get("owner") != "Harness":
            errors.append("capability owner/provider must be Harness")
        if capability.get("enabledByDefault") is not False:
            errors.append("capability must be disabled by default")
        if not SEMVER.match(str(capability.get("version", ""))):
            errors.append("invalid capability version")
        if not SHA256.match(str(capability.get("fingerprint", ""))):
            errors.append("invalid capability fingerprint")
        status = capability.get("status")
        if not isinstance(status, dict) or set(status) != {"documented", "installed", "runnable", "executed"}:
            errors.append("status is not exact")
        scope = capability.get("scope")
        if not isinstance(scope, dict):
            errors.append("scope must be an object")
        else:
            scope_required = {
                "scopeClass",
                "maxRepositories",
                "maxPaths",
                "allowedPathClasses",
                "requiresAEGrant",
                "externalSideEffects",
            }
            if set(scope) != scope_required:
                errors.append("scope is not exact")
            if scope.get("requiresAEGrant") is not True or scope.get("externalSideEffects") is not False:
                errors.append("scope grants unsafe behavior")
        if not isinstance(capability.get("taskCategories"), list) or not capability["taskCategories"]:
            errors.append("task categories must be non-empty")
        if not isinstance(capability.get("evidenceRequirements"), list) or not capability["evidenceRequirements"]:
            errors.append("evidence requirements must be non-empty")
    return errors


def schema_errors(document):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return _manual_schema_errors(document)

    schema = load_json(SCHEMA_PATH)
    validator = Draft202012Validator(schema)
    return [error.message for error in sorted(validator.iter_errors(document), key=lambda error: list(error.path))]


def inspect_manifest(document, expected_fingerprints=None):
    errors = schema_errors(document)
    if errors:
        return {"valid": False, "eligible": False, "errors": errors}

    seen = set()
    for capability in document["capabilities"]:
        capability_id = capability["capabilityId"]
        if capability_id in seen:
            errors.append(f"duplicate capability id: {capability_id}")
        seen.add(capability_id)

        if expected_fingerprints and capability_id in expected_fingerprints:
            expected = expected_fingerprints[capability_id]
            if capability["fingerprint"] != expected:
                errors.append(f"stale capability fingerprint: {capability_id}")

        if capability["availability"] in {"DUPLICATE", "STALE", "INVALID"}:
            errors.append(f"rejected capability state: {capability_id}")

    if set(walk_keys(document)) & FORBIDDEN_KEYS:
        errors.append("manifest contains an authority-bearing field")
    if any(isinstance(value, str) and re.search(r"(?:https?://|ftp://|javascript:|data:)", value, re.I) for value in _walk_values(document)):
        errors.append("manifest contains an instruction-fetch URL")

    return {"valid": not errors, "eligible": not errors and all(
        capability["availability"] == "AVAILABLE" for capability in document["capabilities"]
    ), "errors": errors}


def _walk_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    else:
        yield value


class CapabilityBoundaryTests(unittest.TestCase):
    def test_schema_is_strict_and_declares_the_non_authoritative_boundary(self):
        schema = load_json(SCHEMA_PATH)
        self.assertEqual("capability-manifest-v1", schema["properties"]["schemaVersion"]["const"])
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("registry", schema["required"])
        self.assertIn("authority", schema["required"])
        self.assertIn("capabilities", schema["required"])
        authority = schema["$defs"]["authority"]["properties"]
        self.assertEqual("NON_AUTHORITATIVE", authority["mode"]["const"])
        self.assertEqual("AE", authority["controller"]["const"])
        for field in (
            "canRoute",
            "canSelectModel",
            "canGrantWrite",
            "canCloseLifecycle",
            "canCreateState",
            "canAccessSecrets",
            "canFetchInstructions",
            "canInvokeCallbacks",
            "canRunArbitraryCode",
        ):
            self.assertIs(authority[field]["const"], False, field)

    def test_valid_manifest_declares_all_required_capability_metadata(self):
        manifest = load_json(FIXTURE_ROOT / "valid.json")
        result = inspect_manifest(manifest)
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(result["eligible"], result["errors"])
        self.assertEqual(CAPABILITY_IDS, {item["capabilityId"] for item in manifest["capabilities"]})
        for capability in manifest["capabilities"]:
            for field in (
                "owner",
                "version",
                "fingerprint",
                "taskCategories",
                "readWriteClass",
                "scope",
                "evidenceRequirements",
                "status",
            ):
                self.assertIn(field, capability)

    def test_unavailable_capability_is_explicit_and_not_simulated(self):
        manifest = load_json(FIXTURE_ROOT / "unavailable.json")
        result = inspect_manifest(manifest)
        self.assertTrue(result["valid"], result["errors"])
        self.assertFalse(result["eligible"])
        capability = manifest["capabilities"][0]
        self.assertEqual("UNAVAILABLE", capability["availability"])
        self.assertIn("UNAVAILABLE", capability["status"].values())

    def test_disabled_by_default_and_one_canonical_registry_are_required(self):
        manifest = load_json(FIXTURE_ROOT / "valid.json")
        self.assertTrue(manifest["registry"]["canonical"])
        self.assertFalse(manifest["registry"]["enabledByDefault"])
        self.assertTrue(all(not item["enabledByDefault"] for item in manifest["capabilities"]))
        self.assertFalse(inspect_manifest(load_json(FIXTURE_ROOT / "enabled-by-default.json"))["valid"])
        self.assertFalse(inspect_manifest(load_json(FIXTURE_ROOT / "second-registry.json"))["valid"])

    def test_duplicate_capability_ids_are_rejected_before_selection(self):
        result = inspect_manifest(load_json(FIXTURE_ROOT / "duplicate-id.json"))
        self.assertFalse(result["valid"])
        self.assertTrue(any("duplicate capability id" in error for error in result["errors"]))

    def test_stale_capability_fingerprints_are_rejected_before_selection(self):
        manifest = load_json(FIXTURE_ROOT / "stale.json")
        capability = manifest["capabilities"][0]
        result = inspect_manifest(
            manifest,
            expected_fingerprints={capability["capabilityId"]: "sha256:" + "a" * 64},
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("stale capability fingerprint" in error for error in result["errors"]))

    def test_forbidden_authority_content_is_rejected(self):
        result = inspect_manifest(load_json(FIXTURE_ROOT / "forbidden-authority.json"))
        self.assertFalse(result["valid"])
        self.assertTrue(result["errors"])

    def test_eight_ae_roles_are_documented_as_contracts_not_agents(self):
        text = SPECIALIST_CATALOG_PATH.read_text()
        self.assertIn("contract, not a real agent definition", text)
        for role_id in sorted(ROLE_IDS):
            self.assertIn(f"`{role_id}`", text)
        for term in ("Allowed task types", "Default read/write authority", "Maximum scope", "Non-goals", "Evidence format", "Validation expectations", "Stop conditions"):
            self.assertIn(term, text)

    def test_harness_boundary_documents_all_six_optional_capabilities(self):
        text = HARNESS_BOUNDARY_PATH.read_text()
        self.assertIn("optional capability provider", text)
        self.assertIn("CANDIDATE_UNAVAILABLE", text)
        for capability_id in sorted(CAPABILITY_IDS):
            self.assertIn(f"`{capability_id}`", text)
        for term in (
            "documented",
            "installed",
            "runnable",
            "executed",
            "duplicate",
            "stale",
            "one canonical registry",
            "disabled by default",
            "no routing",
            "no model selection",
            "no lifecycle closure",
            "no state roots",
            "no credentials",
            "no prompts",
            "no callbacks",
        ):
            self.assertIn(term, text)


if __name__ == "__main__":
    unittest.main()
