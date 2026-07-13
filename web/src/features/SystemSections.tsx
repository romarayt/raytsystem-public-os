import {
  Activity,
  Archive,
  Ban,
  Beaker,
  Bell,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  GitCompare,
  Package,
  Play,
  Radio,
  RefreshCw,
  Repeat2,
  ShieldAlert,
  ShieldCheck,
  Timer,
  Workflow,
  Wrench
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatDate, shortId } from "../api";
import { ActionBoundary, OperationalNotice } from "../components/FeatureState";
import { EmptyState, ErrorState, LoadingState, StatusPill } from "../components/StatePanel";
import {
  useEmergencyCommand,
  useNotificationTransition,
  usePlatformFeatures,
  usePolicySimulation,
  useSystemSection,
  useTraceDetail
} from "../featureHooks";
import type {
  EmergencyAction,
  PlatformFeatures,
  PolicySimulation,
  SystemSectionId,
  SystemSectionSnapshot,
  TraceSpan,
  TraceSummary
} from "../featureTypes";
import { fieldLabel, statusLabel } from "../presentation";
import "./systems.css";

const sectionMeta: Record<
  SystemSectionId,
  { label: string; detail: string; icon: typeof Beaker; collections: string[] }
> = {
  evals: {
    label: "Оценки",
    detail: "Детерминированные проверки, результаты и неизменяемые baseline",
    icon: Beaker,
    collections: ["runs", "baselines"]
  },
  traces: {
    label: "Трассировка",
    detail: "Локальные trace/span без raw prompts и секретов",
    icon: Activity,
    collections: ["traces"]
  },
  replays: {
    label: "Повторы",
    detail: "Replay и fork по зафиксированному execution record",
    icon: Repeat2,
    collections: ["plans"]
  },
  policies: {
    label: "Политики",
    detail: "Dry-run тем же policy engine, что защищает выполнение",
    icon: ShieldCheck,
    collections: []
  },
  tools: {
    label: "Инструменты",
    detail: "MCP-каталог, схемы и per-tool разрешения",
    icon: Wrench,
    collections: ["servers", "tools"]
  },
  protocols: {
    label: "Протоколы",
    detail: "Опциональные ACP и loopback-only A2A границы",
    icon: Radio,
    collections: []
  },
  packages: {
    label: "Пакеты",
    detail: "Карантин, проверка, установка и отдельная активация",
    icon: Package,
    collections: ["packages", "active"]
  },
  workflows: {
    label: "Процессы",
    detail: "Типизированные DAG без raw shell-команд",
    icon: Workflow,
    collections: ["workflows", "runs"]
  },
  notifications: {
    label: "Уведомления",
    detail: "Локальный inbox и состояния подтверждений",
    icon: Bell,
    collections: ["notifications"]
  },
  backups: {
    label: "Резервные копии",
    detail: "Проверяемые private backup и public export bundles",
    icon: Archive,
    collections: ["backups"]
  }
};

const sectionIds = Object.keys(sectionMeta) as SystemSectionId[];

const collectionCopy: Record<string, string> = {
  runs: "Запуски",
  baselines: "Baseline",
  traces: "Трассы",
  plans: "Планы replay / fork",
  servers: "MCP-серверы",
  tools: "Инструменты",
  packages: "Ревизии пакетов",
  active: "Активные версии",
  workflows: "Workflow DAG",
  notifications: "Локальный inbox",
  backups: "Созданные bundles"
};

const emergencyActions: Array<{ value: EmergencyAction; label: string; effect: string }> = [
  { value: "pause_all_employees", label: "Поставить всех сотрудников на паузу", effect: "Новые и текущие циклы сотрудников блокируются" },
  { value: "cancel_active_runs", label: "Отменить активные запуски", effect: "Активные запуски получают машинный запрет продолжения" },
  { value: "disable_runtime_execution", label: "Отключить выполнение", effect: "Runtime gate отклоняет любое новое выполнение" },
  { value: "disable_network_adapters", label: "Отключить сетевые адаптеры", effect: "Любой сетевой маршрут блокируется политикой" },
  { value: "disable_external_providers", label: "Отключить внешних провайдеров", effect: "Модельный egress становится недоступен" },
  { value: "freeze_task_checkout", label: "Заморозить checkout задач", effect: "Новые рабочие области задач не выдаются" },
  { value: "revoke_runtime_sessions", label: "Отозвать runtime-сессии", effect: "Текущие runtime-сессии считаются недействительными" },
  { value: "revoke_pending_approvals", label: "Отозвать ожидающие approvals", effect: "Старые approvals нельзя использовать" },
  { value: "emergency_budget_stop", label: "Аварийно остановить бюджет", effect: "Новые расходы и токены блокируются" }
];

function sectionFromLocation(): SystemSectionId {
  const candidate = new URLSearchParams(window.location.search).get("section") as SystemSectionId | null;
  return candidate && sectionIds.includes(candidate) ? candidate : "evals";
}

export function SystemSections() {
  const [section, setSection] = useState<SystemSectionId>(sectionFromLocation);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const features = usePlatformFeatures();
  const snapshot = useSystemSection(section);

  useEffect(() => {
    const onPopState = () => setSection(sectionFromLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const selectSection = (next: SystemSectionId) => {
    window.history.pushState({}, "", `/systems?section=${next}`);
    setSection(next);
  };

  return (
    <div className="route systems-route">
      <SystemsOverview features={features.data} loading={features.isLoading} error={features.error} />
      <nav className="systems-tabs" aria-label="Системные возможности" role="tablist">
        {sectionIds.map((key, index) => {
          const Icon = sectionMeta[key].icon;
          return (
            <button
              ref={(node) => { tabRefs.current[index] = node; }}
              id={`systems-tab-${key}`}
              type="button"
              role="tab"
              aria-selected={section === key}
              aria-controls="systems-section-panel"
              tabIndex={section === key ? 0 : -1}
              className={section === key ? "active" : ""}
              key={key}
              onClick={() => selectSection(key)}
              onKeyDown={(event) => {
                let next: number | undefined;
                if (event.key === "ArrowRight") next = (index + 1) % sectionIds.length;
                else if (event.key === "ArrowLeft") next = (index - 1 + sectionIds.length) % sectionIds.length;
                else if (event.key === "Home") next = 0;
                else if (event.key === "End") next = sectionIds.length - 1;
                if (next === undefined) return;
                event.preventDefault();
                selectSection(sectionIds[next]);
                tabRefs.current[next]?.focus();
              }}
            >
              <Icon size={16} aria-hidden="true" />
              <span>{sectionMeta[key].label}</span>
            </button>
          );
        })}
      </nav>
      <section id="systems-section-panel" role="tabpanel" className="systems-stage panel" aria-labelledby={`systems-tab-${section}`} tabIndex={0}>
        <header className="systems-stage-header">
          <span className="systems-stage-icon">{(() => { const Icon = sectionMeta[section].icon; return <Icon size={21} />; })()}</span>
          <span><span className="eyebrow">{sectionMeta[section].label}</span><h2>{sectionMeta[section].detail}</h2></span>
          {snapshot.data ? <StatusPill status={snapshot.data.state} /> : null}
        </header>
        {snapshot.isLoading ? <LoadingState label={`Читаем раздел «${sectionMeta[section].label}»…`} /> : null}
        {snapshot.isError ? <ErrorState error={snapshot.error} onRetry={() => void snapshot.refetch()} /> : null}
        {snapshot.data ? (
          <SystemSectionBody section={section} snapshot={snapshot.data} features={features.data} />
        ) : null}
      </section>
    </div>
  );
}

function SystemsOverview({
  features,
  loading,
  error
}: {
  features: PlatformFeatures | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) return <div className="systems-overview panel"><LoadingState label="Проверяем системный контур…" /></div>;
  if (error || !features) return <div className="systems-overview panel"><ErrorState error={error} /></div>;
  const enabled = Object.values(features.active_feature_flags ?? {}).filter(Boolean).length;
  const total = Object.keys(features.active_feature_flags ?? {}).length;
  return (
    <section className="systems-overview panel panel-glow">
      <div className="systems-radar" aria-hidden="true"><i /><i /><i /><b>OS</b></div>
      <div className="systems-overview-copy">
        <span className="eyebrow">Flight recorder · локальный control plane</span>
        <h2>Система видит качество, политику и восстановление.</h2>
        <p>Все данные читаются из отдельного операционного хранилища. Внешнее выполнение, отправка и сетевой A2A остаются выключенными.</p>
      </div>
      <div className="systems-metrics" aria-label="Состояние raytsystem">
        <Metric value={`${enabled}/${total}`} label="функций включено" status={features.state} />
        <Metric value={String(features.event_backlog ?? 0)} label="audit events" />
        <Metric value={formatBytes(features.trace_storage_size ?? 0)} label="trace storage" />
        <Metric value={String(features.eval_regression_count ?? 0)} label="регрессий" status={(features.eval_regression_count ?? 0) > 0 ? "blocked" : "ready"} />
      </div>
      <div className="systems-healthline">
        <span><CircleDot size={13} /> store <b>{features.platform_store ?? "unavailable"}</b></span>
        <span>MCP <b>{features.mcp_health ?? "unavailable"}</b></span>
        <span>ACP <b>{features.acp_health ?? "disabled"}</b></span>
        <span>A2A <b>{features.a2a_state ?? "disabled"}</b></span>
        <span>шифрование <b>{features.encryption_provider?.state ?? "unavailable"}</b></span>
      </div>
    </section>
  );
}

function Metric({ value, label, status = "ready" }: { value: string; label: string; status?: string }) {
  return <div className={`systems-metric systems-metric-${status}`}><strong>{value}</strong><span>{label}</span></div>;
}

function SystemSectionBody({
  section,
  snapshot,
  features
}: {
  section: SystemSectionId;
  snapshot: SystemSectionSnapshot;
  features: PlatformFeatures | undefined;
}) {
  const isEmpty = sectionMeta[section].collections.length > 0 && sectionMeta[section].collections.every((key) => records(snapshot, key).length === 0);
  const visibleState = snapshot.state === "ready" && isEmpty ? "empty" : snapshot.state;
  return (
    <div className="systems-stage-body">
      <OperationalNotice state={visibleState} />
      {section === "traces" ? <TracePanel traces={snapshot.traces ?? []} /> : null}
      {section === "policies" ? <PolicyPanel snapshot={snapshot} features={features} /> : null}
      {section === "protocols" ? <ProtocolPanel snapshot={snapshot} /> : null}
      {section === "notifications" ? <NotificationsPanel snapshot={snapshot} /> : null}
      {!(["traces", "policies", "protocols", "notifications"] as SystemSectionId[]).includes(section) ? (
        <CollectionPanels section={section} snapshot={snapshot} />
      ) : null}
    </div>
  );
}

function CollectionPanels({ section, snapshot }: { section: SystemSectionId; snapshot: SystemSectionSnapshot }) {
  const collections = sectionMeta[section].collections;
  if (collections.every((key) => records(snapshot, key).length === 0)) {
    return (
      <EmptyState title="Проверенных записей ещё нет">
        Раздел готов. Пустое состояние не означает, что операция выполняется в фоне.
      </EmptyState>
    );
  }
  return (
    <div className="systems-collections">
      {collections.map((key) => {
        const items = records(snapshot, key);
        if (!items.length) return null;
        return (
          <section className="systems-collection" key={key}>
            <header><span>{collectionCopy[key] ?? key}</span><b>{items.length}</b></header>
            <div className="systems-record-grid">
              {items.map((item, index) => <RecordCard record={item} key={recordKey(item, index)} />)}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function RecordCard({ record }: { record: Record<string, unknown> }) {
  const title = firstString(record, [
    "name", "title", "eval_run_id", "baseline_id", "replay_plan_id", "workflow_id",
    "workflow_run_id", "notification_id", "revision_id", "server_id", "tool_id", "backup_id",
    "package_id", "record_id", "run_id"
  ]) ?? "Типизированная запись";
  const state = firstString(record, ["state", "status", "outcome", "trust_state"]) ?? "recorded";
  const fields = [
    "suite_id", "target_id", "case_id", "mode", "original_run_id", "new_run_id", "version",
    "content_sha256", "manifest_sha256", "unread", "severity", "created_at", "started_at",
    "completed_at", "cost", "duration_ms", "failed_assertion_ids", "deterministic_score_ids",
    "required_approval_ids", "blocked_side_effect_tool_ids"
  ].filter((key) => record[key] !== undefined).slice(0, 6);
  return (
    <article className="systems-record">
      <header><strong title={title}>{shortId(title, 18, 10)}</strong><StatusPill status={state} /></header>
      <dl>
        {fields.map((key) => <div key={key}><dt>{fieldLabel(key)}</dt><dd>{safeValue(record[key])}</dd></div>)}
      </dl>
    </article>
  );
}

function TracePanel({ traces }: { traces: TraceSummary[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  const activeTraceId = selected && traces.some((trace) => trace.trace_id === selected)
    ? selected
    : traces[0]?.trace_id ?? null;
  const detail = useTraceDetail(activeTraceId);
  if (!traces.length) {
    return <EmptyState title="Трасс пока нет">Первый instrumented run появится здесь после локальной записи trace.</EmptyState>;
  }
  return (
    <div className="trace-console">
      <aside className="trace-list" aria-label="Список трасс">
        {traces.map((trace) => (
          <button type="button" className={activeTraceId === trace.trace_id ? "active" : ""} key={trace.trace_id} onClick={() => setSelected(trace.trace_id)}>
            <span><Activity size={14} /><strong>{shortId(trace.trace_id)}</strong></span>
            <small>{formatDate(trace.created_at)} · {trace.span_count ?? 0} spans</small>
            <StatusPill status={trace.status ?? "unset"} />
            <ChevronRight size={15} />
          </button>
        ))}
      </aside>
      <section className="trace-detail" aria-live="polite">
        {detail.isLoading ? <LoadingState label="Строим waterfall по очищенным span…" /> : null}
        {detail.isError ? <ErrorState error={detail.error} onRetry={() => void detail.refetch()} /> : null}
        {detail.data ? <TraceWaterfall trace={detail.data.trace} spans={detail.data.spans} /> : null}
      </section>
    </div>
  );
}

function TraceWaterfall({ trace, spans }: { trace: TraceSummary; spans: TraceSpan[] }) {
  const timing = useMemo(() => waterfallTiming(spans), [spans]);
  const totalTokens = (trace.input_tokens ?? 0) + (trace.output_tokens ?? 0) + (trace.cached_tokens ?? 0);
  return (
    <div className="waterfall">
      <header className="waterfall-header">
        <div><span className="eyebrow">Trace {shortId(trace.trace_id)}</span><h3>Run {shortId(trace.root_run_id)}</h3></div>
        <div className="waterfall-totals">
          <span><Timer size={13} /> {timing.duration} ms</span>
          <span><Activity size={13} /> {totalTokens} tokens</span>
          <span>≈ {String(trace.estimated_cost ?? 0)}</span>
        </div>
      </header>
      <div className="waterfall-scale"><span>0</span><i /><span>{timing.duration} ms</span></div>
      <div className="waterfall-rows">
        {spans.map((span) => {
          const bar = timing.bars[span.span_id] ?? { left: 0, width: 2 };
          return (
            <article className="waterfall-row" key={span.span_id}>
              <div className="waterfall-label">
                <strong>{span.operation_name}</strong>
                <small>{span.span_kind}{span.retry_count ? ` · retry ${span.retry_count}` : ""}</small>
              </div>
              <div className="waterfall-track"><i className={`span-${span.status ?? "unset"}`} style={{ left: `${bar.left}%`, width: `${bar.width}%` }} /></div>
              <div className="waterfall-facts">
                <StatusPill status={span.status ?? "unset"} />
                <code>{span.duration_ms ?? 0} ms</code>
              </div>
              <div className="waterfall-meta">
                {span.model ? <span>model {span.model}</span> : null}
                {span.tool_name ? <span>tool {span.tool_name}</span> : null}
                {span.approval_id ? <span>approval {shortId(span.approval_id)}</span> : null}
                {span.error_code ? <span className="trace-error">error {span.error_code}</span> : null}
                <span>{(span.input_tokens ?? 0) + (span.output_tokens ?? 0) + (span.cached_tokens ?? 0)} tokens</span>
                <span>redaction {span.redaction_status ?? "not_required"}</span>
              </div>
            </article>
          );
        })}
      </div>
      {!spans.length ? <EmptyState title="Span ещё не записаны">Trace существует, но детальные интервалы пока отсутствуют.</EmptyState> : null}
    </div>
  );
}

function PolicyPanel({ snapshot, features }: { snapshot: SystemSectionSnapshot; features: PlatformFeatures | undefined }) {
  const simulation = usePolicySimulation();
  const emergency = useEmergencyCommand();
  const [action, setAction] = useState<EmergencyAction>("disable_runtime_execution");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const selectedAction = emergencyActions.find((item) => item.value === action) ?? emergencyActions[2];
  const runSimulation = () => {
    if (!snapshot.policy_sha256) return;
    simulation.mutate({
      plan: {
        plan_id: "plan_ui_policy_probe",
        employee_id: "employee_ui_probe",
        task_id: "task_ui_probe",
        task_revision: 1,
        runtime_id: "runtime_policy_probe",
        provider: null,
        model: null,
        workspace_mode: "read_only",
        read_roots: [],
        write_roots: [],
        network_access: "none",
        network_destinations: [],
        requested_tools: ["read_catalog"],
        requested_secrets: [],
        graph_scope: [],
        knowledge_scope: [],
        token_budget: 0,
        cost_budget: "0",
        potential_side_effects: [],
        policy_sha256: snapshot.policy_sha256
      },
      granted_approval_kinds: []
    });
  };
  const activateEmergency = () => {
    const expected = features?.emergency_state?.snapshot_id;
    if (!expected || reason.trim().length < 3 || !confirmed) return;
    emergency.mutate({ actions: [action], reason: reason.trim(), approval_id: null, expected_snapshot_id: expected });
  };
  return (
    <div className="policy-console">
      <section className="policy-simulator-card">
        <header><span><Play size={18} /><strong>Policy simulator</strong></span><StatusPill status="ready" label="только dry-run" /></header>
        <p>Проверка не создаёт workspace, не вызывает модель, не выдаёт секреты и не меняет задачу.</p>
        <div className="policy-facts">
          <span><small>Workspace</small><b>{snapshot.workspace_default ?? "staging_only"}</b></span>
          <span><small>Сеть</small><b>{snapshot.network_default ?? "none"}</b></span>
          <span><small>Внешние действия</small><b>{snapshot.external_actions_default ?? "approval_required"}</b></span>
          <span><small>Policy hash</small><code>{shortId(snapshot.policy_sha256)}</code></span>
        </div>
        <ActionBoundary scope="Локальный тестовый execution plan" effect="Только решение allowed / blocked / approval required" approval="Не требуется: side effects отсутствуют" recovery="Изменений состояния нет">
          <button className="primary-button" type="button" onClick={runSimulation} disabled={simulation.isPending || !snapshot.policy_sha256}>
            {simulation.isPending ? <RefreshCw className="spin" size={15} /> : <ShieldCheck size={15} />} Проверить запуск
          </button>
        </ActionBoundary>
        {simulation.isError ? <ErrorState error={simulation.error} /> : null}
        {simulation.data ? <SimulationResult simulation={simulation.data} /> : null}
      </section>
      <section className="emergency-card">
        <header><span><ShieldAlert size={18} /><strong>Аварийный контур</strong></span><StatusPill status={features?.emergency_state?.state ?? "unavailable"} /></header>
        <p>Команда записывается идемпотентно и немедленно попадает в тот же machine-enforced gate.</p>
        <label>Действие<select value={action} onChange={(event) => { setAction(event.target.value as EmergencyAction); setConfirmed(false); }}>{emergencyActions.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
        <label>Причина<textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={4096} placeholder="Почему требуется аварийная блокировка" /></label>
        <ActionBoundary scope="Всё локальное рабочее пространство" effect={selectedAction.effect} approval="Локальная emergency authority; действие остаётся в audit" recovery="Только вручную, со свежим approval">
          <label className="emergency-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />Понимаю область и способ восстановления</label>
          <button className="danger-button" type="button" onClick={activateEmergency} disabled={emergency.isPending || !confirmed || reason.trim().length < 3 || features?.emergency_state?.state === "unavailable"}>
            <Ban size={15} /> Применить блокировку
          </button>
        </ActionBoundary>
        {features?.emergency_state?.active_actions?.length ? <div className="active-emergency"><strong>Активно</strong>{features.emergency_state.active_actions.map((item) => <code key={item}>{item}</code>)}</div> : null}
        {emergency.isError ? <ErrorState error={emergency.error} /> : null}
        {emergency.data ? <div className="action-success" role="status"><CheckCircle2 size={18} /><span><strong>Блокировка подтверждена</strong><small>{emergency.data.recovery}</small></span></div> : null}
      </section>
    </div>
  );
}

function SimulationResult({ simulation }: { simulation: PolicySimulation }) {
  return (
    <div className={`simulation-result simulation-${simulation.outcome}`} role="status">
      <header><GitCompare size={17} /><strong>{statusLabel(simulation.outcome)}</strong><StatusPill status={simulation.outcome} /></header>
      <dl>
        <div><dt>Required approvals</dt><dd>{simulation.required_approvals.length ? simulation.required_approvals.join(", ") : "не требуются"}</dd></div>
        <div><dt>Potential side effects</dt><dd>{simulation.potential_side_effects.length ? simulation.potential_side_effects.join(", ") : "отсутствуют"}</dd></div>
        <div><dt>Allowed tools</dt><dd>{simulation.allowed_tools.join(", ") || "нет"}</dd></div>
        <div><dt>Policy hash</dt><dd><code>{shortId(simulation.policy_sha256)}</code></dd></div>
      </dl>
    </div>
  );
}

const notificationTransitions: Array<{
  from: string[];
  next: "read" | "acknowledged" | "resolved";
  label: string;
}> = [
  { from: ["unread"], next: "read", label: "Прочитано" },
  { from: ["unread", "read"], next: "acknowledged", label: "Подтвердить" },
  { from: ["read", "acknowledged"], next: "resolved", label: "Решено" }
];

function NotificationsPanel({ snapshot }: { snapshot: SystemSectionSnapshot }) {
  const transition = useNotificationTransition();
  const items = records(snapshot, "notifications");
  if (!items.length) {
    return (
      <EmptyState title="Входящих уведомлений нет">
        Approvals, блокировки, регрессии и события безопасности появятся здесь.
      </EmptyState>
    );
  }
  return (
    <div className="systems-collections">
      <section className="systems-collection">
        <header><span>{collectionCopy.notifications}</span><b>{items.length}</b></header>
        {transition.isError ? <ErrorState error={transition.error} /> : null}
        <div className="systems-record-grid">
          {items.map((item, index) => {
            const notificationId = firstString(item, ["notification_id"]);
            const state = firstString(item, ["state"]) ?? "unread";
            const actions = notificationTransitions.filter((option) => option.from.includes(state));
            return (
              <article className="systems-record" key={recordKey(item, index)}>
                <header>
                  <strong title={firstString(item, ["title", "notification_type"]) ?? "Уведомление"}>
                    {shortId(firstString(item, ["title", "notification_type"]) ?? "Уведомление", 24, 6)}
                  </strong>
                  <StatusPill status={state} />
                </header>
                <dl>
                  {["notification_type", "severity", "related_object_id", "created_at"]
                    .filter((key) => item[key] !== undefined)
                    .map((key) => (
                      <div key={key}><dt>{fieldLabel(key)}</dt><dd>{safeValue(item[key])}</dd></div>
                    ))}
                </dl>
                {notificationId && actions.length ? (
                  <div className="notification-actions">
                    {actions.map((option) => (
                      <button
                        type="button"
                        key={option.next}
                        disabled={transition.isPending}
                        onClick={() =>
                          transition.mutate({
                            notification_id: notificationId,
                            state: option.next,
                            expected_snapshot_id: snapshot.snapshot_id
                          })
                        }
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function ProtocolPanel({ snapshot }: { snapshot: SystemSectionSnapshot }) {
  const protocols = [
    { label: "ACP adapter", data: asRecord(snapshot.acp), boundary: "Общий runtime gate; native adapters остаются независимыми" },
    { label: "A2A gateway", data: asRecord(snapshot.a2a), boundary: "Только loopback, без сетевой экспозиции и без канонической записи" }
  ];
  return (
    <div className="protocol-grid">
      {protocols.map(({ label, data, boundary }) => (
        <article className="protocol-card" key={label}>
          <header><Radio size={18} /><strong>{label}</strong><StatusPill status={firstString(data, ["state"]) ?? "unavailable"} /></header>
          <p>{boundary}</p>
          <dl>
            <div><dt>Записей</dt><dd>{arraySize(data, "sessions") + arraySize(data, "requests")}</dd></div>
            <div><dt>Сетевая экспозиция</dt><dd>{data?.network_exposure === true ? "включена" : "выключена"}</dd></div>
            <div><dt>Snapshot</dt><dd><code>{shortId(firstString(data, ["snapshot_id"]))}</code></dd></div>
          </dl>
        </article>
      ))}
    </div>
  );
}

function records(snapshot: SystemSectionSnapshot, key: string): Array<Record<string, unknown>> {
  const value = snapshot[key];
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function firstString(record: Record<string, unknown> | null, keys: string[]): string | null {
  if (!record) return null;
  for (const key of keys) {
    if (typeof record[key] === "string" && record[key]) return String(record[key]);
  }
  return null;
}

function arraySize(record: Record<string, unknown> | null, key: string): number {
  return Array.isArray(record?.[key]) ? record[key].length : 0;
}

function safeValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value.includes("T") && !Number.isNaN(Date.parse(value)) ? formatDate(value) : shortId(value, 24, 10);
  if (Array.isArray(value)) return value.length ? value.map((item) => typeof item === "string" ? shortId(item) : "типизированная запись").join(", ") : "нет";
  return "структурированные данные";
}

function recordKey(record: Record<string, unknown>, index: number): string {
  return firstString(record, ["eval_run_id", "baseline_id", "trace_id", "replay_plan_id", "notification_id", "workflow_id", "workflow_run_id", "revision_id", "backup_id", "record_id"]) ?? `record-${index}`;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function waterfallTiming(spans: TraceSpan[]): { duration: number; bars: Record<string, { left: number; width: number }> } {
  const values = spans.map((span) => {
    const start = Date.parse(span.started_at);
    const fallbackDuration = Math.max(0, span.duration_ms ?? 0);
    const parsedEnd = span.ended_at ? Date.parse(span.ended_at) : Number.NaN;
    const safeStart = Number.isFinite(start) ? start : 0;
    const end = Number.isFinite(parsedEnd) ? parsedEnd : safeStart + fallbackDuration;
    return { id: span.span_id, start: safeStart, end: Math.max(safeStart, end) };
  });
  if (!values.length) return { duration: 0, bars: {} };
  const start = Math.min(...values.map((value) => value.start));
  const end = Math.max(...values.map((value) => value.end));
  const range = Math.max(1, end - start);
  const bars: Record<string, { left: number; width: number }> = {};
  for (const value of values) {
    bars[value.id] = {
      left: Math.max(0, Math.min(100, ((value.start - start) / range) * 100)),
      width: Math.max(1.5, Math.min(100, ((value.end - value.start) / range) * 100))
    };
  }
  return { duration: Math.round(range), bars };
}
