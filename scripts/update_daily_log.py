#!/usr/bin/env python3
"""Upsert a date-wise engineering worklog section by session id."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    from storage_paths import resolve_storage_paths
except ImportError:  # pragma: no cover - package import fallback
    from .storage_paths import resolve_storage_paths

try:
    import fcntl
except ImportError:  # pragma: no cover - platform-specific fallback
    fcntl = None


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SECTION_PATTERN = re.compile(
    r"(?ms)^---\s*\n\s*<!--\s*session:(?P<session>[^>]+?)\s*-->\s*\n.*?(?=^---\s*\n\s*<!--\s*session:|\Z)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the date-wise engineering worklog.")
    parser.add_argument("--root", help="Root folder for date-wise logs; defaults to shared storage.")
    parser.add_argument("--date", help="Date folder in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--time", help="Display time. Defaults to current local time.")
    parser.add_argument("--title", required=True, help="Context title with short summary.")
    parser.add_argument("--session-id", required=True, help="Stable session/context id.")
    parser.add_argument("--summary", action="append", default=[], help="Summary detail. Repeat for multiple paragraphs.")
    parser.add_argument("--complete", action="append", default=[], help="Completed item. Repeatable.")
    parser.add_argument("--in-progress", action="append", default=[], help="In-progress item. Repeatable.")
    parser.add_argument("--todo", action="append", default=[], help="Todo item. Repeatable.")
    parser.add_argument("--new-item", action="append", default=[], help="New/off-plan item. Repeatable.")
    parser.add_argument("--print-path", action="store_true", help="Print only the updated file path.")
    return parser.parse_args()


def validate_date(value: str) -> str:
    if not DATE_PATTERN.fullmatch(value):
        raise ValueError("--date must use YYYY-MM-DD format")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("--date must be a real calendar date") from error
    if parsed.strftime("%Y-%m-%d") != value:
        raise ValueError("--date must be a real calendar date")
    return value


def validate_session_id(value: str) -> str:
    if not SESSION_PATTERN.fullmatch(value):
        raise ValueError("--session-id must be 1-128 chars: letters, digits, '.', '_', ':', '-'")
    return value


def today() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def now_time() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def configured_paths(environment_name: str) -> list[Path]:
    raw = os.environ.get(environment_name, "")
    return [canonical_path(Path(value)) for value in raw.split(os.pathsep) if value.strip()]


def path_is_inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_worklog_root(root: Path) -> Path:
    canonical_root = canonical_path(root)
    protected_roots = [canonical_path(Path(__file__).resolve().parents[1])]
    protected_roots.extend(configured_paths("BUILDCREW_PROTECTED_ROOTS"))
    protected_roots.extend(configured_paths("CODEX_PROTECTED_ROOTS"))
    for protected_root in protected_roots:
        if path_is_inside(canonical_root, protected_root):
            raise ValueError(
                f"worklog root overlaps protected runtime path: {canonical_root}"
            )

    # Both configured policies must permit the root during migration. A new
    # setting must not silently bypass an existing host restriction.
    for name in ("BUILDCREW_WORKLOG_ALLOWED_ROOTS", "CODEX_WORKLOG_ALLOWED_ROOTS"):
        allowed_roots = configured_paths(name)
        if allowed_roots and not any(
            path_is_inside(canonical_root, allowed_root) for allowed_root in allowed_roots
        ):
            raise ValueError(f"worklog root is outside configured allowlist: {canonical_root}")
    # Return the configured lexical absolute path while retaining canonical
    # paths for all protection and allowlist checks above. This avoids macOS
    # /var -> /private/var presentation drift in worklog receipts.
    return Path(root).expanduser().absolute()


def clean_inline(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def clean_markdown(value: str) -> str:
    cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = cleaned.replace("<!-- session:", "<!-- session marker:")
    lines = []
    for line in cleaned.split("\n"):
        lines.append(r"\---" if line.strip() == "---" else line.rstrip())
    return "\n".join(lines)


def clean_bullet(value: str) -> str:
    lines = clean_markdown(value).split("\n")
    if len(lines) == 1:
        return lines[0].strip()
    first = lines[0].strip()
    rest = "\n".join(f"    {line.strip()}" for line in lines[1:] if line.strip())
    return f"{first}\n{rest}" if rest else first


def bullet_block(items: list[str]) -> str:
    cleaned_items = [clean_bullet(item) for item in items if item.strip()]
    if not cleaned_items:
        return "  - None"
    return "\n".join(f"  - {item}" for item in cleaned_items)


def normalize_summary(summary_parts: list[str]) -> str:
    text = "\n\n".join(clean_markdown(part) for part in summary_parts if part.strip())
    return text or "No summary provided."


def build_section(args: argparse.Namespace, date_value: str, time_value: str) -> str:
    return f"""---

<!-- session:{args.session_id} -->
Title : {clean_inline(args.title)}
Time : {date_value} {time_value}
session Id : {args.session_id}
summary detail:
{normalize_summary(args.summary)}

Progress:
- Complete:
{bullet_block(args.complete)}
- In progress:
{bullet_block(args.in_progress)}
- Todo:
{bullet_block(args.todo)}
- New / off-plan:
{bullet_block(args.new_item)}
"""


def upsert_section(existing: str, session_id: str, new_section: str) -> str:
    existing = existing.replace("\r\n", "\n").replace("\r", "\n")
    for match in SECTION_PATTERN.finditer(existing):
        if match.group("session").strip() == session_id:
            return (existing[: match.start()] + new_section.rstrip() + existing[match.end() :]).rstrip() + "\n"
    return existing.rstrip() + "\n\n" + new_section.rstrip() + "\n"


@contextmanager
def exclusive_lock(lock_path: Path):
    with lock_path.open("a", encoding="utf-8") as lock_file:
        if fcntl is None:
            yield
            return
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_write(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def prepare_day_dir(
    root: Path, date_value: str, *, allow_fallback: bool
) -> tuple[Path, str | None]:
    day_dir = root / date_value
    try:
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir, None
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    except OSError as error:
        if not allow_fallback:
            raise
        fallback_root = validate_worklog_root(resolve_storage_paths().worklog_fallback_root)
        fallback_dir = fallback_root / date_value
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return (
            fallback_dir,
            f"worklog root {root} is unavailable ({error.__class__.__name__}: {error}); "
            f"using fallback {fallback_root}",
        )


def main() -> int:
    args = parse_args()
    try:
        date_value = validate_date(args.date or today())
        args.session_id = validate_session_id(args.session_id)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    time_value = args.time or now_time()

    try:
        root = validate_worklog_root(resolve_storage_paths(worklog_root=args.root).worklog_root)
        default_root = canonical_path(resolve_storage_paths().worklog_root)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    allow_fallback = str(root) == str(default_root)
    try:
        day_dir, fallback_warning = prepare_day_dir(
            root, date_value, allow_fallback=allow_fallback
        )
    except OSError as error:
        print(
            f"could not create worklog directory under {root}: "
            f"{error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    if fallback_warning:
        print(f"warning: {fallback_warning}", file=sys.stderr)
    log_path = day_dir / "worklog.md"
    lock_path = day_dir / ".worklog.lock"

    with exclusive_lock(lock_path):
        header = f"# Daily Work Log - {date_value}\n"
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else header
        if not existing.strip():
            existing = header
        elif not existing.startswith("# Daily Work Log"):
            existing = header + "\n" + existing

        section = build_section(args, date_value, time_value)
        updated = upsert_section(existing, args.session_id, section)
        atomic_write(log_path, updated)

    if args.print_path:
        print(log_path)
    else:
        print(f"Updated {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
