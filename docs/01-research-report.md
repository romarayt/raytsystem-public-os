# raytsystem — исследовательский отчёт

Дата среза: 2026-07-10

Статус: research complete, implementation not started

Исходный сценарий: `<local-source-path>` (персональный путь удалён из публичного релиза).

## Короткий ответ

Систему из сценария стоит строить, но не буквальной копией. Её правильное ядро — не Obsidian и не один длинный промпт, а замкнутый контур:

`неизменяемые источники → нормализация → доказуемые claims → Markdown-граф → навыки и производственные workflow → артефакты → измерения → новые источники`.

Лучший вариант для этого проекта — local-first, Git-native и artifact-first архитектура. Exact raw остаётся источником доказательств, typed ledger — каноническим структурированным знанием, Markdown — каноническим человекочитаемым интерфейсом, Obsidian — только viewer, а SQLite/QMD — перестраиваемыми индексами. ChatGPT Work/Codex выполняют смысловую работу, но опасные свойства обеспечивает код и данные: content hashes, idempotency keys, staging, fenced leases, generation-pointer promotion, schema validation, approval gates, Git checkpoints, recovery tests и append-only provenance.

## Что на самом деле описывает сценарий

Сценарий показывает уже зрелую Content OS другого репозитория:

- `_raw/` — неизменяемые источники;
- `obsidian/` — LLM-поддерживаемый граф знаний;
- `WIKI.md` и `CLAUDE.md` — схема и постоянные правила;
- `INGEST`, `QUERY`, `LINT` — базовые операции;
- `hot.md` и lifecycle hooks — восстановление контекста;
- skills и subagents — процедурная память;
- `/yt` — производственный DAG ролика;
- аналитика и комментарии возвращаются в граф.

Главное достоинство — compounding: один раз интегрированное знание используется повторно. Главный недостаток демонстрационной версии — большая часть целостности держится на дисциплине агента и текстовых инструкциях. Нет формальных гарантий, что raw не изменится, повторный ingest не создаст дубль, claim действительно подтверждён указанным фрагментом, два writer-агента не испортят index/hot/log, а оборванный run корректно возобновится.

Все числа A1–A20 в сценарии относятся к исходной Content OS и не являются состоянием этого проекта.

## Pre-document baseline workspace

Read-only аудит до создания этого research package показал:

- workspace — пустой Git-репозиторий без единого коммита;
- языки, package manager, runtime, БД, CI, тесты и документация отсутствуют;
- `AGENTS.md`, `WIKI.md`, skills, raw, vault и workflow отсутствуют;
- пользовательских изменений нет;
- `graphify-out` отсутствует.

Это greenfield. Мы можем выбрать одну структуру истины и не наследовать конфликт `raw/wiki` против `_raw/obsidian`. Текущее состояние после исследования отражают четыре созданных документа и, после старта реализации, единый `docs/STATUS.md`.

## Исследовательские ветки и выводы

### 1. Паттерн LLM Wiki подтверждён, но это паттерн, а не production runtime

[Gist Андрея Карпатого](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) определяет три слоя — immutable raw, LLM-maintained Markdown wiki и schema/instructions — плюс операции ingest/query/lint. Он же рекомендует `index.md` для умеренного масштаба и QMD-подобный hybrid search после роста графа. У Gist нет явно указанной лицензии: по [GitHub no-license guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository) мы переиспользуем архитектурную идею с атрибуцией, но не копируем его текст или код verbatim без разрешения.

Что берём:

- raw как source of truth;
- incremental knowledge compilation вместо повторного RAG по raw на каждом запросе;
- index + append-only log;
- ingest/query/lint;
- Obsidian как IDE/просмотрщик;
- хорошую query-синтезу можно сохранить обратно в граф.

Что усиливаем:

- claim-level provenance до точного source span;
- content-addressed raw и техническую проверку immutability;
- typed schemas и stable IDs;
- idempotent run state machine;
- staging + crash-safe observable promotion via WAL/generation pointer;
- temporal validity и supersession вместо молчаливого overwrite;
- evals, recovery и adversarial tests.

### 2. ChatGPT Work подходит для bootstrap и больших workflow, но не заменяет runtime

Официальная документация [ChatGPT Work](https://learn.chatgpt.com/docs/get-started-with-work) описывает режим для многошаговой работы с файлами, plugins и одобренными tools, где пользователь видит прогресс и подтверждает важные действия. [Prompting for Work](https://learn.chatgpt.com/docs/prompting) рекомендует задать outcome, sources, audience, boundaries, review method и final checks. [Long-running work](https://learn.chatgpt.com/docs/long-running-work) требует outcome + constraints + verifiable definition of done.

Work и Codex умеют [параллельных subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), но их execution surface различается. В Work subagents запускаются в hosted environment и не дают local Codex sandbox/approval controls. В local Codex subagents наследуют permission mode, а [Git worktrees доступны только в Codex desktop](https://learn.chatgpt.com/docs/environments/git-worktrees). Поэтому hosted reviewers получают только минимально нужные безопасные excerpts; local writes и promotion остаются у main task/local Codex.

Следствие:

- Work desktop может автономно исследовать, спроектировать и работать с local files/tools, когда они реально доступны; если local write surface отсутствует, нужен точный handoff в Codex desktop;
- `AGENTS.md`, skills, manifests и Git должны хранить знания вне разговора; automatic `AGENTS.md` loading is a Codex guarantee, so ChatGPT Work also needs an explicit thin `WORK.md` bootstrap/router;
- scheduled runs возможны, но [web scheduled tasks](https://learn.chatgpt.com/docs/automations) не держат локальную папку между запусками; для local project нужен desktop app и включённый компьютер;
- durable locks, retries, transactions и exactly-once observable effects реализуются проектным кодом, а не формулировкой промпта;
- local-first здесь означает storage/state/recovery, а не local model inference: private corpus нельзя отправлять hosted subagents или новому API destination без scoped approval.

### 3. Ingestion: Docling — ведущий кандидат; MarkItDown и специализированные adapters — fallback

[Docling](https://github.com/docling-project/docling) — ведущий кандидат на основной normalizer благодаря local processing, OCR, layout, reading order, tables, formulas и lossless JSON. Но выбор должен пройти pilot benchmark; codebase MIT, а отдельные model artifacts имеют собственные licenses и должны получить отдельные hash/license records.

[Microsoft MarkItDown](https://github.com/microsoft/markitdown) (MIT) полезен как лёгкий fallback для простых Office/HTML/CSV/ZIP/audio/YouTube inputs, но сам предупреждает, что выполняет I/O с правами текущего процесса; его нельзя запускать над недоверенными input без sandbox и узких adapters.

Для YouTube нужен отдельный network capture adapter на [yt-dlp](https://github.com/yt-dlp/yt-dlp): metadata, thumbnails, stable video ID и доступные manual/auto-generated subtitles. Он не создаёт transcript, если captions отсутствуют; тогда нужен отдельный approved ASR adapter. После capture normalization работает только с локальными immutable bytes.

### 4. Поиск: progressive disclosure сначала, QMD после первых сотен страниц

[QMD](https://github.com/tobi/qmd) (code MIT, v2.6.3 на срезе, [audit commit `e428df7`](https://github.com/tobi/qmd/commit/e428df76bc0274d9e93eb7ca3e95673315c42e90)) сочетает SQLite FTS5/BM25, on-device embeddings, reciprocal-rank fusion и local reranker; предоставляет CLI, JSON output и MCP. Это сильный кандидат на retrieval sidecar, но не часть Python runtime: нужен Node.js 22+ или Bun, на macOS — подходящий SQLite, а первый запуск загружает около 2 GB model artifacts с отдельными licenses/usage terms.

Рекомендуемая стратегия:

1. direct lookup по stable ID/metadata;
2. `hot.md` и компактный index для текущего контекста;
3. QMD hybrid retrieval;
4. one-hop graph expansion через derived NetworkX graph;
5. повторная проверка каждого используемого claim по normalized source span.

Источники расходятся в формулировках: QMD README предупреждает, что default models optimized for English, а [EmbeddingGemma model card](https://huggingface.co/google/embeddinggemma-300m) заявляет обучение на 100+ языках. Поэтому русский корпус требует empirical benchmark на локальном golden set; среди кандидатов — default stack и multilingual Qwen3 embedding. Любые model weights скачиваются только после approval с указанием размера, hash, license и destination.

### 5. Human-facing graph остаётся в Markdown, а graph database — derived/optional

[Graphiti](https://github.com/getzep/graphiti) хорошо формулирует нужную модель: episodes как provenance, temporal validity windows, supersession и hybrid retrieval. Эти идеи стоит перенести в data contracts.

Сам runtime Graphiti не нужен в ядре: он требует supported graph backend — Neo4j, FalkorDB/FalkorDB Lite или Neptune; Kuzu deprecated. Это создаёт второй источник истины и увеличивает operational cost. Аналогично:

- [Microsoft GraphRAG](https://github.com/microsoft/graphrag) полезен как reference methodology, но сам предупреждает о дорогом indexing и демонстрационном статусе;
- [LightRAG](https://github.com/HKUDS/LightRAG) зрелый и функциональный, но тащит graph/vector storage stack, который дублирует Markdown + QMD;
- Mem0 и активный [Letta Code](https://github.com/letta-ai/letta-code) решают agent-memory/agent runtime, но в этом проекте canonical memory должна быть inspectable в Git; старый [`letta-ai/letta`](https://github.com/letta-ai/letta) обозначен как legacy V1 server.

NetworkX (BSD-3-Clause) достаточно для derived link graph, orphan detection, hubs, connected components и traversal. Его индекс всегда можно перестроить из Markdown.

### 6. Существующие реализации полезны как donor projects, не как готовое ядро

[OpenKB](https://github.com/VectifyAI/OpenKB) (Apache-2.0; [audited commit `bd9fe39`](https://github.com/VectifyAI/OpenKB/commit/bd9fe3989e71fc8012b19eb305662fa307f0a799)) близок к raw → wiki → generators. Полезны adapters, long-document/PageIndex ideas, Skill Factory и CLI UX. Собственный roadmap на срезе всё ещё включает database-backed storage и scaling large collections; до принятия runtime нужны fixture benchmark, operational review и dependency audit.

[Pratiyush/llm-wiki](https://github.com/Pratiyush/llm-wiki) (MIT; [audited commit `b108889`](https://github.com/Pratiyush/llm-wiki/commit/b1088890ee0743810a92577aecad946c6b3eb2d2)) полезен как donor для:

- Codex/Claude/Gemini session adapters;
- page lifecycle `draft → reviewed → verified → stale → archived`;
- confidence/lifecycle lint;
- MCP read surface;
- static export и cross-agent compatibility.

Текущий README уже заявляет quarantine, raw immutability и collision fixes, поэтому его нельзя оценивать по старому snapshot. Перед переносом конкретной функции M4 обязан зафиксировать exact file/commit и проверить её против наших более строгих требований: typed staging, source-span provenance, fenced locks, generation-pointer promotion и crash recovery.

Локально уже установлены пользовательские skills `wiki`, `wiki-ingest`, `wiki-query`, `wiki-lint`, `save`, `yt`, `roma-yt-*`, `shorts-*`, `watch`, `autoresearch`. Их нужно считать ценными domain assets: snapshot с hashes и provenance, затем port в repo-scoped skills и покрытие evals. Нельзя слепо копировать global live versions.

### 7. Agent control plane: persistent instructions должны быть маленькими

Codex [читает `AGENTS.md` до начала работы](https://learn.chatgpt.com/docs/agent-configuration/agents-md), собирает инструкции от root к текущей директории и имеет default budget 32 KiB. Поэтому:

- `AGENTS.md` хранит только invariants, commands и routing;
- `WORK.md` хранит только explicit start/routing instructions для Work и ссылается на те же canonical skills;
- подробные INGEST/QUERY/LINT/YT procedures живут в skills;
- machine-enforced policy живёт в validators/hooks/CI;
- `CLAUDE.md` — thin compatibility pointer, не второй независимый документ правил.

Control loop:

`observe → plan → acquire fenced lease → write in staging → validate → independent critique → human/automatic gate → generation-pointer promote → reconcile/checkpoint → resume/stop`.

### 8. Security: все источники — untrusted data

[OpenAI](https://openai.com/safety/prompt-injections/) определяет prompt injection как инструкции третьей стороны в retrieved content. Для agentic систем опасная комбинация — untrusted source + sensitive sink/tool. [OWASP AI Agent Security](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html) рекомендует least privilege, structured validation, human gates для high-impact actions, bounded retries и separation decision/execution.

Отсюда обязательны:

- raw/web/PDF/transcript/README/comment content никогда не считается инструкцией;
- normalizer не имеет write-доступа к canonical knowledge и secrets;
- no network/tool execution во время semantic extraction, если оно не требуется;
- external actions идут через draft outbox;
- publish/send/delete/pay/change-external-system требуют approval;
- sensitivity classification выполняется до Git/model egress: restricted exact raw остаётся access-controlled и gitignored/encrypted, а в Git попадают только безопасные checksum/classification/retention metadata;
- secrets не попадают в prompt, generated artifacts, traces или Git;
- adversarial fixtures входят в CI.

### 9. Evaluation: deterministic checks сначала, LLM graders потом

Основные invariants проверяются кодом:

- raw hash unchanged;
- schema valid;
- every claim resolves to an existing source span;
- wikilinks resolve;
- second identical ingest is a no-op;
- no unapproved side effect;
- interrupted run resumes without duplicate promotion.

[Promptfoo](https://github.com/promptfoo/promptfoo) (MIT) подходит для model/prompt regression и red teaming после появления model calls. Его configs сами являются executable/trusted artifacts, поэтому adversarial inputs нужно запускать изолированно. OpenAI Agents SDK tracing или OpenTelemetry нужны только после появления автономного API runner; для первых milestones достаточно JSONL run manifests + pytest.

## Матрица решений

| Компонент | Решение | Роль | Почему | Лицензия/ограничение |
|---|---|---|---|---|
| Karpathy LLM Wiki | Reimplement pattern | Архитектурное ядро | Простота, compounding, inspectability | No explicit license; cite idea, do not copy text/code verbatim |
| Локальные `wiki*`, `yt`, `roma-yt-*` skills | Adapt | Domain procedures | Уже отражают workflow Ромы | Пользовательские local assets; snapshot + hashes |
| W3C PROV | Adapt model | Provenance vocabulary | Проверенная модель Entity/Activity/Agent/derivation | W3C standard; RDF runtime не нужен |
| Docling | Candidate dependency | Primary parser benchmark | OCR/layout/lossless local representation | Code MIT; model artifacts separately licensed |
| MarkItDown | Optional adapter | Простые документы/форматы | Лёгкий converter | MIT; sandbox untrusted I/O |
| yt-dlp | Use as Fetcher adapter | YouTube capture | IDs, metadata, available captions, thumbnails | Unlicense + third-party notices; no ASR |
| Obsidian Web Clipper | Use for manual capture | Web intake | Локальный Markdown и assets | MIT |
| SQLite FTS5 | Use | Baseline lexical retrieval | Mature, serverless, rebuildable | SQLite public-domain distribution |
| QMD | Benchmark behind interface | Hybrid retrieval | Local BM25 + vectors + rerank + MCP | Code MIT; Node/Bun sidecar; ~2 GB models with separate terms |
| NetworkX | Use derived-only | Link graph/lint | Достаточно без graph DB | BSD-3-Clause |
| Pratiyush/llm-wiki | Selective adapt | Adapters/lifecycle/MCP/export | Cross-agent implementation; audit `b108889` | MIT; pin exact source file/commit before adaptation |
| OpenKB | Selective adapt | CLI/ingestion/skill UX | Близкий raw→wiki donor; audit `bd9fe39` | Apache-2.0; benchmark incomplete roadmap items |
| nashsu/llm_wiki | Design reference only | Desktop UX/queue/review patterns | Богатый UX и SHA cache | GPL-3.0; [no incorporation into a distributed permissive-only core without GPL compliance](https://www.gnu.org/licenses/gpl-faq.html#IfLibraryIsGPL) |
| PageIndex | Benchmark later | Очень длинные документы | Hierarchical reasoning retrieval | MIT; LLM cost и cloud upsell, не default |
| Graphiti | Borrow data model | Temporal claims/provenance | Хорошая model of truth-over-time | Apache-2.0; Neo4j/FalkorDB(-Lite)/Neptune backend |
| Kuzu | Avoid | Embedded graph DB | Репозиторий архивирован | MIT, но project dead/read-only |
| GraphRAG | Avoid core | Reference/evaluation | Полезные local/global query ideas | MIT; дорогой batch indexing |
| LightRAG | Avoid core | Optional experiment | Много storage backends и KG features | MIT; дублирует canonical graph |
| Mem0 / Letta Code | Avoid core | Reference for memory/runtime | Useful append/temporal ideas | Separate projects/licenses; second memory/runtime source |
| OpenAI Agents SDK | Optional phase 7 | API runner/HITL/tracing | Durable RunState, sessions, approvals | MIT; API billing отдельно от Work |
| LangGraph/Temporal | Defer | Heavy durability | Нужны только при server-grade orchestration | Дополнительная инфраструктура до доказанной нужды |
| Promptfoo | Use after model integration | Evals/red teaming | CI-friendly prompt/agent regression | MIT; configs treated as trusted code |
| pre-commit + markdownlint-cli2 + lychee | Use | Local quality gate | Fast deterministic checks before commit | Permissive; domain lint всё равно пишем сами |
| Obsidian | Viewer only | Human IDE | Great local Markdown graph UX | Proprietary application; no canonical dependency |
| n8n | Edge integration only | Connectors | Удобно для внешних triggers | Sustainable Use License; не core OSS |

## Что нельзя копировать буквально

1. Ручной `hot.md` как единственную память. Он должен генерироваться из current runs, recent promoted events и open decisions.
2. «Прочитай index и 3–5 страниц» без retrieval/routing criteria.
3. Page-level `confidence: high/medium/low` без claim-level evidence.
4. Direct writes LLM в canonical wiki.
5. Один master-document как единственный machine state. Нужны typed stage artifacts; master.md — projection.
6. Параллельных writer-agents без file ownership, fenced leases и, в Codex, optional worktree isolation.
7. Hooks как единственный enforcement layer.
8. Автоматическую публикацию или отправку.
9. Большие внутренние числа сценария как baseline нового проекта.

## Противоречия и открытые вопросы

- Исходный Content OS repository не приложен. Мы видим сценарий и global skills, но не можем проверить WIKI.md, analytics и production data A1–A20. План включает importer, а не миграцию вслепую.
- Не определён первый реальный corpus для pilot. Нужны 10–20 репрезентативных источников: transcript, PDF, web article, CSV/JSONL analytics, image/thumbnail.
- Не решено, должны ли крупные raw binaries синхронизироваться через Git LFS, external backup или оставаться локально с committed manifests.
- Не определён бюджет cloud/API calls. MVP поэтому должен работать с ChatGPT Work/Codex и deterministic CLI без обязательного OpenAI API key.
- QMD multilingual configuration требует benchmark на русском corpus; выбор модели нельзя делать только по README.

## Уверенность

Высокая по принципам архитектуры, текущему состоянию workspace, возможностям Work/Codex и выбору artifact-first core. Средняя по конкретным версиям rapidly evolving LLM tooling: перед установкой каждую зависимость нужно повторно проверить, pin by lockfile и записать license/provenance. Средняя по migration plan, пока не предоставлен исходный Content OS repository и реальный pilot corpus.

## Definition of done исследования

- сценарий прочитан полностью;
- workspace audited read-only;
- официальные Work/Codex capabilities проверены;
- первичные репозитории и licenses сравнены;
- локальные donor skills изучены;
- target architecture, build/adapt/avoid decisions и риски определены;
- implementation пока не начат.
