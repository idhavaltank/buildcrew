"""Runtime coordinator for configured native agents and model providers.

This module is the executable runtime layer above the source contracts. It
constructs the existing bridges, owns the registered provider set, selects a
provider that can satisfy an already-bound assignment, and delegates through
the bounded host-execution transports. It does not choose a model, read
credentials, or turn an unavailable provider into a fake success.
"""

from __future__ import annotations

from typing import Any, Mapping

from .host_execution import (
    probe_host_execution_manifest,
    SubprocessNativeAgentTransport,
    SubprocessProviderTransport,
    validate_host_execution_manifest,
)
from .model_selection import validate_assignment
from .native_agent_bridge import NativeAgentBridge, unavailable_native_manifest, validate_native_manifest
from .provider_bridge import ModelProviderBridge, unavailable_provider_manifest, validate_provider_manifest


CONFIG_SCHEMA_VERSION = "runtime-coordinator-config-v1"
STATUS_SCHEMA_VERSION = "runtime-coordinator-status-v1"


class RuntimeCoordinatorError(ValueError):
    """Raised when runtime configuration or ownership bindings are invalid."""


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _execution_validation(value: Any, *, operation: str, label: str, host_id: str) -> list[str]:
    if value is None:
        return []
    result = validate_host_execution_manifest(value)
    errors = [f"{label}.{item}" for item in result["errors"]]
    if result["valid"]:
        if value.get("operation") != operation:
            errors.append(f"{label}.operation: expected {operation}")
        if value.get("hostId") != host_id:
            errors.append(f"{label}.hostId: must match runtime hostId")
    return errors


def validate_runtime_coordinator_config(value: Any) -> dict[str, Any]:
    """Validate the exact config used to construct the runtime coordinator."""

    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["runtimeCoordinatorConfig: expected object"]}
    required = {
        "schemaVersion",
        "runtimeId",
        "hostId",
        "nativeAgent",
        "providers",
        "sourceBound",
        "externalSideEffects",
    }
    errors: list[str] = []
    errors.extend(f"runtimeCoordinatorConfig: missing required field {key}" for key in sorted(required - set(value)))
    errors.extend(f"runtimeCoordinatorConfig: unknown field {key}" for key in sorted(set(value) - required))
    if value.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        errors.append(f"runtimeCoordinatorConfig.schemaVersion: expected {CONFIG_SCHEMA_VERSION}")
    for key in ("runtimeId", "hostId"):
        if not _non_empty(value.get(key)):
            errors.append(f"runtimeCoordinatorConfig.{key}: expected non-empty string")
    if value.get("sourceBound") is not True:
        errors.append("runtimeCoordinatorConfig.sourceBound: must be true")
    if value.get("externalSideEffects") != "HOST_CONTROLLED":
        errors.append("runtimeCoordinatorConfig.externalSideEffects: must be HOST_CONTROLLED")

    host_id = str(value.get("hostId", ""))
    native = value.get("nativeAgent")
    if not isinstance(native, Mapping) or set(native) != {"bridgeManifest", "executionManifest"}:
        errors.append("runtimeCoordinatorConfig.nativeAgent: expected bridgeManifest/executionManifest")
    else:
        bridge_result = validate_native_manifest(native.get("bridgeManifest"))
        errors.extend(f"runtimeCoordinatorConfig.nativeAgent.bridgeManifest.{item}" for item in bridge_result["errors"])
        if bridge_result["valid"] and native["bridgeManifest"].get("hostId") != host_id:
            errors.append("runtimeCoordinatorConfig.nativeAgent.bridgeManifest.hostId: must match runtime hostId")
        errors.extend(
            _execution_validation(
                native.get("executionManifest"),
                operation="native-spawn",
                label="runtimeCoordinatorConfig.nativeAgent.executionManifest",
                host_id=host_id,
            )
        )

    providers = value.get("providers")
    if not isinstance(providers, list) or len(providers) > 16:
        errors.append("runtimeCoordinatorConfig.providers: expected at most 16 provider entries")
    else:
        provider_ids: set[str] = set()
        for index, item in enumerate(providers):
            label = f"runtimeCoordinatorConfig.providers[{index}]"
            if not isinstance(item, Mapping) or set(item) != {"providerManifest", "executionManifest"}:
                errors.append(f"{label}: expected providerManifest/executionManifest")
                continue
            provider_result = validate_provider_manifest(item.get("providerManifest"))
            errors.extend(f"{label}.providerManifest.{error}" for error in provider_result["errors"])
            if provider_result["valid"]:
                provider_id = str(item["providerManifest"]["providerId"])
                if provider_id in provider_ids:
                    errors.append(f"{label}.providerManifest.providerId: duplicate provider")
                provider_ids.add(provider_id)
                if item["providerManifest"].get("hostId") != host_id:
                    errors.append(f"{label}.providerManifest.hostId: must match runtime hostId")
            errors.extend(
                _execution_validation(
                    item.get("executionManifest"),
                    operation="provider-execute",
                    label=f"{label}.executionManifest",
                    host_id=host_id,
                )
            )
    return {"valid": not errors, "errors": errors}


def _execution_status(manifest: Mapping[str, Any] | None) -> dict[str, Any]:
    if manifest is None:
        return {
            "status": "UNAVAILABLE",
            "proof": "no execution adapter was configured",
            "blockers": ["execution adapter is not configured"],
        }
    return probe_host_execution_manifest(manifest)


class RuntimeCoordinator:
    """Construct and run the configured native-agent/provider runtime."""

    def __init__(self, config: Mapping[str, Any]):
        validation = validate_runtime_coordinator_config(config)
        if not validation["valid"]:
            raise RuntimeCoordinatorError("invalid runtime coordinator config: " + "; ".join(validation["errors"]))
        self.config = dict(config)
        self.runtime_id = str(config["runtimeId"])
        self.host_id = str(config["hostId"])

        native_spec = config["nativeAgent"]
        native_transport = (
            SubprocessNativeAgentTransport(native_spec["executionManifest"])
            if native_spec["executionManifest"] is not None
            else None
        )
        self.native_agent = NativeAgentBridge(native_spec["bridgeManifest"], native_transport)

        self.providers: dict[str, ModelProviderBridge] = {}
        for provider_spec in config["providers"]:
            provider_manifest = provider_spec["providerManifest"]
            provider_transport = (
                SubprocessProviderTransport(provider_spec["executionManifest"])
                if provider_spec["executionManifest"] is not None
                else None
            )
            provider_id = str(provider_manifest["providerId"])
            self.providers[provider_id] = ModelProviderBridge(provider_manifest, provider_transport)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "RuntimeCoordinator":
        return cls(config)

    def status(self) -> dict[str, Any]:
        native_spec = self.config["nativeAgent"]
        native_execution = _execution_status(native_spec["executionManifest"])
        native_available = (
            self.native_agent.manifest["status"] == "AVAILABLE"
            and native_execution["status"] == "AVAILABLE"
        )
        provider_statuses: list[dict[str, Any]] = []
        for provider_spec in self.config["providers"]:
            provider_id = str(provider_spec["providerManifest"]["providerId"])
            provider = self.providers[provider_id]
            catalog = provider.catalog()
            execution = _execution_status(provider_spec["executionManifest"])
            available = catalog["status"] == "AVAILABLE" and execution["status"] == "AVAILABLE"
            provider_statuses.append(
                {
                    "providerId": provider_id,
                    "status": "AVAILABLE" if available else "CAPABILITY_UNAVAILABLE",
                    "catalog": catalog["status"],
                    "execution": execution["status"],
                    "blockers": list(catalog.get("blockers", [])) + list(execution.get("blockers", [])),
                }
            )
        overall_available = native_available or any(
            item["status"] == "AVAILABLE" for item in provider_statuses
        )
        return {
            "schemaVersion": STATUS_SCHEMA_VERSION,
            "runtimeId": self.runtime_id,
            "hostId": self.host_id,
            "status": "AVAILABLE" if overall_available else "CAPABILITY_UNAVAILABLE",
            "nativeAgent": {
                "status": "AVAILABLE" if native_available else "CAPABILITY_UNAVAILABLE",
                "bridge": self.native_agent.manifest["status"],
                "execution": native_execution["status"],
                "blockers": list(native_execution.get("blockers", [])),
            },
            "providers": provider_statuses,
            "sideEffects": [],
        }

    def spawn_agent(
        self,
        assignment: Mapping[str, Any],
        slot: Mapping[str, Any],
        *,
        expected_policy: Mapping[str, Any] | None = None,
        expected_policy_fingerprint: str | None = None,
        expected_binding: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        self._require_host(assignment)
        return self.native_agent.spawn(
            assignment,
            slot,
            expected_policy=expected_policy,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            now=now,
        )

    def execute_provider(
        self,
        assignment: Mapping[str, Any],
        *,
        expected_policy: Mapping[str, Any] | None = None,
        expected_policy_fingerprint: str | None = None,
        expected_binding: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        self._require_host(assignment)
        validation = validate_assignment(
            assignment,
            expected_policy=expected_policy,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            now=now,
        )
        if not validation["valid"]:
            fallback = ModelProviderBridge(unavailable_provider_manifest(host_id=self.host_id))
            return fallback.execute(
                assignment,
                expected_policy=expected_policy,
                expected_policy_fingerprint=expected_policy_fingerprint,
                expected_binding=expected_binding,
                now=now,
            )

        model = assignment.get("resolvedModel")
        effort = assignment.get("reasoningEffort")
        for provider in self.providers.values():
            catalog = provider.catalog()
            models = (
                catalog.get("catalog", {}).get("availableModels", {})
                if isinstance(catalog.get("catalog"), Mapping)
                else {}
            )
            if catalog["status"] == "AVAILABLE" and effort in models.get(model, []):
                return provider.execute(
                    assignment,
                    expected_policy=expected_policy,
                    expected_policy_fingerprint=expected_policy_fingerprint,
                    expected_binding=expected_binding,
                    now=now,
                )

        if self.providers:
            provider = next(iter(self.providers.values()))
            return provider.execute(
                assignment,
                expected_policy=expected_policy,
                expected_policy_fingerprint=expected_policy_fingerprint,
                expected_binding=expected_binding,
                now=now,
            )
        fallback = ModelProviderBridge(unavailable_provider_manifest(host_id=self.host_id))
        return fallback.execute(
            assignment,
            expected_policy=expected_policy,
            expected_policy_fingerprint=expected_policy_fingerprint,
            expected_binding=expected_binding,
            now=now,
        )

    def _require_host(self, assignment: Mapping[str, Any]) -> None:
        if not isinstance(assignment, Mapping) or assignment.get("hostId") != self.host_id:
            raise RuntimeCoordinatorError("assignment hostId must match runtime coordinator hostId")


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "STATUS_SCHEMA_VERSION",
    "RuntimeCoordinator",
    "RuntimeCoordinatorError",
    "validate_runtime_coordinator_config",
]
