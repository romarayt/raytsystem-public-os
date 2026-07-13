# ADR-014 — Synthetic qualification and pilot boundary

Status: accepted
Date: 2026-07-11

## Context

M0–M4 prove individual contracts and vertical slices, but those results do not constitute real
quality, human approval or production readiness. M5a needs one reproducible evidence map across all
hard invariants without inventing baselines or silently treating unavailable attack surfaces as
implemented features.

## Decision

- Maintain `evals/m5a/synthetic-qualification-v1.json` as the versioned, fixture-hashed map from
  every declared G0–G7 invariant and adversarial category to exact executable tests/artifacts.
- Store measured command results and synthetic-only metrics in a separate immutable result artifact;
  bind dataset and result hashes from the M5a run manifest.
- Report G0–G7 as `PASS_SYNTHETIC` only within the declared fixture scope. Keep G8 real quality, G9
  human review and G10 pilot fixed at `PENDING_USER_PILOT`.
- Treat unavailable live HTTPS/yt-dlp Fetchers, archives, webhooks, OCR/ASR, QMD model assets and
  scheduled runs as explicit disabled boundaries. Default-deny tests are evidence of no reachable
  side effect, not functional qualification of those adapters.
- Add an offline captured-HTML Extractor, high-confidence email/E.164 detection, self-wikilink lint,
  PDF timeout/output quotas and ENOSPC recovery cases to close observed synthetic gaps.
- Publish scanner scope/limitations and require a 16-item user package plus exact model destination,
  dependency/risk review and hash-bound manual-promotion approval before M5b.
- Because no key provider is approved, M5b is initially limited to public/internal data without
  secrets/PII. Access-controlled Git-ignored quarantine is not represented as application
  encryption or as pilot authority.

## Consequences

- The repository can reproduce why each synthetic gate passed and can detect a stale fixture,
  missing test or dishonest pilot status.
- Synthetic retrieval and YouTube consistency metrics remain useful regression checks but cannot be
  generalized to real Russian queries, claims, channel quality, latency or cost.
- `READY_FOR_USER_PILOT` means implementation qualification is complete and the next action belongs
  to the user; it does not authorize real promotion, cloud transfer, publication or automation.
- Encrypted restricted raw, live Fetchers and all M6 automation require separate decisions rather
  than being smuggled into the M5a completion claim.
