# raytsystem landscape and product verdict

Date: 2026-07-11
Status: accepted research input for the web control-plane milestone

## Answer

There is no reviewed permissive open-source product that combines all of the following in one
coherent system: a polished agent control plane, durable tasks, inspectable agent context,
skills/agent registries, a provenance-first temporal knowledge graph and a safe local runtime.

raytsystem should therefore remain its own deterministic kernel and adapt six proven product
patterns rather than fork a monolith:

1. Paperclip-style mission control, tasks, approvals and cost/audit visibility.
2. OpenHands-style runtime adapters and explicit sandbox/workspace boundaries.
3. LangGraph-style checkpoints, interrupts and run inspection.
4. Letta ADE-style context and memory inspection.
5. Open WebUI/Hermes-style skills, tools and agent catalog discovery.
6. Graphiti/GraphRAG-style temporal and community graph lenses, while retaining raytsystem evidence
   validation and canonical ledgers.

## Product Lens verdict: GO

**Idea:** add a universal, local-first web control plane to the existing provenance-first kernel.

**Hypothesis:** knowledge workers and builders need one surface to understand what agents know,
what they are doing, why an artifact exists and which action requires approval. A local raytsystem
that makes those chains inspectable will reduce context setup and agent supervision cost without
trading away control.

**Evidence:**

- the supplied Mission Control reference validates the demand for a unified tasks/agents surface;
- the supplied RUBRIC reference validates the spatial knowledge-universe metaphor;
- Paperclip, Hermes, OpenHands, Letta, Flowise and Open WebUI independently converge on task,
  registry, context and observability surfaces;
- none of those reviewed products combines those surfaces with raytsystem's immutable raw evidence,
  claim provenance, contradiction lifecycle and crash-safe promotion.

**Risks:** an ornamental graph, unsafe loopback HTTP, workflow-canvas sprawl, vendor-centric
navigation, false claims of agent execution and a license that is not actually open source.

**Minimum proof:** one-command local UI with verified workspace snapshots, a real append-only task
board, an inert agents/skills/context catalog, run/safety views and a useful interactive graph where
every factual node can lead to evidence.

## Supplied references

### Mission Control screenshot

The screenshot is consistent with the Hermes Agentic Operating System integration described by
Julian Goldie: Claude, OpenClaw, Hermes, Paperclip, mission control, Kanban and shared memory. It is
a product/UX reference, not an approved code or asset donor. Public materials do not establish a
permissive license for that exact integrated UI.

Useful ideas:

- persistent navigation and global command palette;
- tasks, pipelines, artifacts and agents visible in one workspace;
- per-agent identity and live state;
- local-first dashboard rather than terminal-only operation.

Corrections for raytsystem:

- navigation is organized by user intent, not vendor names;
- chat is attached to a task/run rather than becoming the whole product;
- every action shows capability, policy, provenance and approval state;
- external systems are adapters, not embedded applications presented as one OS.

### RUBRIC Second Brain screenshot

The screenshot is consistent with RUBRIC / RoboNuggets' Second Brain demonstration. Its strongest
idea is not the decoration but the concentric semantic model:

| Reference ring | raytsystem interpretation |
|---|---|
| central `CLAUDE.md` | current workspace/mission plus typed instruction documents |
| skills | capabilities and procedures |
| memory | sources, claims, entities and context snapshots |
| routines | tasks, flows, triggers and schedules |
| applications | runtime and connector adapters |

raytsystem adds the missing evidence, approvals, runs, artifacts, recovery and temporal truth layers.
The orbital view is a projection, never the data model.

## Decision matrix

| Product | Strong pattern to adapt | Do not inherit |
|---|---|---|
| [Paperclip](https://github.com/paperclipai/paperclip) | mission-to-task traceability, atomic task ownership, approvals, budgets and audit | company/org-chart metaphor as the universal domain model |
| [OpenHands Agent Canvas](https://github.com/OpenHands/agent-canvas) | frontend separated from one or more runtime servers; explicit workspace/sandbox choices | unrestricted host execution or coding-only assumptions |
| [LangGraph](https://github.com/langchain-ai/langgraph) | durable checkpoints, interrupts, replay/fork and step inspection | hosted account dependency or conflating execution and knowledge graphs |
| [Letta ADE](https://docs.letta.com/guides/ade/overview) | show exactly what an agent sees: instructions, tools, memory and state | treating mutable agent memory as verified knowledge |
| [Hermes dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard) | searchable skill/tool catalog and local management UI | automatic trust of installed community skills |
| [Hermes Kanban](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md) | human-in-the-loop board, dependency-aware decomposition and restricted orchestrator profile | visual transitions that bypass policy/state validation |
| [Open WebUI](https://docs.openwebui.com/features/) | approachable workspace primitives and progressive discovery | current branding-restricted code as a white-label base |
| [Flowise](https://docs.flowiseai.com/) | progressive complexity and visual debugging/HITL | visual workflow JSON as canonical business state |
| [Dify](https://github.com/langgenius/dify) | onboarding, catalogs and workflow ergonomics | modified license and multi-tenant/product constraints |
| [n8n](https://n8n.io/ai-agents/) | integration catalog and deterministic execution history | Sustainable Use License and canvas spaghetti |
| [Agent Zero](https://github.com/agent0ai/agent-zero) | artifact-first cowork surfaces and visible browser/desktop actions | a generic UI path to unrestricted machine execution |
| [Graphiti](https://github.com/getzep/graphiti) | validity windows, episodes and provenance-aware temporal edges | LLM extraction writing canonical truth directly |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | hierarchical community projections for large graphs | mandatory LLM-heavy indexing in the local v1 core |
| [OpenFang](https://github.com/RightNow-AI/openfang) | capability manifests, adapters and one-command packaging | pre-1.0 mega-kernel scope |
| [AutoGen Studio](https://microsoft.github.io/autogen/stable/index.html) | declarative galleries and team-flow visualization | a maintenance-mode runtime as the new foundation |

## Product differentiation

The memorable interaction is a live, typed path such as:

```text
source bytes → stable span → supported claim → skill/context → agent/task/run → artifact
```

Users can select any object and answer:

- Why does this exist?
- Which exact source supports it?
- Which agent, skill and context snapshot were used?
- What changed or was superseded?
- Which approval or recovery gate blocks the next step?

That is a useful wow effect rather than a graph screensaver.

## Confidence and open questions

Confidence is high for the architecture direction because the patterns triangulate across primary
product documentation and the existing kernel already implements the hardest integrity layer.
Open questions for a later public release are product naming, community pack governance,
multi-user/remote security and runtime adapter certification. They do not block a loopback-only
open-source control plane.
