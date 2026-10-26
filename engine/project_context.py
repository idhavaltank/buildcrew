#!/usr/bin/env python3
"""Read-only project-context discovery for general evidence-driven agentic engineering.

The module deliberately returns plain dictionaries so the CLI, schemas, and
host controller can share one stable boundary without adding dependencies.
Discovery never executes project commands and never writes to the repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

SCHEMA_VERSION = "project-context-map-v1"
RECEIPT_SCHEMA_VERSION = "context-discovery-receipt-v1"
MANIFEST_SCHEMA_VERSION = "project-context-manifest-v1"
DISCOVERY_VERSION = "project-context-bootstrap-v1"
POLICY_CATALOG_VERSION = "project-context-policy-v1"

MAX_CANDIDATES = 5000
MAX_CANDIDATE_LIMIT = 50000
FAST_MAX_CANDIDATES = 1000
MAX_CONTENT_BYTES = 2_000_000
MAX_SOURCE_FINGERPRINT_BYTES = 1_000_000

EXCLUDED_DIRECTORIES = {
    ".git",
    ".codex",
    ".claude",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".cache",
    "logs",
    "session-state",
    ".codex-session-state",
    "generated",
    "target",
}

INSTRUCTION_NAMES = {"AGENTS.md", "AGENT.md", "CLAUDE.md"}
MANIFEST_NAMES = {
    "package.json",
    "pubspec.yaml",
    "pubspec.yml",
    "pyproject.toml",
    "Cargo.toml",
    "Makefile",
    "project-context.yaml",
    "project-context.yml",
    "project-context.json",
    "platform-profile.yaml",
    "platform-profile.yml",
}
ROOT_CONTEXT_NAMES = {
    "README.md",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "BEST_PRACTICES.md",
    "TESTING_GUIDE.md",
    "ENGINEERING.md",
}
TEST_CONFIG_NAMES = {
    "jest.config.js",
    "jest.config.ts",
    "vitest.config.ts",
    "playwright.config.ts",
    "pytest.ini",
    "tox.ini",
    "analysis_options.yaml",
}

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|password|dsn)\s*[:=]\s*[^\s#]{8,}"),
    re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(?:aws_access_key_id|aws_secret_access_key)\s*[:=]\s*[^\s#]{8,}"),
)

INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(?:all\s+)?previous\s+instructions"),
    re.compile(r"(?i)reveal\s+(?:secrets|tokens|credentials)"),
    re.compile(r"(?i)run\s+(?:this|the following)\s+command\s+as\s+root"),
)


class ContextDiscoveryError(ValueError):
    """Raised when discovery cannot safely resolve its boundary."""


def _excluded_directories() -> set[str]:
    """Combine built-in private directories with host-declared runtime names."""

    names = set(EXCLUDED_DIRECTORIES)
    configured = os.environ.get("BUILDCREW_CONTEXT_EXCLUDED_DIRS", "")
    for value in configured.split(os.pathsep):
        name = value.strip()
        if not name:
            continue
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise ContextDiscoveryError("context excluded directories must be directory names")
        names.add(name)
    return names


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def default_protected_roots() -> list[Path]:
    """Return active-skill and host-configured protected boundaries."""

    values = [Path(__file__).resolve().parents[1]]
    for name in ("BUILDCREW_PROTECTED_ROOTS", "AUTONOMOUS_ENGINEERING_PROTECTED_ROOTS"):
        configured = os.environ.get(name, "")
        values.extend(Path(item).expanduser().resolve(strict=False) for item in configured.split(os.pathsep) if item)
    return values


def _safe_relative(path: Path, root: Path) -> PurePosixPath | None:
    resolved = path.resolve(strict=False)
    if not _is_within(resolved, root):
        return None
    return PurePosixPath(resolved.relative_to(root).as_posix())


def _normalise_remote(value: str) -> str:
    value = value.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.hostname:
            host = parsed.hostname
            try:
                port = parsed.port
            except ValueError:
                port = None
            if port:
                host = f"{host}:{port}"
            value = f"{parsed.scheme}://{host}{parsed.path}"
    else:
        value = re.sub(r"^[^@]+@", "", value)
    value = re.sub(r"[?#].*$", "", value)
    return value.rstrip("/")


def _read_git_origin(root: Path) -> str | None:
    config = root / ".git" / "config"
    if config.is_file():
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        in_origin = False
        for line in text.splitlines():
            section = re.match(r"\s*\[remote\s+\"([^\"]+)\"\]", line)
            if section:
                in_origin = section.group(1) == "origin"
                continue
            if in_origin:
                match = re.match(r"\s*url\s*=\s*(.+?)\s*$", line)
                if match:
                    return _normalise_remote(match.group(1))
    return None


def _read_git_ref(root: Path) -> str | None:
    head = root / ".git" / "HEAD"
    try:
        text = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    match = re.fullmatch(r"ref:\s+(.+)", text)
    return match.group(1) if match else (text or None)


def resolve_repository_root(
    supplied_path: str | os.PathLike[str],
    *,
    protected_roots: Sequence[str | os.PathLike[str]] = (),
) -> Path:
    """Resolve a caller path without executing Git or project code."""

    raw = Path(supplied_path).expanduser()
    if not raw.exists():
        raise ContextDiscoveryError(f"repository path does not exist: {supplied_path}")
    start = raw.resolve(strict=True)
    if start.is_file():
        start = start.parent

    protected = default_protected_roots()
    protected.extend(Path(value).expanduser().resolve(strict=False) for value in protected_roots)
    for boundary in protected:
        if _is_within(start, boundary) or _is_within(boundary, start):
            raise ContextDiscoveryError(
                "repository path overlaps a protected root"
            )

    current = start
    git_candidates: list[Path] = []
    while True:
        if (current / ".git").exists():
            git_candidates.append(current)
        if current.parent == current:
            break
        current = current.parent

    if git_candidates:
        # The nearest Git root is the safest target when a worktree contains a
        # nested repository. Equal candidates cannot occur in one parent chain.
        root = git_candidates[0]
    else:
        root = start

    root = root.resolve(strict=True)
    for boundary in protected:
        if _is_within(root, boundary) or _is_within(boundary, root):
            raise ContextDiscoveryError(
                "resolved repository root overlaps a protected root"
            )
    return root


def normalise_target_paths(
    root: Path,
    targets: Sequence[str | os.PathLike[str]] | None,
) -> list[str]:
    if not targets:
        return ["."]
    result: list[str] = []
    for value in targets:
        raw = Path(value).expanduser()
        candidate = raw if raw.is_absolute() else root / raw
        resolved = candidate.resolve(strict=False)
        relative = _safe_relative(resolved, root)
        if relative is None:
            raise ContextDiscoveryError(f"target escapes repository root: {value}")
        normalized = relative.as_posix() or "."
        if normalized not in result:
            result.append(normalized)
    return result or ["."]


def build_repository_identity(
    root: Path,
    *,
    project_id: str | None = None,
    repository_id: str | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    remote = _read_git_origin(root)
    if repository_id:
        resolved_repository_id = repository_id.strip()
        confidence = "explicit"
    elif remote:
        resolved_repository_id = f"remote:{remote}"
        confidence = "repository_metadata"
    else:
        marker_names = sorted(
            path.name
            for path in root.iterdir()
            if path.name in MANIFEST_NAMES or path.name in {".git", "AGENTS.md"}
        )
        resolved_repository_id = f"local:{_sha256_text(root.as_posix() + _canonical_json(marker_names))[:24]}"
        confidence = "local_fallback"

    if project_id:
        resolved_project_id = project_id.strip()
    elif remote:
        basename = remote.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        resolved_project_id = basename.removesuffix(".git") or root.name
    else:
        resolved_project_id = root.name or "project"

    return {
        "projectId": resolved_project_id,
        "repositoryId": resolved_repository_id,
        "repositoryPathHint": ".",
        "worktreeIdentity": _sha256_text(str((root / ".git").resolve(strict=False)))[:24]
        if (root / ".git").exists()
        else None,
        "sourceRef": source_ref or _read_git_ref(root),
        "identityConfidence": confidence,
    }


def _collect_files(root: Path, *, limit: int | None = None) -> tuple[list[Path], bool]:
    limit = MAX_CANDIDATES if limit is None else limit
    collected: list[Path] = []
    excluded = _excluded_directories()
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name
            for name in sorted(directories)
            if name not in excluded
            and not (current_path / name).is_symlink()
        ]
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink():
                continue
            if len(collected) >= limit:
                return collected, True
            collected.append(path)
    return collected, False


def _collect_target_files(
    root: Path,
    targets: Sequence[str],
    *,
    limit: int = FAST_MAX_CANDIDATES,
) -> tuple[list[Path], bool]:
    """Collect only target descendants and their nearby context files.

    Fast mobile work needs the requested module plus root/ancestor instructions
    and manifests.  It must not pay the cost of a repository-wide walk merely
    to classify or start a routine source change.
    """

    collected: list[Path] = []
    seen: set[Path] = set()
    truncated = False
    excluded = _excluded_directories()

    def add(path: Path) -> None:
        nonlocal truncated
        if truncated or path in seen or path.is_symlink() or not path.is_file():
            return
        relative = _safe_relative(path, root)
        if relative is None or any(part in excluded for part in relative.parts[:-1]):
            return
        if len(collected) >= limit:
            truncated = True
            return
        seen.add(path)
        collected.append(path)

    def add_context(directory: Path) -> None:
        if not _is_within(directory, root):
            return
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.is_file() and not path.is_symlink():
                relative = _safe_relative(path, root)
                if relative is not None and _is_candidate(relative):
                    add(path)

    # Context files are prioritized so a large target cannot evict AGENTS or
    # package metadata from the bounded collection.
    target_paths = [root if target == "." else root / target for target in targets]
    for target_path in target_paths:
        current = target_path if target_path.is_dir() else target_path.parent
        while _is_within(current, root):
            add_context(current)
            if current == root:
                break
            current = current.parent

    # A dot target intentionally means root context only in fast mode.  The
    # caller should provide an explicit mobile module when source files are
    # needed; this keeps an omitted target from becoming a full-repo scan.
    for target_path in target_paths:
        if target_path == root:
            continue
        if target_path.is_file():
            add(target_path)
            continue
        if not target_path.is_dir():
            continue
        for current, directories, filenames in os.walk(target_path, topdown=True, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name
                for name in sorted(directories)
                if name not in excluded
                and not (current_path / name).is_symlink()
            ]
            for filename in sorted(filenames):
                add(current_path / filename)
                if truncated:
                    return collected, True
    return collected, truncated


def _is_candidate(rel: PurePosixPath) -> bool:
    name = rel.name
    parts = rel.parts
    if name in INSTRUCTION_NAMES | MANIFEST_NAMES | ROOT_CONTEXT_NAMES | TEST_CONFIG_NAMES:
        return True
    if name.startswith("openapi") or name.startswith("schema"):
        return rel.suffix.lower() in {".json", ".yaml", ".yml", ".md", ".toml"}
    if rel.suffix.lower() == ".md" and parts and parts[0] in {"docs", "plans", "design"}:
        return len(parts) <= 6
    if parts[:2] == (".github", "workflows") and rel.suffix.lower() in {".yml", ".yaml"}:
        return True
    if "test" in name.lower() and rel.suffix.lower() in {".md", ".toml", ".yaml", ".yml", ".json"}:
        return True
    if "plan" in name.lower() and rel.suffix.lower() in {".md", ".yaml", ".yml", ".json"}:
        return True
    return False


def _classify_kind(rel: PurePosixPath) -> tuple[str, str]:
    name = rel.name
    lower = rel.as_posix().lower()
    if name in INSTRUCTION_NAMES:
        return "instruction", "high" if name == "AGENTS.md" else "medium"
    if name in MANIFEST_NAMES or name in TEST_CONFIG_NAMES:
        return ("test" if "test" in name.lower() or "jest" in name.lower() or "vitest" in name.lower() else "manifest"), "high"
    if "openapi" in lower or "schema" in lower or "contract" in lower:
        return "contract", "high" if "openapi" in lower else "medium"
    if "architecture" in lower or "/design/" in lower or lower.startswith("design/"):
        return "architecture", "medium"
    if "plan" in lower or "exec-plans" in lower:
        return "plan", "medium"
    if "test" in lower or "quality" in lower or "ci/" in lower or ".github/workflows" in lower:
        return "quality", "medium"
    if name == "README.md" or "product" in lower:
        return "product", "medium"
    if "workflow" in lower or "md-execute" in lower:
        return "workflow", "low"
    return "reference", "low"


def _scope_for(rel: PurePosixPath, targets: Sequence[str]) -> tuple[str, str]:
    parent = rel.parent
    parent_parts = () if str(parent) == "." else parent.parts
    if rel.name in INSTRUCTION_NAMES:
        if not parent_parts:
            return "/", "root"
        for target in targets:
            target_parts = () if target == "." else PurePosixPath(target).parts
            if target_parts[: len(parent_parts)] == parent_parts:
                return f"/{parent.as_posix()}/**", "ancestor"
        return f"/{parent.as_posix()}/**", "global_candidate"
    for target in targets:
        target_parts = () if target == "." else PurePosixPath(target).parts
        if target_parts[: len(rel.parts)] == rel.parts or rel.parts[: len(target_parts)] == target_parts:
            return f"/{rel.as_posix()}", "target"
    return "/", "root"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path, limit: int = MAX_CONTENT_BYTES) -> str:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    return data[:limit].decode("utf-8", errors="replace")


def _looks_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _contains_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)


def _source_record(path: Path, root: Path, targets: Sequence[str]) -> tuple[dict[str, Any], str]:
    rel = _safe_relative(path, root)
    if rel is None:
        raise ContextDiscoveryError(f"candidate escapes repository root: {path}")
    kind, confidence = _classify_kind(rel)
    scope, scope_kind = _scope_for(rel, targets)
    try:
        metadata = path.stat()
        content_hash = _hash_file(path)
        text = _read_text(path)
    except OSError as exc:
        record = {
            "path": rel.as_posix(),
            "kind": kind,
            "scope": scope,
            "scopeKind": scope_kind,
            "contentHash": "unreadable",
            "sizeBytes": 0,
            "classificationConfidence": "low",
            "usability": "ignored",
            "limitation": f"source could not be read: {type(exc).__name__}",
        }
        return record, ""

    sensitive = _looks_sensitive(text)
    if sensitive:
        usability = "ignored"
        limitation = "secret-like content detected; content excluded from context"
    elif kind == "instruction" and rel.name == "AGENTS.md":
        usability = "authoritative"
        limitation = None
    elif kind == "workflow":
        usability = "candidate"
        limitation = "workflow text is contextual until controller authority is resolved"
    elif kind == "reference" and confidence == "low":
        usability = "candidate"
        limitation = "low-confidence document classification"
    else:
        usability = "contextual"
        limitation = None

    record: dict[str, Any] = {
        "path": rel.as_posix(),
        "kind": kind,
        "scope": scope,
        "scopeKind": scope_kind,
        "contentHash": content_hash,
        "sizeBytes": int(metadata.st_size),
        "modifiedAt": str(metadata.st_mtime_ns),
        "classificationConfidence": confidence,
        "usability": usability,
    }
    if limitation:
        record["limitation"] = limitation
    return record, text if not sensitive else ""


def _manifest_candidates(root: Path, files: Sequence[Path] | None = None) -> list[Path]:
    paths = []
    candidates = files if files is not None else _collect_files(root)[0]
    for path in candidates:
        rel = _safe_relative(path, root)
        if rel and rel.as_posix().startswith("docs/agent-context/") and path.name in {
            "project-context.yaml",
            "project-context.yml",
            "project-context.json",
        }:
            paths.append(path)
    return paths


def load_project_manifest(root: Path, files: Sequence[Path] | None = None) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Load an optional JSON manifest or YAML when the host provides PyYAML."""

    candidates = _manifest_candidates(root, files)
    if not candidates:
        return None, None, None
    path = candidates[0]
    try:
        text = _read_text(path)
        if _looks_sensitive(text):
            return None, path.relative_to(root).as_posix(), "manifest contains secret-like content"
        if path.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            try:
                import yaml  # type: ignore
            except ImportError:
                return None, path.relative_to(root).as_posix(), "YAML parser unavailable; manifest remains contextual"
            value = yaml.safe_load(text)
        if not isinstance(value, dict):
            return None, path.relative_to(root).as_posix(), "manifest root must be an object"
        return value, path.relative_to(root).as_posix(), None
    except (OSError, ValueError, TypeError) as exc:
        return None, path.relative_to(root).as_posix(), f"manifest could not be parsed: {type(exc).__name__}"


def _manifest_profiles(manifest: Mapping[str, Any] | None) -> list[str]:
    if not manifest or not isinstance(manifest.get("profiles"), list):
        return []
    return sorted({str(item).strip() for item in manifest["profiles"] if str(item).strip()})


def _normalise_product_id(value: Any) -> str:
    """Convert a host-declared product/profile label into a safe portable id."""

    if not isinstance(value, str):
        return ""
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", value.strip().lower()).strip("._-")
    return normalized[:64]


def _add_signal(signals: list[dict[str, Any]], value: dict[str, Any]) -> None:
    identity = (value.get("platform"), value.get("product"), value.get("signal"), value.get("sourcePath"))
    if not any((item.get("platform"), item.get("product"), item.get("signal"), item.get("sourcePath")) == identity for item in signals):
        signals.append(value)


def detect_platform_and_product(
    root: Path,
    sources: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
    manifest_path: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    platform_signals: list[dict[str, Any]] = []
    product_signals: list[dict[str, Any]] = []
    profiles = {item.lower().replace("-", "_") for item in _manifest_profiles(manifest)}

    explicit_platforms = {
        "web": "web",
        "backend": "backend",
        "mobile": "mobile",
        "flutter": "flutter",
        "cross_platform": "cross_platform",
        "desktop": "desktop",
        "extension": "extension",
    }
    for profile, platform in explicit_platforms.items():
        if profile in profiles:
            _add_signal(platform_signals, {
                "platform": platform,
                "signal": f"project manifest profile: {profile}",
                "sourcePath": manifest_path or "project manifest",
                "confidence": "high",
                "detectionMode": "explicit",
            })

    source_by_path = {str(item.get("path")): item for item in sources}
    package_path = next((root / path for path in source_by_path if path == "package.json"), None)
    if package_path and package_path.is_file():
        try:
            package = json.loads(_read_text(package_path, 512_000))
        except (OSError, ValueError):
            package = {}
        dependency_names: set[str] = set()
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            if isinstance(package.get(key), dict):
                dependency_names.update(str(item).lower() for item in package[key])
        if dependency_names & {"react", "vite", "next", "@tanstack/react-router", "@tanstack/start"}:
            _add_signal(platform_signals, {
                "platform": "web",
                "signal": "package.json frontend framework dependency",
                "sourcePath": "package.json",
                "confidence": "high",
                "detectionMode": "manifest",
            })
        if dependency_names & {"@nestjs/core", "fastify", "express", "hono"}:
            _add_signal(platform_signals, {
                "platform": "backend",
                "signal": "package.json server framework dependency",
                "sourcePath": "package.json",
                "confidence": "high",
                "detectionMode": "manifest",
            })
        if "react-native" in dependency_names or "expo" in dependency_names:
            _add_signal(platform_signals, {
                "platform": "mobile",
                "signal": "package.json mobile framework dependency",
                "sourcePath": "package.json",
                "confidence": "high",
                "detectionMode": "manifest",
            })

    if any(path.name in {"pubspec.yaml", "pubspec.yml"} for path in (root / item for item in source_by_path)):
        _add_signal(platform_signals, {
            "platform": "flutter",
            "signal": "pubspec manifest",
            "sourcePath": "pubspec.yaml",
            "confidence": "high",
            "detectionMode": "manifest",
        })

    if (root / "android").is_dir() or (root / "ios").is_dir() or any(root.glob("*.xcodeproj")):
        _add_signal(platform_signals, {
            "platform": "mobile",
            "signal": "android/ios native project root",
            "sourcePath": "android/ or ios/",
            "confidence": "high",
            "detectionMode": "heuristic",
        })

    # Product/profile identity is intentionally host-declared. A general
    # skill must not guess a product from arbitrary documentation text or
    # contain a fixed product allowlist. `profiles` remains compatible with
    # V2, while `products` and `productProfiles` make the contract explicit
    # for projects that need a separate product identity.
    declared_products: set[str] = set()
    for key in ("products", "productProfiles", "applications"):
        value = manifest.get(key) if isinstance(manifest, Mapping) else None
        values = value if isinstance(value, list) else [value]
        for item in values:
            product = _normalise_product_id(item)
            if product:
                declared_products.add(product)

    platform_profiles = set(explicit_platforms)
    non_product_profiles = {"common", "v3_common", "project", "task", "shared"}
    declared_products.update(
        profile for profile in profiles
        if profile not in platform_profiles and profile not in non_product_profiles
    )
    for product in sorted(declared_products):
        _add_signal(product_signals, {
            "product": product,
            "signal": "project manifest product/profile declaration",
            "sourcePath": manifest_path or "project manifest",
            "confidence": "high",
            "detectionMode": "explicit",
        })

    distinct_high = {item["platform"] for item in platform_signals if item["confidence"] == "high" and item["platform"] != "cross_platform"}
    if len(distinct_high) >= 2:
        _add_signal(platform_signals, {
            "platform": "cross_platform",
            "signal": "multiple high-confidence platform signals",
            "sourcePath": "derived",
            "confidence": "high",
            "detectionMode": "manifest",
        })
    return platform_signals, product_signals


def discover_plan_roots(root: Path) -> list[dict[str, str]]:
    candidates = (
        ("docs/exec-plans/active", "canonical"),
        ("docs/exec-plans/completed", "canonical"),
        ("docs/plans", "legacy"),
        ("plans", "unknown"),
        ("design", "unknown"),
    )
    return [
        {"path": path, "status": status}
        for path, status in candidates
        if (root / path).is_dir()
    ]


def _source_fingerprint(root: Path, targets: Sequence[str], files: Sequence[Path]) -> str:
    records: list[dict[str, Any]] = []
    for path in files:
        rel = _safe_relative(path, root)
        if rel is None:
            continue
        target_relevant = any(
            target == "."
            or rel.as_posix().startswith(target.rstrip("/") + "/")
            or rel.as_posix() == target
            for target in targets
        )
        try:
            metadata = path.stat()
        except OSError:
            continue
        item: dict[str, Any] = {
            "path": rel.as_posix(),
            "size": int(metadata.st_size),
            "mtime": int(metadata.st_mtime_ns),
            "targetRelevant": target_relevant,
        }
        if metadata.st_size <= MAX_SOURCE_FINGERPRINT_BYTES:
            try:
                item["hash"] = _hash_file(path)
            except OSError:
                item["hash"] = "unreadable"
        records.append(item)
    return _sha256_text(_canonical_json(records))


def _context_fingerprint(sources: Sequence[Mapping[str, Any]], plan_roots: Sequence[Mapping[str, Any]]) -> str:
    values = [
        {
            "path": item.get("path"),
            "kind": item.get("kind"),
            "scope": item.get("scope"),
            "contentHash": item.get("contentHash"),
            "usability": item.get("usability"),
        }
        for item in sources
    ]
    return _sha256_text(_canonical_json({"sources": values, "planRoots": list(plan_roots)}))


def _task_scope_fingerprint(task_type: str, targets: Sequence[str], acceptance: Sequence[str]) -> str:
    return _sha256_text(_canonical_json({
        "taskType": task_type,
        "targets": sorted(targets),
        "acceptanceCriteria": list(acceptance),
    }))


def _select_sources(sources: Sequence[Mapping[str, Any]], targets: Sequence[str], task_type: str) -> list[str]:
    selected: list[str] = []
    for item in sources:
        if item.get("usability") == "ignored":
            continue
        scope_kind = item.get("scopeKind")
        kind = item.get("kind")
        if scope_kind in {"root", "ancestor", "target"} or kind in {"manifest", "instruction", "architecture", "contract"}:
            path = str(item.get("path"))
            if path not in selected:
                selected.append(path)
    return sorted(selected)


def _task_requires_deep_context(task_type: str, acceptance: Sequence[str]) -> set[str]:
    text = f"{task_type} {' '.join(acceptance)}".lower()
    required: set[str] = set()
    if any(token in text for token in ("offline", "sync", "queue", "migration", "persistence")):
        required.add("persistence")
    if any(token in text for token in ("permission", "authorization", "tenant", "security")):
        required.add("authorization")
    if any(token in text for token in ("browser", "rendered", "responsive")):
        required.add("browser")
    if any(token in text for token in ("device", "lifecycle", "flutter")):
        required.add("device")
    return required


def _build_gaps(
    sources: Sequence[Mapping[str, Any]],
    platform_signals: Sequence[Mapping[str, Any]],
    task_type: str,
    acceptance: Sequence[str],
    manifest_error: str | None,
    manifest_path: str | None,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not any(item.get("path") == "AGENTS.md" for item in sources):
        gaps.append({
            "gapId": "missing-root-agents",
            "category": "instruction",
            "expected": "root AGENTS.md",
            "blocking": False,
            "reason": "No repository-root AGENTS.md was found; active host guidance remains in force.",
        })
    if manifest_error and manifest_path:
        gaps.append({
            "gapId": "manifest-unavailable",
            "category": "manifest",
            "expected": manifest_path,
            "blocking": False,
            "reason": manifest_error,
        })
    high_platforms = {item.get("platform") for item in platform_signals if item.get("confidence") == "high"}
    if not high_platforms:
        gaps.append({
            "gapId": "platform-unknown",
            "category": "platform_profile",
            "expected": "verified web, backend, mobile, Flutter, or cross-platform signal",
            "blocking": False,
            "reason": "No high-confidence platform signal was found; platform-specific blocking rules are not applied.",
        })

    deep = _task_requires_deep_context(task_type, acceptance)
    kinds = {item.get("kind") for item in sources if item.get("usability") != "ignored"}
    if "persistence" in deep and not ({"architecture", "quality", "contract"} & kinds):
        gaps.append({
            "gapId": "missing-persistence-guidance",
            "category": "architecture",
            "expected": "offline/persistence/queue/migration guidance or tests",
            "blocking": True,
            "reason": "The task references persistence-sensitive behavior but no architecture, quality, or contract source was found.",
        })
    if "authorization" in deep and not ({"instruction", "architecture", "contract"} & kinds):
        gaps.append({
            "gapId": "missing-authorization-guidance",
            "category": "architecture",
            "expected": "authorization, tenant, or permission guidance",
            "blocking": True,
            "reason": "The task references authorization-sensitive behavior but no scoped authority or contract source was found.",
        })
    return gaps


def _build_conflicts(sources: Sequence[Mapping[str, Any]], source_text: Mapping[str, str]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    authoritative = [str(item["path"]) for item in sources if item.get("usability") == "authoritative"]
    workflow_candidates = [
        str(item["path"])
        for item in sources
        if item.get("kind") == "workflow" or "md-execute" in str(item.get("path"))
    ]
    mentions_competing_controller = [
        path for path, text in source_text.items()
        if path in workflow_candidates and re.search(r"(?i)md-execute|second controller|competing controller", text)
    ]
    if authoritative and mentions_competing_controller:
        conflicts.append({
            "conflictId": "lifecycle-authority-001",
            "sourcePaths": sorted(set(authoritative + mentions_competing_controller)),
            "subject": "lifecycle controller",
            "blocking": True,
            "reason": "A contextual workflow source proposes a competing lifecycle controller; authority must be clarified before affected mutation.",
        })

    injection_paths = [path for path, text in source_text.items() if _contains_injection(text)]
    if injection_paths:
        conflicts.append({
            "conflictId": "document-injection-001",
            "sourcePaths": sorted(injection_paths),
            "subject": "natural-language prompt injection",
            "blocking": False,
            "reason": "Document prose was treated as data, not authority; suspicious imperative text remains visible for review.",
        })
    return conflicts


def _source_policy_fingerprint() -> str:
    return _sha256_text(DISCOVERY_VERSION + ":" + POLICY_CATALOG_VERSION)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def discover_project_context(
    supplied_path: str | os.PathLike[str],
    *,
    task_type: str = "NEW_TASK",
    targets: Sequence[str | os.PathLike[str]] | None = None,
    acceptance_criteria: Sequence[str] = (),
    project_id: str | None = None,
    repository_id: str | None = None,
    source_ref: str | None = None,
    explicit_profiles: Sequence[str] = (),
    protected_roots: Sequence[str | os.PathLike[str]] = (),
    fast_path: bool = False,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    if max_candidates is not None and (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or not 1 <= max_candidates <= MAX_CANDIDATE_LIMIT
    ):
        raise ContextDiscoveryError(
            f"max_candidates must be an integer from 1 to {MAX_CANDIDATE_LIMIT}"
        )
    if fast_path and max_candidates is not None:
        raise ContextDiscoveryError("max_candidates cannot be combined with fast_path")

    root = resolve_repository_root(supplied_path, protected_roots=protected_roots)
    normalized_targets = normalise_target_paths(root, targets)
    identity = build_repository_identity(
        root,
        project_id=project_id,
        repository_id=repository_id,
        source_ref=source_ref,
    )
    full_limit = MAX_CANDIDATES if max_candidates is None else max_candidates
    files, enumeration_truncated = (
        _collect_target_files(root, normalized_targets)
        if fast_path
        else _collect_files(root, limit=full_limit)
    )
    enumeration_limit = FAST_MAX_CANDIDATES if fast_path else full_limit
    enumeration_mode = "target-scoped fast" if fast_path else "repository-wide"
    manifest, manifest_path, manifest_error = load_project_manifest(root, files)
    sources: list[dict[str, Any]] = []
    source_text: dict[str, str] = {}
    for path in files:
        rel = _safe_relative(path, root)
        if rel is None or not _is_candidate(rel):
            continue
        record, text = _source_record(path, root, normalized_targets)
        sources.append(record)
        if text:
            source_text[record["path"]] = text
    sources.sort(key=lambda item: str(item["path"]))

    platform_signals, product_signals = detect_platform_and_product(root, sources, manifest, manifest_path)
    plan_roots = discover_plan_roots(root)
    command_sources = sorted({
        str(item["path"])
        for item in sources
        if item.get("kind") in {"manifest", "test", "quality", "contract"}
    })
    gaps = _build_gaps(sources, platform_signals, task_type, acceptance_criteria, manifest_error, manifest_path)
    if enumeration_truncated:
        gaps.append({
            "gapId": "candidate-enumeration-truncated",
            "category": "reference",
            "expected": f"all candidate files within the {enumeration_limit}-file {enumeration_mode} discovery bound",
            "blocking": True,
            "reason": "The bounded discovery limit was reached; omitted paths remain out of scope and freshness cannot be treated as complete.",
        })
    conflicts = _build_conflicts(sources, source_text)
    source_fingerprint = _source_fingerprint(root, normalized_targets, files)
    context_fingerprint = _context_fingerprint(sources, plan_roots)
    task_scope_fingerprint = _task_scope_fingerprint(task_type, normalized_targets, acceptance_criteria)
    blocking_conflict = any(bool(item.get("blocking")) for item in conflicts)
    blocking_gap = any(bool(item.get("blocking")) for item in gaps)
    status = "CONFLICT" if blocking_conflict else ("INCOMPLETE" if blocking_gap else "CURRENT")
    selected_sources = _select_sources(sources, normalized_targets, task_type)

    context_map: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "repository": identity,
        "status": status,
        "fingerprints": {
            "sourceFingerprint": source_fingerprint,
            "contextFingerprint": context_fingerprint,
            "policyFingerprint": _source_policy_fingerprint(),
            "taskScopeFingerprint": task_scope_fingerprint,
        },
        "sources": sources,
        "platformSignals": platform_signals,
        "productSignals": product_signals,
        "manifestProfiles": _manifest_profiles(manifest),
        "planRoots": plan_roots,
        "commandSources": command_sources,
        "gaps": gaps,
        "conflicts": conflicts,
        "persistence": "session_only",
        "generatedAt": utc_now(),
        "discoveryVersion": DISCOVERY_VERSION,
    }
    receipt: dict[str, Any] = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "taskId": _sha256_text(identity["repositoryId"] + ":" + task_scope_fingerprint)[:24],
        "repository": identity,
        "fingerprints": context_map["fingerprints"],
        "status": status,
        "inspectedSources": [
            {"path": item["path"], "kind": item["kind"], "contentHash": item["contentHash"]}
            for item in sources
        ],
        "selectedSources": selected_sources,
        "gaps": gaps,
        "conflicts": conflicts,
        "evidence": [
            {"operation": "canonicalize_repository_root", "result": "pass", "summary": "repository boundary canonicalized and protected-root checked"},
            {"operation": "bounded_candidate_enumeration", "result": "partial" if enumeration_truncated else "pass", "summary": f"{enumeration_mode} discovery enumerated {len(files)} files within a {enumeration_limit}-file bound and inspected {len(sources)} candidate sources" + ("; discovery bound reached and omitted paths remain out of scope" if enumeration_truncated else "")},
            {"operation": "platform_product_detection", "result": "pass", "summary": f"platform signals: {len(platform_signals)}, product signals: {len(product_signals)}"},
            {"operation": "privacy_and_boundary_filter", "result": "pass", "summary": "excluded runtime, generated, secret-like, and escaping paths"},
        ],
        "persistence": "session_only",
        "terminalStatus": "MISSING_COVERAGE" if enumeration_truncated else ("BLOCKED" if blocking_conflict or blocking_gap else "COMPLETED"),
        "issuedAt": utc_now(),
    }
    return {
        "root": root.as_posix(),
        "manifest": manifest,
        "manifestPath": manifest_path,
        "contextMap": context_map,
        "receipt": receipt,
        "selectedSources": selected_sources,
    }
