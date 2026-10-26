# Human-readable code quality gate

The readability gate evaluates changed source before closure. It is a
maintainability check, not an authorship detector. It never claims that code
was written by a human; it checks whether the code is understandable and
consistent enough for the next developer to maintain.

## Checks

The bounded default policy checks:

- trailing whitespace and excessive line length;
- Python syntax errors;
- oversized Python functions;
- excessive control-flow complexity;
- deep nesting;
- unfinished `TODO`, `FIXME`, or `HACK` placeholders;
- missing explanations for non-obvious complex logic;
- missing language-specific coverage.

The policy is intentionally conservative. It reports `NEEDS_REVIEW` for
maintainability findings rather than pretending that a generic cross-language
heuristic can replace a project formatter, linter, compiler, or human review.
Use the project’s own tools for language-specific formatting and static
analysis when available.

## Workflow position

```text
repository reuse assessment
  -> smallest coherent implementation
  -> focused tests
  -> readability assessment
  -> code review and verification
  -> closure only when findings are resolved or explicitly accepted
```

The assessment returns only paths, line numbers, rules, and suggestions.
Source content is not included in the result. Its `humanAuthorship` value is
always `NOT_DETERMINABLE`.

## CLI

Provide a JSON array containing changed-file objects with `path`, `content`,
and optional `language`:

```bash
python3 -B scripts/validate_readability.py \
  --files-json /absolute/path/to/changed-files.json \
  --task-id TASK_ID \
  --source-fingerprint sha256:CURRENT_SOURCE_FINGERPRINT
```

The exit code is zero only for `PASS`. `NEEDS_REVIEW` and `BLOCKED` return a
non-zero code so a host can prevent unreviewed closure.
