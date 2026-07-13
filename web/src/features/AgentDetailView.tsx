import {
  ArrowLeft,
  BookOpenText,
  Boxes,
  Clock3,
  KeyRound,
  PlayCircle,
  ShieldCheck
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { shortId } from "../api";
import { Surface, SurfaceContent, SurfaceTabs } from "../components/SurfaceTabs";
import { ErrorState, LoadingState, StatusPill } from "../components/StatePanel";
import { useAgentDetail } from "../executionHooks";
import {
  canonicalAgentName,
  capabilityLabel,
  catalogDescription,
  localizedCatalogLabel,
  roleLabel,
  statusLabel
} from "../presentation";
import {
  accessValueLabel,
  activityLabel,
  agentReadinessLabel,
  agentReasonLabel,
  booleanLabel,
  boundaryLabel,
  filesystemModeLabel,
  limitationLabel,
  safeValueLabel
} from "./agentPresentation";

type AgentDetailTab = "overview" | "instruction" | "skills" | "runtime" | "access" | "history";

const detailTabs = [
  { id: "overview", label: "Обзор", icon: <Boxes size={15} /> },
  { id: "instruction", label: "Инструкция", icon: <BookOpenText size={15} /> },
  { id: "skills", label: "Skills", icon: <ShieldCheck size={15} /> },
  { id: "runtime", label: "Runtime", icon: <PlayCircle size={15} /> },
  { id: "access", label: "Доступ", icon: <KeyRound size={15} /> },
  { id: "history", label: "История", icon: <Clock3 size={15} /> }
] as const;

type DetailValue = string | number | boolean | null;

function DetailList({ values }: { values: Array<[string, DetailValue]> }) {
  return (
    <dl className="surface-detail-list">
      {values.map(([label, value], index) => (
        <div key={`${label}-${index}`}>
          <dt>{label}</dt>
          <dd>{value === null || value === "" ? "Не указано" : typeof value === "boolean" ? booleanLabel(value) : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function EmptyState({ children }: { children: string }) {
  return <p className="muted-copy">{children}</p>;
}

function safeRecordText(record: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function safeRecordNumber(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function shortSafeId(value: string | null, left = 12, right = 6): string {
  return value ? shortId(value, left, right) : "Не указано";
}

function dateTimeLabel(value: string | null): string {
  if (!value) return "Не указано";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Не указано";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC"
  }).format(date);
}

function contextSourcesLabel(paths: string[]): string {
  if (!paths.length) return "Не заявлены";
  return `${paths.length} · значения путей скрыты в безопасной проекции`;
}

function tokenLimitLabel(tokens: { input_tokens: number; output_tokens: number; cached_tokens: number }): string {
  return `вход ${tokens.input_tokens} · выход ${tokens.output_tokens} · кэш ${tokens.cached_tokens}`;
}

export function AgentDetailView({
  agentId,
  catalogSha256,
  onBack,
  onOpenSkill
}: {
  agentId: string;
  catalogSha256: string;
  onBack: () => void;
  onOpenSkill: (skillId: string) => void;
}) {
  const detail = useAgentDetail(agentId, catalogSha256);
  const [tab, setTab] = useState<AgentDetailTab>("overview");
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (detail.data) headingRef.current?.focus();
  }, [agentId, detail.data]);

  if (detail.isLoading) return <LoadingState label="Собираем определение и execution-состояние агента…" />;
  if (detail.isError || !detail.data) {
    return <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />;
  }

  const { agent } = detail.data;
  const panelId = `agent-${agent.agent_id}-panel`;
  const returnToAgentList = () => {
    const returnTargetLabel = `Открыть агента ${canonicalAgentName(agent)}`;
    onBack();
    window.requestAnimationFrame(() => {
      const target = Array.from(document.querySelectorAll<HTMLButtonElement>("button[aria-label]"))
        .find((button) => button.getAttribute("aria-label") === returnTargetLabel);
      target?.focus();
    });
  };
  return (
    <>
      <header className="detail-hero panel">
        <button className="secondary-button" type="button" onClick={returnToAgentList}>
          <ArrowLeft size={15} /> Все агенты
        </button>
        <div className="detail-hero-copy">
          <span className="eyebrow">Роль: {roleLabel(agent.role)}</span>
          <h2 ref={headingRef} tabIndex={-1}>{canonicalAgentName(agent)}</h2>
          <p>{catalogDescription(agent.agent_id, agent.description)}</p>
        </div>
        <div className="detail-hero-state">
          <StatusPill status={agent.readiness} label={agentReadinessLabel(agent.readiness)} />
          <span>{agentReasonLabel(agent.unavailable_reason)}</span>
        </div>
      </header>

      <Surface className="detail-tab-surface" aria-label={`Подробности агента ${agent.name}`}>
        <SurfaceTabs
          tabs={detailTabs.map((item) => ({
            ...item,
            panelId
          }))}
          activeTab={tab}
          onTabChange={setTab}
          ariaLabel="Разделы подробностей агента"
          id={`agent-detail-tabs-${agent.agent_id}`}
        />
        <SurfaceContent id={panelId} labelledBy={`agent-detail-tabs-${agent.agent_id}-tab-${tab}`}>
          {tab === "overview" ? (
            <section className="detail-section panel">
              <h3>Профиль агента</h3>
              <DetailList values={[
                ["Имя", canonicalAgentName(agent)],
                ["ID", agent.agent_id],
                ["Роль", roleLabel(agent.role)],
                ["Описание", catalogDescription(agent.agent_id, agent.description)],
                ["Пакет", agent.pack_id],
                ["Версия", agent.version],
                ["Статус определения", statusLabel(agent.definition_state)],
                ["Назначение", agent.definition?.capabilities.map(capabilityLabel).join(", ") ?? null]
              ]} />
            </section>
          ) : null}

          {tab === "instruction" ? (
            <div className="detail-section-grid">
              <section className="detail-section panel">
                <h3>Контекст и возможности</h3>
                <DetailList values={[
                  ["Контекстные источники", contextSourcesLabel(detail.data.instruction.context_paths)],
                  ["Возможности", detail.data.instruction.capabilities.map(capabilityLabel).join(", ") || "Не заявлены"],
                  ["Ограничения", detail.data.instruction.limitations.map(limitationLabel).join(" · ") || "Не заявлены"]
                ]} />
              </section>
              <section className="detail-section panel">
                <h3>Системные границы</h3>
                {Object.entries(detail.data.instruction.system_boundaries).length ? (
                  <DetailList values={Object.entries(detail.data.instruction.system_boundaries).map(([key, value]) => [
                    boundaryLabel(key),
                    safeValueLabel(value)
                  ])} />
                ) : <EmptyState>Системные границы не заявлены.</EmptyState>}
              </section>
              <section className="detail-section panel">
                <h3>Безопасная проекция определения</h3>
                {agent.definition ? (
                  <DetailList values={[
                    ["ID", agent.definition.agent_id],
                    ["Имя", canonicalAgentName(agent.definition)],
                    ["Роль", roleLabel(agent.definition.role)],
                    ["Описание", catalogDescription(agent.definition.agent_id, agent.definition.description)],
                    ["Версия", agent.definition.version],
                    ["Пакет", agent.definition.pack_id],
                    ["Адаптер выполнения", agent.definition.runtime_adapter_id],
                    ["Skills", agent.definition.skill_ids.join(", ") || "Не назначены"],
                    ["Запрошенный доступ к файлам", filesystemModeLabel(agent.definition.requested_filesystem_mode)],
                    ["Классы данных", agent.definition.approved_data_classes.map(statusLabel).join(", ") || "Не заявлены"],
                    ["Внешняя передача данных заявлена", agent.definition.egress_declared],
                    ["Определение включено", agent.definition.enabled]
                  ]} />
                ) : <EmptyState>Определение агента отсутствует; показана только execution-запись.</EmptyState>}
              </section>
            </div>
          ) : null}

          {tab === "skills" ? (
            <section className="detail-section panel">
              <h3>Назначенные Skills</h3>
              <div className="related-object-list">
                {detail.data.skills.length ? detail.data.skills.map((skill) => (
                  <button type="button" key={skill.skill_id} onClick={() => onOpenSkill(skill.skill_id)}>
                    <span><strong>{skill.skill_id}</strong><small>{skill.permissions.join(", ") || "Разрешения не заявлены"}</small></span>
                    <StatusPill status={skill.status} />
                  </button>
                )) : <p className="muted-copy">Skills не назначены.</p>}
              </div>
            </section>
          ) : null}

          {tab === "runtime" ? (
            <div className="detail-section-grid">
              <section className="detail-section panel">
                <h3>Состояние выполнения</h3>
                <DetailList values={[
                  ["Адаптер", localizedCatalogLabel(agent.runtime_adapter.adapter_id, agent.runtime_adapter.name)],
                  ["Состояние адаптера", statusLabel(agent.runtime_adapter.state)],
                  ["Статус выполнения", statusLabel(agent.execution_status)],
                  ["Текущая сессия", agent.current_session_id ? shortId(agent.current_session_id) : null],
                  ["Режим рабочей области", filesystemModeLabel(agent.filesystem_policy.mode)],
                  ["Текущая задача", agent.current_task_id ? shortId(agent.current_task_id) : null],
                  ["Параллельность", agent.concurrency_limit],
                  ["Причина блокировки", agentReasonLabel(agent.unavailable_reason)]
                ]} />
              </section>
              <section className="detail-section panel">
                <h3>Сессии</h3>
                {detail.data.runtime.sessions.length ? detail.data.runtime.sessions.map((session) => (
                  <div className="history-row" key={session.session_id}>
                    <code>{shortId(session.session_id)}</code>
                    <StatusPill status={session.status} label={statusLabel(session.status)} />
                    <span>
                      Задача {shortSafeId(session.task_id)} · {session.provider}
                      {session.model ? ` · ${session.model}` : ""} · старт {dateTimeLabel(session.started_at)}
                    </span>
                  </div>
                )) : <EmptyState>Сессии ещё не создавались или хранилище выполнения не инициализировано.</EmptyState>}
              </section>
              <section className="detail-section panel">
                <h3>Бюджеты</h3>
                {detail.data.runtime.budgets.length ? detail.data.runtime.budgets.map((budget) => (
                  <div className="history-row" key={budget.budget_policy_id}>
                    <code>{shortId(budget.budget_policy_id)}</code>
                    <StatusPill status="configured" label="Настроен" />
                    <span>
                      Лимиты: {tokenLimitLabel(budget.token_limit)} · запуски {budget.usage?.run_count ?? 0}/{budget.run_limit}
                      {` · при лимите: ${accessValueLabel(budget.active_run_action)}`}
                    </span>
                  </div>
                )) : <EmptyState>Бюджеты для агента не настроены.</EmptyState>}
              </section>
              <section className="detail-section panel">
                <h3>Аренды задач</h3>
                {detail.data.runtime.leases.length ? detail.data.runtime.leases.map((lease, index) => {
                  const leaseId = safeRecordText(lease, "lease_id");
                  const taskId = safeRecordText(lease, "task_id");
                  const status = safeRecordText(lease, "status") ?? "active";
                  const expiresAt = safeRecordText(lease, "expires_at");
                  const fencingToken = safeRecordNumber(lease, "fencing_token");
                  return (
                    <div className="history-row" key={leaseId ?? `lease-${index}`}>
                      <code>{shortSafeId(leaseId)}</code>
                      <StatusPill status={status} label={statusLabel(status)} />
                      <span>
                        Задача {shortSafeId(taskId)} · до {dateTimeLabel(expiresAt)}
                        {fencingToken === null ? "" : ` · маркер ограждения ${fencingToken}`}
                      </span>
                    </div>
                  );
                }) : <EmptyState>Активных аренд задач нет.</EmptyState>}
                <p className="muted-copy">Команды, рабочие пути и сессия провайдера скрыты из этой проекции.</p>
              </section>
            </div>
          ) : null}

          {tab === "access" ? (
            <div className="detail-section-grid">
              <section className="detail-section panel">
                <h3>Файловая система и данные</h3>
                <DetailList values={[
                  ["Режим", filesystemModeLabel(detail.data.access.filesystem.mode)],
                  ["Чтение рабочей области", detail.data.access.filesystem.allow_workspace_read],
                  ["Запись в staged-область", detail.data.access.filesystem.allow_staged_write],
                  ["Чтение Git", detail.data.access.filesystem.allow_git_read],
                  ["Запись в Git", detail.data.access.filesystem.allow_git_write],
                  ["Классы данных", detail.data.access.data_classes.map(statusLabel).join(", ") || "Не заявлены"]
                ]} />
              </section>
              <section className="detail-section panel">
                <h3>Эффективные разрешения</h3>
                <DetailList values={[
                  ["Чтение рабочей области", detail.data.access.effective_permissions.workspace_read],
                  ["Запись в staged-область", detail.data.access.effective_permissions.staged_write],
                  ["Запись в Git", detail.data.access.effective_permissions.git_write],
                  ["Сетевой доступ", detail.data.access.effective_permissions.network],
                  ["Внешняя передача данных заявлена", detail.data.access.network.egress_declared],
                  ["Подтверждение обязательно", detail.data.access.network.approval_required]
                ]} />
              </section>
              <section className="detail-section panel">
                <h3>Инструменты</h3>
                {detail.data.access.tools.length ? detail.data.access.tools.map((tool, index) => {
                  const toolId = safeRecordText(tool, "tool_id", "id") ?? `tool-${index + 1}`;
                  const provider = safeRecordText(tool, "provider");
                  const access = safeRecordText(tool, "access", "mode");
                  const status = safeRecordText(tool, "health", "status") ?? "available";
                  const approvalPolicy = safeRecordText(tool, "approval_policy");
                  return (
                    <div className="history-row" key={toolId}>
                      <code>{toolId}</code>
                      <StatusPill status={status} label={statusLabel(status)} />
                      <span>
                        {provider ? `Провайдер ${provider}` : "Провайдер не указан"}
                        {access ? ` · ${accessValueLabel(access)}` : ""}
                        {approvalPolicy ? ` · политика: ${accessValueLabel(approvalPolicy)}` : ""}
                      </span>
                    </div>
                  );
                }) : <EmptyState>Связанные инструменты Tool Hub не заявлены.</EmptyState>}
              </section>
              <section className="detail-section panel">
                <h3>Подтверждения</h3>
                {detail.data.access.approvals.length ? detail.data.access.approvals.map((approval) => (
                  <div className="history-row" key={approval.approval_id}>
                    <code>{shortId(approval.approval_id)}</code>
                    <StatusPill status="confirmed" label="Подтверждено" />
                    <span>
                      {activityLabel(approval.action)} · область: {approval.scope.map(activityLabel).join(", ") || "не указана"} · до {dateTimeLabel(approval.expires_at)}
                      {approval.destination_present ? " · назначение скрыто" : ""}
                    </span>
                  </div>
                )) : <EmptyState>Активных подтверждений нет.</EmptyState>}
              </section>
            </div>
          ) : null}

          {tab === "history" ? (
            <section className="detail-section panel">
              <h3>Безопасная история выполнения</h3>
              <DetailList values={[
                ["Ревизия конфигурации", shortId(detail.data.history.configuration_revision, 12, 8)],
                ["Назначения", detail.data.history.assignments.length],
                ["Запуски", detail.data.history.runs.length],
                ["Аудит-события", detail.data.history.audit_events.length]
              ]} />
              <h3 className="section-subheading">Назначения</h3>
              {detail.data.history.assignments.length ? detail.data.history.assignments.map((assignment) => (
                <div className="history-row" key={assignment.assignment_id}>
                  <code>{shortId(assignment.assignment_id)}</code>
                  <StatusPill status="assigned" label={statusLabel("assigned")} />
                  <span>
                    Задача {shortId(assignment.task_id)} · ревизия {assignment.task_revision} · адаптер {assignment.runtime_adapter_id}
                  </span>
                </div>
              )) : <EmptyState>Назначений задач ещё нет.</EmptyState>}

              <h3 className="section-subheading">Запуски</h3>
              {detail.data.history.runs.length ? detail.data.history.runs.map((run) => (
                <div className="history-row" key={run.run_id}>
                  <code>{shortId(run.run_id)}</code>
                  <StatusPill status={run.status} label={statusLabel(run.status)} />
                  <span>
                    Задача {shortId(run.task_id)} · {run.provider}{run.model ? ` / ${run.model}` : ""} · изменено файлов: {run.changed_file_count} · тестов: {run.tests.length}
                  </span>
                </div>
              )) : <EmptyState>Запусков для агента ещё нет.</EmptyState>}

              <h3 className="section-subheading">Аудит-события</h3>
              {detail.data.history.audit_events.length ? detail.data.history.audit_events.map((event, index) => {
                const eventId = safeRecordText(event, "event_id", "audit_event_id") ?? `audit-${index + 1}`;
                const eventType = safeRecordText(event, "event_type", "type", "action") ?? "event";
                const eventStatus = safeRecordText(event, "status", "state") ?? "confirmed";
                const recordedAt = safeRecordText(event, "recorded_at", "created_at");
                const sequence = safeRecordNumber(event, "sequence");
                return (
                  <div className="history-row" key={eventId}>
                    <code>{shortId(eventId)}</code>
                    <StatusPill status={eventStatus} label={statusLabel(eventStatus)} />
                    <span>
                      {activityLabel(eventType)}{sequence === null ? "" : ` · #${sequence}`} · {dateTimeLabel(recordedAt)}
                    </span>
                  </div>
                );
              }) : <EmptyState>Аудит-события для этого агента не зафиксированы.</EmptyState>}
            </section>
          ) : null}
        </SurfaceContent>
      </Surface>
    </>
  );
}
