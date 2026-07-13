# Security Policy

raytsystem is a **local-first** system. Understanding its security posture matters both for reporting
issues and for evaluating whether the project is safe to run against your own data.

## Security posture (what the project guarantees)

- **Loopback only.** The web control plane refuses any non-`127.0.0.1` bind in this release
  (`raytsystem ui`). No remote listener is exposed.
- **No egress by default.** Every external-effect feature flag is off in shipped configuration:
  `external_mcp_execution_enabled`, `external_notifications_enabled`, `external_kms_enabled`,
  `a2a_network_exposure_enabled`, `otel_export_enabled`, `promptfoo_remote_generation_enabled`.
  The default UI does not execute a model or contact a provider.
- **Imported content is inert data, never instructions.** Ingested sources are treated as untrusted
  data; routing is driven by the declared operation, never by text embedded in imported content.
- **No absolute filesystem paths are sent to the browser.** Catalog and code paths are workspace-
  relative; the control plane never leaks the host's absolute paths to the client.
- **Writes are gated.** Every mutating web request requires a same-origin session, CSRF binding and
  idempotency key. External send / publish / upload / push / payment / deletion / private-corpus
  egress / real-corpus promotion all require separate, explicitly scoped approval.
- **Secrets stay out of tracked material.** Secrets must never be placed in prompts, Git-tracked
  files, traces or logs. Restricted exact bytes live only in the gitignored `_raw/restricted/` and
  `ops/encrypted/` zones.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately to the maintainer:

- Telegram: [@romarayt](https://t.me/romarayt)

Include: affected version/commit, a description of the issue, reproduction steps, and the impact you
observed. Please give the maintainer a reasonable window to respond and ship a fix before any public
disclosure. There is no bug-bounty program; responsible disclosure is appreciated and credited (with
your consent) in the release notes.

## Scope

In scope: the raytsystem engine (`src/raytsystem/**`), the CLI, the local web control plane, the template/
installer flow, and the documented security invariants above.

Out of scope: third-party dependencies (report upstream), issues that require an attacker to already
have write access to your local machine, and the optional demonstration packs when run outside their
documented, default-off configuration.

## Supported versions

raytsystem is pre-1.0 and under active development. Only the latest `main` is supported; fixes are not
back-ported to older tags unless separately stated in release notes.
