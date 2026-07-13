import {
  Activity,
  Bot,
  Clock3,
  FileClock,
  Filter,
  Gauge,
  Search,
  ShieldCheck
} from "lucide-react";
import { useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { formatDate, shortId } from "../api";
import { ErrorState, EmptyState, LoadingState, StatusPill } from "../components/StatePanel";
import { useDigitalEmployees, useExecutionRuns } from "../executionHooks";
import type { DigitalEmployeeView, ExecutionRunView } from "../executionTypes";
import { roleLabel, statusLabel } from "../presentation";
import type { Selection } from "../types";

interface ExecutionViewProps {
  onSelect: (selection: Selection) => void;
}

function BoundaryNotice({ children, tone = "gold" }: { children: ReactNode; tone?: "gold" | "cyan" }) {
  return (
    <div className="trust-strip" role="status">
      <span className={tone === "cyan" ? "local-indicator" : "boundary-chip"}>
        <ShieldCheck size={14} aria-hidden="true" /> {children}
      </span>
      <span>GET · read-only</span>
      <span>скрытые команды и пути не выдаются</span>
    </div>
  );
}

function employeeAccent(status: string): string {
  if (status === "running") return "var(--cyan)";
  if (["blocked", "error"].includes(status)) return "var(--rose)";
  if (["idle", "assigned"].includes(status)) return "var(--mint)";
  return "var(--periwinkle)";
}

function employeeReason(reason: string): string {
  const labels: Record<string, string> = {
    digital_employees_disabled: "сотрудники отключены",
    runtime_execution_disabled: "runtime отключён",
    runtime_adapter_disabled: "адаптер отключён",
    catalog_definition_disabled: "профиль не активирован",
    operational_state_uninitialized: "только каталог",
    configuration_revision_changed: "конфигурация изменилась",
    persisted_operational_state: "операционное состояние"
  };
  return labels[reason] ?? statusLabel(reason);
}

function filesystemLabel(mode: string): string {
  if (mode === "task_worktree") return "изолированный worktree";
  if (mode === "workspace_root_readonly") return "корень read-only";
  return mode;
}

function employeeSelection(employee: DigitalEmployeeView, snapshotId: string): Selection {
  return {
    id: employee.employee_id,
    kind: "employee",
    label: employee.name,
    status: employee.status,
    subtitle: employee.description,
    metadata: {
      role: roleLabel(employee.role),
      runtime_adapter_id: employee.runtime_adapter_id,
      state_source: employee.state_source,
      reason_code: employee.reason_code,
      current_task_id: employee.current_task_id ?? "none",
      current_session_id: employee.current_session_id ?? "none",
      configuration_current:
        employee.configuration_current === null
          ? "uninitialized"
          : String(employee.configuration_current)
    },
    snapshotId
  };
}

export function DigitalEmployeesView({ onSelect }: ExecutionViewProps) {
  const employees = useDigitalEmployees();
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("ru-RU");
    if (!needle) return employees.data?.employees ?? [];
    return (employees.data?.employees ?? []).filter((employee) =>
      [
        employee.name,
        employee.role,
        employee.description,
        employee.employee_id,
        employee.runtime_adapter_id,
        employee.status
      ]
        .join(" ")
        .toLocaleLowerCase("ru-RU")
        .includes(needle)
    );
  }, [employees.data?.employees, query]);

  if (employees.isLoading) {
    return <LoadingState label="Сверяем цифровых сотрудников с текущим каталогом…" />;
  }
  if (employees.isError || !employees.data) {
    return <ErrorState error={employees.error} onRetry={() => void employees.refetch()} />;
  }

  const disabled = !employees.data.features.digital_employees_enabled;
  const catalogOnly = employees.data.state === "catalog_only" || employees.data.storage_state === "uninitialized";
  return (
    <div className="route-list">
      <div className="route-tools">
        <label className="search-field">
          <Search size={16} aria-hidden="true" />
          <input
            aria-label="Найти цифрового сотрудника"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Имя, роль, адаптер или ID"
          />
        </label>
        <span className="inert-badge">
          <Bot size={14} aria-hidden="true" /> {employees.data.total_catalog_employees} в каталоге
        </span>
      </div>

      {disabled ? (
        <BoundaryNotice>Цифровые сотрудники выключены feature gate</BoundaryNotice>
      ) : catalogOnly ? (
        <BoundaryNotice tone="cyan">
          Каталог готов; операционное хранилище ещё не инициализировано
        </BoundaryNotice>
      ) : (
        <BoundaryNotice tone="cyan">Состояние прочитано из локального execution store</BoundaryNotice>
      )}

      {!filtered.length ? (
        <EmptyState
          title={query ? "Сотрудники не найдены" : "В каталоге нет цифровых сотрудников"}
          action={
            query ? (
              <button className="secondary-button" type="button" onClick={() => setQuery("")}>
                Сбросить фильтр
              </button>
            ) : undefined
          }
        >
          {query
            ? "Измените запрос или сбросьте фильтр."
            : "Сотрудники появляются только из проверенных AgentDefinition и RuntimeAdapterDefinition."}
        </EmptyState>
      ) : (
        <div className="catalog-grid agent-grid" aria-label="Цифровые сотрудники">
          {filtered.map((employee, index) => (
            <button
              className="agent-card panel"
              type="button"
              key={employee.employee_id}
              style={
                {
                  "--agent-accent": employeeAccent(employee.status),
                  "--stagger": `${Math.min(index, 8) * 45}ms`,
                  minHeight: 250,
                  padding: 18
                } as CSSProperties
              }
              aria-label={`Открыть сотрудника ${employee.name}`}
              onClick={() => onSelect(employeeSelection(employee, employees.data.snapshot_id))}
            >
              <span className="agent-aura" style={{ marginBottom: 18 }}>
                <Bot size={23} aria-hidden="true" />
              </span>
              <span className="eyebrow">{roleLabel(employee.role)}</span>
              <h3>{employee.name}</h3>
              <p>{employee.description}</p>
              <span className="agent-capabilities">
                <i>{filesystemLabel(employee.filesystem_policy.mode)}</i>
                <i>{employee.enabled_skill_ids.length} навыков</i>
                <i>×{employee.concurrency_limit}</i>
              </span>
              <footer>
                <StatusPill status={employee.status} />
                <span>{employee.current_task_id ? `задача ${shortId(employee.current_task_id)}` : employeeReason(employee.reason_code)}</span>
              </footer>
            </button>
          ))}
        </div>
      )}
      <p className="route-footnote">
        Карточки содержат только очищенную проекцию. Instruction paths и runtime credentials намеренно отсутствуют.
      </p>
    </div>
  );
}

function totalTokens(run: ExecutionRunView): number {
  return run.usage.input_tokens + run.usage.output_tokens + run.usage.cached_tokens;
}

function runSelection(run: ExecutionRunView, snapshotId: string): Selection {
  return {
    id: run.run_id,
    kind: "execution_run",
    label: `${run.provider} · ${shortId(run.run_id, 9, 6)}`,
    status: run.status,
    subtitle: run.summary || `Задача ${shortId(run.task_id)}`,
    metadata: {
      task_id: run.task_id,
      employee_id: run.employee_id,
      runtime_adapter_id: run.runtime_adapter_id,
      provider: run.provider,
      model: run.model ?? "default",
      workspace_id: run.workspace_id,
      graph_scope_id: run.graph_scope_id,
      fencing_token: String(run.fencing_token),
      token_usage: String(totalTokens(run)),
      tests: `${run.tests.filter((test) => test.status === "passed").length}/${run.tests.length}`,
      changed_file_count: String(run.changed_file_count)
    },
    snapshotId
  };
}

function RunMetrics({ runs }: { runs: ExecutionRunView[] }) {
  const running = runs.filter((run) => ["queued", "preparing", "running"].includes(run.status)).length;
  const review = runs.filter((run) => run.status === "review").length;
  const attention = runs.filter((run) => ["blocked", "failed", "cancelled"].includes(run.status)).length;
  const tokens = runs.reduce((total, run) => total + totalTokens(run), 0);
  return (
    <section className="metric-quartet" aria-label="Сводка запусков">
      <div><strong>{running}</strong><span>активно</span></div>
      <div><strong>{review}</strong><span>на проверке</span></div>
      <div><strong>{attention}</strong><span>требуют внимания</span></div>
      <div><strong>{tokens.toLocaleString("ru-RU")}</strong><span>tokens учтено</span></div>
    </section>
  );
}

export function ExecutionRunsView({ onSelect }: ExecutionViewProps) {
  const runs = useExecutionRuns();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("ru-RU");
    return (runs.data?.runs ?? []).filter(
      (run) =>
        (status === "all" || run.status === status) &&
        (!needle ||
          [
            run.run_id,
            run.task_id,
            run.employee_id,
            run.provider,
            run.model ?? "",
            run.status,
            run.summary
          ]
            .join(" ")
            .toLocaleLowerCase("ru-RU")
            .includes(needle))
    );
  }, [query, runs.data?.runs, status]);

  if (runs.isLoading) {
    return <LoadingState label="Читаем очищенную историю execution runs…" />;
  }
  if (runs.isError || !runs.data) {
    return <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />;
  }
  const uninitialized = runs.data.state === "uninitialized" || runs.data.storage_state === "uninitialized";
  const disabled = runs.data.feature_state === "disabled" || !runs.data.features.runtime_execution_enabled;

  if (uninitialized && !runs.data.runs.length) {
    return (
      <div className="route-list">
        <BoundaryNotice>Runtime выключен; execution store ещё не инициализирован</BoundaryNotice>
        <EmptyState title="Запусков пока нет">
          История появится после явно разрешённого запуска. GET не создаёт базу, workspace или graph snapshot.
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="route-list">
      <div className="route-tools">
        <label className="search-field">
          <Search size={16} aria-hidden="true" />
          <input
            aria-label="Найти execution run"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Задача, сотрудник, provider или ID"
          />
        </label>
        <label className="select-field">
          <Filter size={15} aria-hidden="true" />
          <select aria-label="Фильтр execution runs по состоянию" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">Все состояния</option>
            <option value="running">В работе</option>
            <option value="review">На проверке</option>
            <option value="succeeded">Успешно</option>
            <option value="blocked">Заблокировано</option>
            <option value="failed">Ошибка</option>
            <option value="cancelled">Отменено</option>
          </select>
        </label>
        <span className="inert-badge">
          <Activity size={14} aria-hidden="true" /> {runs.data.pagination.returned} записей
        </span>
      </div>

      {disabled ? (
        <BoundaryNotice>Новые запуски отключены; сохранённая история доступна только для чтения</BoundaryNotice>
      ) : (
        <BoundaryNotice tone="cyan">Runtime включён; отображается очищенный журнал</BoundaryNotice>
      )}

      <RunMetrics runs={runs.data.runs} />

      {!filtered.length ? (
        <EmptyState
          title={query || status !== "all" ? "Запуски не найдены" : "Запусков пока нет"}
          action={
            query || status !== "all" ? (
              <button className="secondary-button" type="button" onClick={() => { setQuery(""); setStatus("all"); }}>
                Сбросить фильтры
              </button>
            ) : undefined
          }
        >
          {query || status !== "all"
            ? "Измените запрос или сбросьте фильтры."
            : "Execution run появится только после успешной policy, workspace и lease подготовки."}
        </EmptyState>
      ) : (
        <section className="data-table panel" aria-label="Execution runs">
          <header className="table-row table-head">
            <span>Запуск</span><span>Состояние</span><span>Начат</span><span>Адаптер</span><span>Ресурс</span>
          </header>
          {filtered.map((run) => (
            <button
              className="table-row"
              type="button"
              key={run.run_id}
              onClick={() => onSelect(runSelection(run, runs.data.snapshot_id))}
            >
              <span className="table-primary">
                <i className="object-glyph small"><FileClock size={15} aria-hidden="true" /></i>
                <span><strong>{run.provider}</strong><small>{shortId(run.run_id, 10, 7)} · {shortId(run.task_id)}</small></span>
              </span>
              <span><StatusPill status={run.status} /></span>
              <span><Clock3 size={14} aria-hidden="true" /> {formatDate(run.started_at)}</span>
              <code>{shortId(run.runtime_adapter_id, 14, 5)}</code>
              <span><Gauge size={14} aria-hidden="true" /> {totalTokens(run).toLocaleString("ru-RU")}</span>
            </button>
          ))}
        </section>
      )}
      <p className="route-footnote">
        Команда, рабочая директория, environment и provider session не входят в эту проекцию.
      </p>
    </div>
  );
}
