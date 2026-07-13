import {
  ArrowUpRight,
  Bot,
  CheckCircle2,
  CircleAlert,
  Database,
  FileCheck2,
  ListTodo,
  LockKeyhole,
  Orbit,
  Plus,
  ShieldCheck
} from "lucide-react";
import { formatDate, shortId } from "../api";
import { useRuns, useSystem, useTasks } from "../hooks";
import type { Selection, TaskStatus } from "../types";
import { operationLabel, pluralRu, taskStatusLabel } from "../presentation";
import { ErrorState, LoadingState, StatusPill } from "../components/StatePanel";

const workStates: TaskStatus[] = ["inbox", "planned", "ready", "running", "review", "blocked", "done"];

interface CommandCenterProps {
  onCreateTask: () => void;
  onNavigate: (route: string) => void;
  onSelect: (selection: Selection) => void;
}

export function CommandCenter({ onCreateTask, onNavigate, onSelect }: CommandCenterProps) {
  const system = useSystem();
  const tasks = useTasks();
  const runs = useRuns();

  if (system.isLoading) return <LoadingState label="Читаем проверенную панель управления…" />;
  if (system.isError || !system.data) return <ErrorState error={system.error} onRetry={() => void system.refetch()} />;
  const data = system.data;
  const attentionTotal =
    data.attention.blocked_tasks + data.attention.failed_runs + data.attention.restricted_skills;
  const totalKnowledge = data.counts.claims + data.counts.entities + data.counts.sources + data.counts.evidence;

  return (
    <div className="route route-command-center">
      <section className="trust-strip" aria-label="Граница текущего среза">
        <span className="local-indicator"><i /> Только локально</span>
        <span><Database size={14} /> знания <code>{shortId(data.fingerprint.knowledge_generation_id)}</code></span>
        <span><ListTodo size={14} /> задачи <code>{shortId(data.fingerprint.task_generation_id)}</code></span>
        <span><ShieldCheck size={14} /> каталог <code>{shortId(data.fingerprint.catalog_sha256)}</code></span>
      </section>

      <div className="command-grid">
        <section className="mission-hero panel panel-glow">
          <div className="hero-copy">
            <span className="eyebrow">Центр управления · проверенный срез</span>
            <h2>Все ваши системы —<br /><em>в одном поле зрения.</em></h2>
            <p>
              Знания, работа, агенты и точные доказательства остаются связаны, а браузер не получает
              права выполнять команды.
            </p>
            <div className="hero-actions">
              <button className="primary-button" type="button" onClick={() => onNavigate("universe")}>
                <Orbit size={17} /> Открыть вселенную
              </button>
              <button className="secondary-button" type="button" onClick={onCreateTask}>
                <Plus size={17} /> Создать задачу
              </button>
            </div>
          </div>
          <div className="mini-universe" aria-hidden="true">
            <span className="mini-ring ring-a" />
            <span className="mini-ring ring-b" />
            <span className="mini-ring ring-c" />
            <i className="mini-core"><span>OS</span></i>
            <i className="mini-node n1" /><i className="mini-node n2" /><i className="mini-node n3" />
            <i className="mini-node n4" /><i className="mini-node n5" /><i className="mini-node n6" />
            <div className="orbit-caption"><strong>{totalKnowledge}</strong><span>{pluralRu(totalKnowledge, "проверенный объект", "проверенных объекта", "проверенных объектов")}</span></div>
          </div>
        </section>

        <section className={`attention-panel panel ${attentionTotal ? "has-attention" : "is-clear"}`}>
          <header className="panel-header">
            <div>
              <span className="eyebrow">Требует внимания</span>
              <h3>{attentionTotal ? `${attentionTotal} ${pluralRu(attentionTotal, "сигнал", "сигнала", "сигналов")}` : "Всё в порядке"}</h3>
            </div>
            {attentionTotal ? <CircleAlert size={22} /> : <CheckCircle2 size={22} />}
          </header>
          <div className="attention-list">
            <button type="button" onClick={() => onNavigate("tasks")}>
              <span className="attention-icon rose"><ListTodo size={17} /></span>
              <span><strong>Заблокированные задачи</strong><small>Только операционное состояние</small></span>
              <b>{data.attention.blocked_tasks}</b>
            </button>
            <button type="button" onClick={() => onNavigate("runs")}>
              <span className="attention-icon gold"><FileCheck2 size={17} /></span>
              <span><strong>Неудачные запуски</strong><small>Зафиксированные записи</small></span>
              <b>{data.attention.failed_runs}</b>
            </button>
            <button type="button" onClick={() => onNavigate("skills")}>
              <span className="attention-icon violet"><LockKeyhole size={17} /></span>
              <span><strong>Ограниченные навыки</strong><small>Контроль чувствительности</small></span>
              <b>{data.attention.restricted_skills}</b>
            </button>
          </div>
        </section>

        <section className="work-panel panel">
          <header className="panel-header">
            <div><span className="eyebrow">Состояние работы</span><h3>Операционный журнал</h3></div>
            <button className="text-button" type="button" onClick={() => onNavigate("tasks")}>Открыть доску <ArrowUpRight size={14} /></button>
          </header>
          <div className="work-bars">
            {workStates.map((status) => {
              const count = data.counts.tasks[status] ?? 0;
              const max = Math.max(1, ...Object.values(data.counts.tasks));
              return (
                <div className="work-bar" key={status}>
                  <span>{taskStatusLabel(status)}</span>
                  <i><b style={{ width: `${Math.max(count ? 8 : 0, (count / max) * 100)}%` }} /></i>
                  <strong>{count}</strong>
                </div>
              );
            })}
          </div>
          {tasks.data?.tasks.slice(0, 3).map((task) => (
            <button
              className="compact-object"
              type="button"
              key={task.task_id}
              onClick={() =>
                onSelect({
                  id: task.task_id,
                  kind: "task",
                  label: task.title,
                  status: task.status,
                  subtitle: task.priority,
                  snapshotId: tasks.data?.generation_id ?? undefined
                })
              }
            >
              <span className={`priority-mark priority-${task.priority}`} />
              <span><strong>{task.title}</strong><small>{shortId(task.task_id)}</small></span>
              <StatusPill status={task.status} />
            </button>
          ))}
        </section>

        <section className="runs-panel panel">
          <header className="panel-header">
            <div><span className="eyebrow">Последние запуски</span><h3>Зафиксированная история</h3></div>
            <button className="text-button" type="button" onClick={() => onNavigate("runs")}>Открыть <ArrowUpRight size={14} /></button>
          </header>
          <div className="timeline-list">
            {runs.data?.runs.slice(0, 5).map((run) => (
              <button
                type="button"
                key={run.run_id}
                onClick={() => onSelect({
                  id: run.run_id,
                  kind: "run",
                  label: operationLabel(run.operation_type),
                  status: run.state,
                  subtitle: formatDate(run.updated_at),
                  metadata: { manifest_sha256: run.manifest_sha256, semantic_noop: String(run.semantic_noop) }
                })}
              >
                <i className={`timeline-dot run-${run.state}`} />
                <span><strong>{operationLabel(run.operation_type)}</strong><small>{formatDate(run.updated_at)}</small></span>
                <StatusPill status={run.state} />
              </button>
            ))}
            {!runs.data?.runs.length ? <p className="muted-copy">Зафиксированных запусков пока нет.</p> : null}
          </div>
        </section>

        <section className="knowledge-panel panel">
          <header className="panel-header">
            <div><span className="eyebrow">Слой знаний</span><h3>{data.counts.claims} {pluralRu(data.counts.claims, "каноническое утверждение", "канонических утверждения", "канонических утверждений")}</h3></div>
            <Database size={21} />
          </header>
          <div className="metric-quartet">
            <div><strong>{data.counts.claims}</strong><span>утверждения</span></div>
            <div><strong>{data.counts.entities}</strong><span>сущности</span></div>
            <div><strong>{data.counts.sources}</strong><span>источники</span></div>
            <div><strong>{data.counts.evidence}</strong><span>фрагменты</span></div>
          </div>
          <div className="generation-line"><span>Активное поколение</span><code>{shortId(data.fingerprint.knowledge_generation_id, 12, 8)}</code></div>
        </section>

        <section className="agents-panel panel">
          <header className="panel-header">
            <div><span className="eyebrow">Реестр агентов</span><h3>{data.counts.agents} {pluralRu(data.counts.agents, "объявленный профиль", "объявленных профиля", "объявленных профилей")}</h3></div>
            <Bot size={21} />
          </header>
          <p>Наличие профиля или назначенной задачи не означает, что агент выполняется.</p>
          <div className="boundary-chip"><LockKeyhole size={14} /> выполнение отключено</div>
        </section>
      </div>
    </div>
  );
}
