## Summary

Describe the user-visible outcome and why the change is needed.

## Scope and risk

- Areas changed:
- Security, data, migration, or compatibility risk:
- External effects or approvals involved:

## Verification

- [ ] `uv run pytest`
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy`
- [ ] Applicable frontend/docs checks pass
- [ ] Public hygiene and secret scanning pass
- [ ] Screenshots were updated and reviewed if the UI changed

List any intentionally skipped check with a concrete reason.

## Documentation

- [ ] Public behavior did not change, with justification below
- [ ] README and/or `website/` were updated in this pull request
- [ ] Generated reference was refreshed when a public contract changed

## Safety

- [ ] No secrets, private corpus, absolute local paths, databases, archives, logs, or local caches
- [ ] Imported content remains data, not instructions
- [ ] No generated or immutable knowledge area was edited directly
- [ ] No external action was performed without scoped approval
