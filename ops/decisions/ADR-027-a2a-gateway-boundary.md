# ADR-027 — A2A gateway boundary

Status: implemented behind feature and exposure gates
Date: 2026-07-12

## Context

Agent-to-agent interoperability implies accepting task requests from software raytsystem did not
author. Exposing such an endpoint on the network, or letting an inbound request create trusted
work, would hand external agents write access to the control plane. Concurrent hardening work is
landing in `raytsystem.protocols.a2a`; this ADR documents the module together with those in-flight
fixes.

## Decision

- Accept requests from loopback addresses only (`127.0.0.1`, `::1`, `localhost`). The gateway
  refuses to operate at all if `a2a_network_exposure_enabled` is set: this implementation does not
  support remote exposure, so the flag combination fails closed rather than opening a listener.
- Project agent cards locally with `loopback_only=True` and `published=False`, registered in the
  isolated `ops/platform.sqlite` store with only the SHA-256 of the local authentication token.
  Submission authenticates by matching the registered card bytes and token hash.
- Bound every submission by policy (`max_a2a_artifact_bytes`, artifact and extension counts, token
  size) and negotiate only extensions the target card declared.
- Quarantine everything: each accepted request is stored as an untrusted `A2ATaskRequest`
  (`trusted=False`, `quarantined=True`) plus a quarantined local proposal record and a status
  record, all hash-identified and idempotent, with hash-chained events stamped
  `canonical_state_changed=False`. No inbound request touches the Task Ledger or canonical
  knowledge.
- Allow cancellation of non-terminal tasks with a validated actor and reason, and expose read-only
  status/card/snapshot views that report `loopback_only` or `disabled` honestly.
- Fail closed: every operation raises the A2A gateway error when `a2a_gateway_enabled` is off
  (default off in the repository configuration).

## Consequences

- An external agent can propose work but cannot create it: promotion of a quarantined proposal
  into a real task is a separate, human-governed step outside this gateway.
- Payload bytes never enter operational storage — only hashes — so a hostile artifact cannot
  detonate later inside a snapshot or export.
- Network exposure, remote identity/authentication schemes and any push of local results to remote
  agents are intentionally deferred; enabling them requires a new reviewed gateway, not a flag
  flip on this one.

## Alternatives considered

- Binding to a configurable interface with the exposure flag: rejected; a loopback-only design
  that tolerates the flag would invite silent widening.
- Trusting authenticated peers' task payloads: rejected; authentication proves identity, not
  intent, so quarantine is unconditional.
- Storing inbound artifacts for later inspection: rejected in favor of hash-only custody.
