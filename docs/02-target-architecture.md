# raytsystem — целевая архитектура

Статус: superseded historical proposal; текущий runtime-статус ведётся в `docs/STATUS.md`

> Историческая записка. Описанный ниже YouTube domain pack удалён из активного продукта.
> Универсальные knowledge, research и media-inspection границы сохранены; опубликованные schema
> registries и ledger history остаются неизменяемыми compatibility artifacts.

Архитектурный стиль: local-first, artifact-first, Git-native, agent-assisted

Первый domain pack: YouTube Content OS

## 1. Product brief

Нужно построить систему, в которой файлы и заметки не лежат мёртвым архивом, а накапливаются как проверяемое знание и автоматически питают полный цикл YouTube-производства. Она должна работать с ChatGPT Work/Codex, оставаться переносимой между моделями и не зависеть от Obsidian как от базы данных.

Обещанный результат:

- источники видимы агенту между сессиями;
- знание компилируется один раз и затем поддерживается;
- INGEST/QUERY/LINT работают воспроизводимо;
- процедурная память оформлена как repo-scoped skills;
- `/yt` создаёт полный draft-пакет ролика;
- публикация и внешние действия всегда требуют отдельного approval;
- каждый вывод можно проследить до source span;
- повторные и оборванные runs безопасны.

## 2. Архитектурные принципы

1. **Raw is evidence.** Модель никогда не редактирует source of truth.
2. **Facts are claims, not prose.** Значимое утверждение имеет stable ID, provenance, temporal status и evidence.
3. **Structured ledger is canonical knowledge; Markdown is the canonical human interface.** Obsidian — viewer; граф читается обычным editor/CLI.
4. **Indexes are disposable.** SQLite FTS, QMD vectors и NetworkX graph полностью rebuildable.
5. **LLM proposes; deterministic kernel validates.** Canonical write происходит только после schema/integrity gates.
6. **One writer per partition.** Read-heavy subagents параллельны; promotion сериализован.
7. **Every run is idempotent and resumable.** Один input fingerprint не создаёт второй side effect.
8. **Temporal truth is preserved.** Новая информация supersedes/contradicts, а не стирает историю.
9. **Draft by default.** Send/publish/delete/external mutation идут только через outbox + approval.
10. **Quality compounds only when measured.** Evals и feedback являются частью цикла, а не финальным polish.

## 3. Два контура

```text
┌──────────────────────────── deterministic kernel ────────────────────────────┐
│ hash · manifests · schemas · locks · staging · lint · indexes · recovery   │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │ validated contracts
┌────────────────────────── semantic agent layer ──────────────────────────────┐
│ research · extraction proposals · synthesis · critique · content workflows │
│ ChatGPT Work · Codex · repo skills · bounded subagents                     │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │ draft artifacts / proposed patches
                                   ▼
                      verify → approve → promote → Git
```

Промпт управляет semantic layer. Код и данные обеспечивают гарантии deterministic kernel.

## 4. Поток системы

```text
NETWORK/LOCAL CAPTURE
  ↓
INBOX (untrusted)
  ↓ fingerprint + trust classification
IMMUTABLE RAW (content-addressed)
  ↓ deterministic normalization + span map
VERSIONED NORMALIZED EVIDENCE
  ↓ structured LLM proposal
STRUCTURED LEDGER
  ↓ conflict/entity resolution in staging
MATERIALIZED MARKDOWN KNOWLEDGE GRAPH
  ↓ FTS/QMD + graph traversal
SKILLS / WORKFLOWS
  ↓
DRAFT ARTIFACTS / OUTBOX
  ↓ human approval where required
RESULTS + ANALYTICS + FEEDBACK
  └──────────────────────→ new source ingest
```

## 5. Каноническая структура репозитория

```text
raytsystem/
├── AGENTS.md                      # короткие invariants, commands, routing
├── WORK.md                        # explicit ChatGPT Work bootstrap → same skills
├── CLAUDE.md                      # thin compatibility pointer → AGENTS.md/skills
├── WIKI.md                        # human-readable schema; JSON Schema validates it
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .gitignore
├── .env.example
│
├── config/
│   ├── raytsystem.toml               # project settings and thresholds
│   ├── policies.yaml              # trust, approvals, network, retention
│   ├── sources.yaml               # adapter routing
│   └── schemas/                   # versioned JSON Schemas
│
├── inbox/                         # untrusted drop zone; not canonical
├── _raw/
│   ├── blobs/sha256/              # immutable content-addressed evidence
│   ├── revisions/sha256/          # immutable typed SourceRevision records
│   └── manifests/sources.jsonl    # rebuildable ordered projection
│
├── normalized/
│   └── <source_revision_id>/<normalization_id>/
│       ├── document.txt           # normalized inert text; never active Markdown
│       ├── document.json          # lossless parser representation when available
│       ├── segments.jsonl         # stable span IDs → pages/timecodes/bboxes
│       └── assets/
│
├── ledger/                        # canonical typed knowledge, generation-addressed
│   ├── objects/sha256/            # immutable typed record blobs
│   ├── generations/               # immutable manifests: IDs → object hashes
│   └── CURRENT                    # atomic pointer to active generation
│
├── knowledge/                     # open this directory as Obsidian vault
│   ├── index.md                   # compact catalog, generated
│   ├── hot.md                     # bounded generated session brief
│   ├── overview.md
│   ├── sources/
│   ├── entities/
│   ├── concepts/
│   ├── claims/
│   ├── decisions/
│   ├── questions/
│   ├── rules/
│   ├── anti-patterns/
│   ├── projects/
│   ├── manual/                    # human-authored notes, re-ingested as sources
│   └── reports/
│
├── skills/
│   ├── raytsystem-ingest/
│   ├── raytsystem-query/
│   ├── raytsystem-lint/
│   ├── raytsystem-save/
│   ├── raytsystem-research/
│   ├── raytsystem-yt/
│   └── roma-yt-*/
│
├── workflows/
│   ├── definitions/               # typed DAG definitions
│   └── youtube/                   # stage contracts and templates
│
├── artifacts/
│   ├── drafts/
│   ├── reports/
│   └── outbox/                    # proposed external actions, never auto-send
│
├── ops/
│   ├── control.sqlite              # durable local coordination; reconstructed from manifests/events
│   ├── runs/<run_id>/manifest.json
│   ├── staging/<run_id>/
│   ├── locks/
│   ├── events.jsonl               # append-only promoted operation log
│   └── decisions/                 # ADRs
│
├── src/raytsystem/
│   ├── cli/
│   ├── contracts/
│   ├── ingestion/
│   ├── knowledge/
│   ├── retrieval/
│   ├── orchestration/
│   ├── policies/
│   └── observability/
│
├── tests/
│   ├── fixtures/
│   ├── golden/
│   ├── adversarial/
│   └── recovery/
│
├── evals/
└── docs/
```

Derived caches (`.raytsystem/index.sqlite`, QMD index, NetworkX export, parser cache) не являются source of truth и должны rebuild-иться одной командой. `ops/control.sqlite` is a separate durable runtime coordination database for unique operation keys, fencing counters and live transactions; committed run manifests/events remain its recovery record, so it is not canonical knowledge and is not Git-tracked.

Нативный code graph следует той же границе:

```text
Canonical state: ledger + evidence + tasks + runs + catalogs
                         ↓ read-only projection
Derived state: FTS5 + knowledge graph + .raytsystem/graph snapshots + communities
                         ↓ bounded typed view
Unified raytsystem Universe (including the Code lens)
```

`.raytsystem/graph/CURRENT` выбирает только активное поколение disposable code projection и никогда
не заменяет `ledger/CURRENT`. Архитектурные/impact запросы идут graph-first при актуальном
snapshot; factual knowledge остаётся evidence-first, а обычный локальный поиск остаётся явно
помеченным fallback. Полный lifecycle и Graphify provenance описаны в
[`docs/11-code-graph-and-execution-plane.md`](11-code-graph-and-execution-plane.md) и ADR-016.

Разделение истины однозначно:

- `_raw/` blobs + immutable revision records — authoritative source evidence; `sources.jsonl` is a rebuildable projection. Restricted blobs/records remain local gitignored/encrypted, while Git receives only opaque safe classification/retention metadata;
- immutable normalization snapshot — canonical citation address для конкретной версии parser/config; смена parser создаёт новый `normalization_id`, а не перезаписывает старые spans;
- `ledger/objects` + `ledger/generations` — canonical structured records, а `ledger/CURRENT` одним pointer swap выбирает активное состояние;
- `knowledge/` — materialized Markdown views из ledger;
- `knowledge/manual/` — единственная human-editable зона vault; её изменения проходят обычный ingest и не переписывают ledger напрямую;
- Git + `ops/events.jsonl` — история promotion и audit trail.

## 6. Data model

### 6.1 Source

| Field | Meaning |
|---|---|
| `source_id` | Stable identity of the logical source |
| `source_revision_id` | Immutable revision derived from source identity + exact content hash |
| `content_sha256` | Hash exact raw bytes |
| `origin` | local path, URL, YouTube ID, connector reference |
| `source_type` | transcript, video, paper, web, image, analytics, chat, etc. |
| `retrieved_at` | Capture timestamp |
| `published_at` | Source publication time if known |
| `trust_class` | primary, official, research, user, community, generated, untrusted |
| `rights` | license/copyright/usage note |
| `raw_path` | immutable blob path |
| `adapter` | name + version |

### 6.2 Segment / Evidence

Stable address inside normalized source:

- `segment_id`;
- source revision ID, `normalization_id` and normalized hash;
- line range, page+bbox or timecode range;
- exact excerpt hash;
- parser and parser version;
- language/modality.

`normalization_id = hash(source_revision_id, adapter, parser_version, normalization_config_hash)`. `segment_id` derives from `normalization_id`, locator and excerpt hash. Normalization snapshots are immutable; a parser/config change creates a new snapshot, so old claim citations remain resolvable.

Locator is a closed discriminated union for text line/character ranges, PDF page+bbox, integer timecodes, image pixel boxes, RFC 6901 JSON pointers and table cell ranges. Citation binds both `segment_id` and exact `excerpt_sha256`.

### 6.3 Claim

| Field | Meaning |
|---|---|
| `claim_id` | Stable ID |
| `statement` | One atomic assertion |
| `relation_ids` | Optional authoritative links to typed Relation records |
| `evidence_ids` | One or more source segments |
| `status` | supported, confirmed, disputed, superseded, retracted, stale |
| `valid_at/invalid_at` | When the claim is true in the world |
| `recorded_at` | When the system learned it |
| `supersedes/contradicts` | Claim-to-claim edges |
| `confidence_components` | source quality, corroboration, recency, extraction certainty |
| `review_ids` | Links to immutable Review records |

Page-level confidence может отображаться, но вычисляется из claims и не заменяет их evidence.

`proposed` belongs to `ProposalItem`, not a canonical Claim. Only promotion resolves a proposal item to a stable canonical ID/status.

### 6.4 Entity / Relation

- stable entity ID;
- canonical label and aliases;
- entity type;
- merge/split history;
- claim-backed relations only;
- source and temporal lineage.

### 6.5 Contract registry

M0 publishes versioned schemas for every durable boundary, not only the headline objects:

| Contract | Required responsibility |
|---|---|
| `Source` / `SourceRevision` | Logical identity, exact bytes, origin, rights, trust and sensitivity |
| `Normalization` / `Segment` | Parser/config identity, immutable snapshot and stable evidence locators |
| `Claim` / `Entity` / `Relation` / `Review` | Canonical knowledge records and temporal/provenance edges |
| `ProposalRequest` / `ProposalResponse` | Model-neutral semantic extraction boundary; no canonical writes |
| `EvidencePack` / `AnswerProposal` / `QueryCitation` | Retrieval inputs, cited synthesis and citation verification |
| `Run` / `Lease` / `PromotionTxn` / `PromotionEvent` / `GitCheckpointEvent` | Idempotency, fencing, WAL state, event/commit reconciliation and recovery |
| `GenerationEntry` / `LedgerGeneration` | Full active record snapshot, parent, schema registry and commit metadata |
| `SensitivityDecision` / `PolicyDecision` / `ApprovalRecord` | Classification, decision, action/destination/payload hash, policy hash, approver, time and expiry |
| `Artifact` / `OutboxAction` / `Feedback` | Draft lifecycle, proposed side effect and later measurement |

Each schema specifies required/optional fields, canonical JSON serialization, ID derivation, allowed state transitions and forward/backward compatibility. `ApprovalRecord` is hash-bound to the exact action and artifact; a free-form approval status is insufficient.

### 6.6 Run

```text
operation_key = hash(
  operation_type,
  input_hashes,
  parser_version,
  schema_version,
  prompt_or_skill_hash,
  model_id,
  pipeline_version,
  relevant_config_hash
)
```

Keys are stage-scoped rather than one monolithic run hash: capture excludes model fields, normalization binds parser/config, proposal binds evidence + prompt/skill/model, and promotion binds the validated proposal hash + parent generation + policy/schema versions. Irrelevant components serialize explicitly as `none`.

States:

`queued → running → staging → validating → awaiting_review → awaiting_approval? → promoting → promoted → reconciling → succeeded`

Failure states:

Before the canonical pointer commit: `retryable_failed | terminal_failed | quarantined | cancelled`. After `promoted`, recovery remains `reconciling`; it never reports a pre-commit failure or repeats the pointer swap.

Уникальный `operation_key` возвращает существующий successful result либо присоединяется/возобновляет существующий non-terminal run; он не создаёт второй run с тем же смыслом.

Lease includes partition key, owner, expiry/renewal and a monotonically increasing fencing token. Promotion rejects a stale token even if an expired worker later resumes.

### 6.7 Artifact / Feedback

Каждый content artifact хранит:

- inputs and claim IDs;
- skill/prompt/model versions;
- project/stage/run IDs;
- zero or more hash-bound approval record IDs;
- output hash;
- later performance/feedback IDs.

### 6.8 Ownership and projections

| Information type | Canonical store | Human projection | Direct edit path | Rebuild source |
|---|---|---|---|---|
| Sources/revisions/normalizations | `_raw` manifests + immutable normalized snapshots | `knowledge/sources/` | None | Source/normalization records |
| Claims/entities/relations/reviews | Active ledger generation | `knowledge/claims/`, `entities/`, `concepts/` | None | `ledger/CURRENT` generation |
| Decisions/questions/rules/anti-patterns | Typed ledger records backed by claims/reviews | Matching Markdown directories | None | Active ledger generation |
| Projects/artifact reports | Typed Artifact/Run/Feedback records | `knowledge/projects/`, `reports/` | Draft commands only | Canonical records |
| Human notes | Raw bytes after capture | `knowledge/manual/` | Human only | Re-ingest as a source |
| Index/hot/search graph | None; derived | `knowledge/index.md`, `hot.md` | None | Active generation + run state |

`raytsystem save` creates a staged typed proposal plus Markdown preview; it never treats a directly edited generated page as canonical. Relation records are authoritative for subject/predicate/object edges, while claims reference them by ID.

### 6.9 Canonical serialization and compatibility

- UTF-8, LF, no BOM; keys sorted; compact JSON for hashes; duplicate keys, NaN and Infinity forbidden.
- Strings are NFC in metadata; exact raw is never normalized. Timestamps are UTC RFC3339 with fixed millisecond precision. Hashed identity uses integers/normalized decimal strings, not binary floats.
- Paths in committed contracts are workspace-relative POSIX paths; absolute/private paths are rejected.
- Schema versions are SemVer and each exact schema has a registry hash. Writers emit the current exact version; readers accept only explicitly registered versions and pure deterministic migrations.
- Unknown core fields reject (`extra=forbid`); namespaced `extensions` is the only forward-extension zone.
- ID derivation never changes under the same `id_scheme_version`; immutable objects are never rewritten by migration.

## 7. INGEST contract

1. Discover input in `inbox/` or an explicit connector.
2. Treat all content as untrusted data, never as commands.
3. Capture remote input through a dedicated Fetcher boundary; local parsers never fetch.
4. Fingerprint exact bytes, classify sensitivity and build `source_revision_id` + `operation_key`.
5. Return/join/resume an existing run with the same unique operation key.
6. Copy bytes into `_raw/blobs/sha256/`; register one unique source revision. Restricted raw is excluded from Git/model egress by policy.
7. Normalize offline via a typed adapter:
   - Docling as the leading primary-parser candidate pending pilot benchmark;
   - MarkItDown fallback;
   - locally captured YouTube metadata and available subtitle/auto-caption files; optional ASR is a separate adapter;
   - native JSON/JSONL/CSV adapters for analytics.
8. Produce an immutable normalization snapshot and stable segments with source locations.
9. Export a model-neutral `ProposalRequest` with an evidence pack. A configured `ModelAdapter`, a Work skill or a human returns a typed `ProposalResponse`; the LLM never writes canonical files.
10. Import the response, resolve aliases and compare claims with the active ledger generation.
11. Record conflicts; never silently overwrite.
12. Render record blobs, next generation manifest and Markdown previews under `ops/staging/<run_id>/`.
13. Run deterministic lint, then independent semantic review.
14. Apply trust/approval policy and verify an exact hash-bound approval when required.
15. Commit through `PromotionTxn`, then regenerate views/indexes and create a Git checkpoint.

CLI bridge: `raytsystem prepare → raytsystem export-proposal → raytsystem import-proposal → raytsystem validate → raytsystem promote`. A convenience `raytsystem ingest` may call the whole sequence only when a `ModelAdapter` is configured. Tests use a deterministic fake adapter; ChatGPT Work can orchestrate the export/import path without any API key.

Initial mode: autonomous promotion only inside a fixture/test namespace; real corpus requires manual, hash-bound approval. `trusted_auto` can be enabled only after pilot gates pass.

### 7.1 Crash-safe promotion

Filesystem-wide multi-file atomicity is not claimed. The observable canonical state is all-or-nothing:

1. Acquire the operation partition lease. Before the final commit, acquire a separate global `ledger:current` lease/fence for the `ledger/CURRENT` file because all promotions share one parent pointer.
2. Write/fsync immutable ledger object blobs and a complete next-generation manifest.
3. Create a `PromotionTxn` WAL record: `prepared → committing` with parent generation, output hashes, unique `event_id` and expected fencing token.
4. Recheck policy, approval hash, active parent generation, partition token and global pointer fencing token inside one control-DB transaction.
5. Atomically replace the small `ledger/CURRENT` pointer; this is the canonical commit point.
6. Mark the WAL `committed`, append/reconcile the unique event, materialize Markdown, rebuild derived indexes and checkpoint Git. The event cannot embed the SHA of the commit that contains itself; commits instead carry `raytsystem-Event` and `raytsystem-Generation` trailers, and a derived reconciler maps event IDs to commit SHAs.
7. Mark the run `succeeded` only after post-commit validation. On restart, reconcile any committed pointer with missing event/view/index/checkpoint without repeating the canonical commit.

Kill/fault tests cover every boundary, including raw registration, each durable write, pointer swap, event append, Git checkpoint and the final run state. Trailing partial JSONL records are quarantined and recovered from unique IDs plus the WAL.

### 7.2 Capture and extraction trust boundaries

- `Fetcher` is the only networked intake process. It permits explicit `https` sources by default, revalidates scheme/DNS/IP after every redirect, blocks loopback/private/link-local/metadata addresses, enforces allowlists where possible, timeouts, byte/redirect quotas and content-type checks.
- `yt-dlp` belongs to Fetcher: it captures metadata, thumbnails and available manual/auto-generated subtitles. If captions are absent, no transcript is invented; an approved local/cloud ASR adapter may create a separate derived source.
- `Extractor` receives local immutable bytes, runs offline in a quarantined temporary root and has no shell, secrets or network. It enforces symlink/path/archive containment, decompression ratio, file-count, recursion, page/time and memory limits.
- Sensitivity classification happens before Git staging or model egress. Detected secrets/PII trigger a policy decision: quarantine, block, or create an approved redacted derivative while preserving restricted exact raw outside Git.

## 8. QUERY contract

1. Classify intent: fact, comparison, relationship, temporal, corpus-wide, workflow decision.
2. Retrieve progressively:
   - current hot/index;
   - structured metadata/aliases in SQLite FTS5;
   - QMD hybrid candidates;
   - one-hop graph expansion;
   - PageIndex only for a selected long document if benchmarked.
3. Fetch source spans for every material claim.
4. Synthesize with inline links to knowledge pages and exact source references.
5. Separate facts, inference, uncertainty and missing evidence.
6. If evidence is insufficient, return a gap instead of model-memory speculation.
7. Save valuable synthesis only as a staged, cited page.

## 9. LINT contract

### Deterministic on every promotion

- schema and schema version;
- stable/unique IDs;
- raw hash integrity;
- source/evidence resolvability;
- claim citation coverage;
- local wikilinks;
- duplicate aliases and slug collisions;
- index/hot freshness;
- secrets/PII patterns in logs and generated artifacts;
- stage ownership and illegal canonical writes;
- operation-key uniqueness;
- master artifact consistency with structured stage outputs.

### Semantic on schedule or before release

- contradictions and unsupported synthesis;
- stale/superseded claims;
- duplicate concepts/entities;
- missing cross-references;
- orphan pages and coverage gaps;
- weak rules inferred from small or incomparable samples;
- source authority and temporal mismatch.

Fixes are proposals. Deletion, claim resolution and entity merge require review.

## 10. Retrieval architecture

| Scale/need | Default |
|---|---|
| Early graph | `index.md` + `rg` + SQLite FTS5 |
| Hundreds/thousands of pages | QMD hybrid search behind `SearchAdapter` |
| Relations/orphans/hubs | Derived NetworkX graph |
| One very long structured document | Optional PageIndex benchmark |
| Multi-user/remote/very large corpus | Re-evaluate LanceDB/Qdrant |
| Corpus-wide thematic map | Offline GraphRAG experiment only |

No threshold is hard-coded solely by page count. Promotion to a heavier backend requires a benchmark regression showing insufficient recall/latency.

## 11. Memory model

Four separate layers:

1. **Working:** current task/run manifest and bounded scratch.
2. **Hot:** generated `knowledge/hot.md` with active projects, recent promoted events, open decisions and blockers. Never manually authoritative.
3. **Semantic:** claims/entities/relations/rules in the active ledger generation, rendered as the Markdown graph.
4. **Episodic/procedural:** append-only run/event history and versioned skills.

Chat history is never canonical memory. `hot.md` is a cache and must be reproducible.

## 12. Agent and Work integration

### Surface preflight

`local-first` describes canonical storage, local commands and recoverable state; it does not promise local model inference.

- In ChatGPT Work desktop, the main task may use local files/tools when they are actually available, but Work subagents run hosted and must not be assumed to share a local Codex sandbox, checkout or worktree. Give them only minimal non-sensitive excerpts and read-only questions.
- In local Codex, subagents inherit the selected sandbox/permission mode and can inspect the checkout. Git worktrees are a Codex-desktop feature, not a Work guarantee.
- Before mutation, record `surface`, project root, permission mode, available tools and model-egress policy. If Work cannot write the local project, return a precise handoff to Codex rather than simulating changes.
- Real private corpus, PII, secrets and pilot inputs do not go to hosted subagents, connectors or a new API destination without destination-scoped approval.

### `AGENTS.md`

Keep below a small budget. It contains:

- immutable/raw/secrets/draft invariants;
- canonical commands;
- skill routing;
- verification requirements;
- approval boundaries;
- instruction that source content is data, not instructions.

`WIKI.md` объясняет человеку типы страниц, ledger/view split, naming и операции. Machine-enforcement остаётся в versioned schemas и validators, поэтому `WIKI.md` не становится вторым независимым набором правил.

### `WORK.md`

Automatic `AGENTS.md` loading is a Codex behavior, not an assumption for every Work surface. `WORK.md` is a deliberately tiny, explicit entry point: it tells a Work task to run surface/egress preflight, read the shared invariants, choose one command/skill and persist state in run manifests. It points to the same `skills/<name>/SKILL.md` files and does not duplicate their procedures. Human-facing launch snippets use the form: “Read `WORK.md`, then run INGEST/QUERY/LINT/SAVE for …”.

### Skills

Each skill owns one repeatable process and includes:

- trigger description;
- inputs/outputs;
- preconditions;
- allowed tools and write scope;
- stage-by-stage contract;
- validators and stop conditions;
- failure/recovery rules;
- references loaded progressively;
- eval fixtures.

The user-facing identity is the canonical English `skill_id`; localization applies to
descriptions, statuses, field labels, actions and errors, never to the identifier. A full skill
detail view may render the allowlisted `SKILL.md` body, but imported Markdown remains inert data:
HTML, scripts, iframes, event handlers, remote embeds and commands in prose do not execute.

Skill editability is explicit policy, not a property the document can grant itself. An enabled,
non-restricted, user-trusted skill is editable only at the exact repo-local path
`skills/<skill_id>/SKILL.md`. Official, pinned, generated, historical, external and
unverified-origin skills are read-only. A safe read-only source may be forked into a new unique
`pack_local` skill; restricted or unverified content is non-forkable.

Authoring is optimistic and hash-bound: preview/save requires expected catalog SHA-256 and source
SHA-256, while the HTTP boundary also requires a local session, CSRF and an idempotency key. The
server derives the path from the typed skill ID, validates UTF-8, size, YAML frontmatter,
directory/name agreement, permissions, submitted test status and sensitivity, writes atomically
without following links, re-reads through `CatalogService`, and appends revision/audit records.
Concurrent changes return a conflict and are never auto-merged. The effective test status after
edit or fork is always `pending`; saving never executes the skill or an external tool. See
ADR-034.

### Agent presentation

`AgentDefinition` (catalog plane) and `DigitalEmployee` (execution plane) remain distinct storage
records but are one user-facing Agent. The read model joins them only by stable
`agent_id` / `employee_id`; a definition-only Agent remains catalog-only, while an
execution-only/mismatched record is visible as degraded state. Canonical English names remain
unchanged; Russian roles, descriptions and statuses are separate presentation fields.

### Subagents

- main agent owns decisions, write plan and final promotion;
- parallel agents do research, retrieval, fact checking, test/log/security review;
- no two agents write the same partition;
- independent reviewer does not inherit the writer's full reasoning context;
- in Work, hosted reviewers receive the minimum safe evidence and never own local writes;
- in Codex, worktrees may isolate large write tasks, but promotion stays serialized.

### Optional runtime

OpenAI Agents SDK is phase-7 only, when API-driven runs, durable HITL state, tracing or webhooks become necessary. ChatGPT Work subscription is not treated as an API entitlement.

## 13. YouTube domain pack

The original `/yt` promise is preserved, but the single master document becomes a projection, not the only state.

```text
brief
  → verdict
  → research + fact table
  → titles ─────────┐
  → thumbnails ─────┼→ packaging
  → script ─────────┘
  → presentation
  → warmup
  → master.md + run manifest
```

Structured stage artifacts:

```text
artifacts/drafts/youtube/<project_id>/
├── brief.yaml
├── verdict.json
├── research.md
├── fact_table.jsonl
├── titles.json
├── thumbnails.json
├── script.md
├── presentation.yaml
├── packaging.json
├── warmup.md
├── master.md
└── run.json
```

Rules:

- facts in script/deck/packaging must resolve to fact-table IDs;
- internal analytics claims must resolve to local dataset queries;
- title/thumbnail numeric promises must agree;
- output remains DRAFT until explicit publish approval;
- post-release analytics link back to exact package/run version;
- production rules store cohort, sample size, effect, confidence, scope, exceptions and last validation date.

Existing local `yt`, `roma-yt-*`, `shorts-*`, `watch` and research skills become seed assets. Import process: inventory → hash → diff Claude/Codex variants → choose canonical → port to repo scope → add contracts/tests → record provenance.

## 14. Security and approval matrix

| Action | Default |
|---|---|
| Read workspace/public primary sources | Autonomous |
| Write staging/drafts/tests inside project | Autonomous |
| Edit an eligible local skill after hash-bound preview | Autonomous local write; session/CSRF/idempotency/CAS and audit required |
| Fork a safe read-only skill into a local copy | Explicit preview and confirmation; source remains unchanged |
| Edit official, pinned, restricted, historical or external skill in place | Forbidden |
| Promote synthetic fixture knowledge in fixture namespace | Autonomous during implementation |
| Promote any real-corpus knowledge | Manual, exact artifact hash bound |
| Add pinned project dependency | Allowed only within approved plan; record license/SBOM |
| Global install, system package, any model-weight/embedding-pack download | Approval with artifact, size, hash, license and destination |
| Read secrets/keychains/private unrelated folders | Forbidden unless explicit scoped approval |
| Network call with private content | Approval + destination disclosure |
| Delete/merge canonical knowledge | Approval |
| Send/publish/upload/message/change external system | Approval, always |
| Git commit | Autonomous green checkpoint |
| Git push/PR/release | Approval |

Defense in depth:

- sandbox/least privilege;
- data/control separation;
- quarantine path;
- egress allowlist where possible;
- no shell/network in source extraction;
- secret scanning and log redaction;
- signed/pinned dependencies where practical;
- adversarial source fixtures;
- outbox pattern for side effects.

Secret/privacy gates are scoped and reportable, not absolute claims: tests name scanners, inspected artifact/log paths, planted cases and known blind spots. Restricted raw may intentionally contain sensitive bytes; it stays access-controlled and out of Git/model egress, while committed manifests retain only safe hashes/classification/retention metadata.

Rollback is a compensating promotion, never deletion of history: append a rollback event, retract/supersede affected claims, point a new generation at the restored active set and reconcile its event with the Git commit SHA. Old object blobs, generations and events remain auditable.

## 15. Stack

| Layer | Choice | Why |
|---|---|---|
| Runtime | Python 3.12 | Best compatibility with Docling/agent/eval ecosystem |
| Package manager | `uv` + lockfile | Fast, reproducible, mature |
| CLI | Typer or Click | Typed local commands; no server required |
| Contracts | Pydantic v2 + versioned JSON Schema | Structured model outputs and deterministic validation |
| Canonical storage | Content-addressed JSON records + generation manifests/pointer + safe Git metadata | Inspectable, portable, crash-safe active state |
| Runtime control | `ops/control.sqlite` + committed run/event records | Unique keys, fenced leases and transaction recovery |
| Derived index | `.raytsystem/index.sqlite` | Aliases and FTS5 projection; delete/rebuild safely |
| Document parsing | Native adapters first; Docling candidate; MarkItDown fallback | Benchmark quality; parser code/models licensed separately |
| Remote media capture | `yt-dlp` Fetcher + optional ASR adapter | Available captions/metadata first; never invent transcript |
| Search | FTS5 → optional QMD Node/Bun sidecar | QMD code and downloaded model artifacts have separate licenses/approvals |
| Graph analytics | NetworkX derived graph | No graph DB duplication |
| Tests | pytest + golden/adversarial/recovery fixtures | Hard quality gates |
| Prompt/agent eval | Promptfoo after model integration | Regression and red team |
| Observability | JSONL first, OpenTelemetry-compatible fields | Local-first and vendor-neutral |
| UI | Obsidian optional | Excellent UX without canonical lock-in |
| Hosting | None in MVP | `ops/staging` holds run transactions; Codex worktrees are optional Git isolation |
| Auth | OS permissions + sandbox | No network service in MVP |

## 15a. Platform subsystems (implemented)

Поверх контуров знаний и исполнения работает платформенный слой качества и управления. Все его
подсистемы включаются флагами `config/platform.yaml`, живут в изолированном append-only
`ops/platform.sqlite` (hash-chained события, оптимистичные ревизии, идемпотентные квитанции) и не
пишут в канонические знания:

```text
platform plane (ops/platform.sqlite)
    ├── evaluation laboratory (детерминированные assertions, immutable baselines, LLM judges отдельно)
    ├── local trace model (task → run → model/tool/approval spans; OTLP export off-by-default)
    ├── replay / fork / compare (без повторения внешних side effects, без переноса approvals)
    ├── policy simulator (тот же evaluate_execution_policy, что и runtime preflight; dry-run)
    ├── emergency controls + circuit breakers (machine-enforced, manual approval-gated recovery)
    ├── MCP governance (catalog_only; per-tool policy; исполнение за отдельным флагом)
    ├── ACP adapter / A2A gateway (loopback-only contract boundaries)
    ├── pack lifecycle (install ≠ activate; hash-bound rollback; локальный registry)
    ├── workflow DAG (типизированные узлы; deterministic_command только по operation ID)
    ├── notifications inbox / contract-only outbox (allowlist + egress approval; отправка не реализована)
    ├── secrets encryption (честный unavailable без key provider)
    ├── backup / export / restore (4 вида bundle, redaction report, restore в пустую директорию)
    └── init / templates / migrations (идемпотентный init, журналируемый migration registry)
```

Точный операторский справочник — `docs/12-platform-capabilities.md`; решения — ADR-020…ADR-032.

## 16. Explicit non-goals for v1

- no graph database;
- no hosted dashboard;
- no multi-user collaboration server;
- no n8n as core;
- no autonomous publishing;
- no mandatory OpenAI API key;
- no migration of the full historical archive before the vertical slice passes;
- no self-modifying skills promoted without eval and review.
