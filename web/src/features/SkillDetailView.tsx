import {
  ArrowLeft,
  CheckCircle2,
  ClipboardCheck,
  Code2,
  Copy,
  Eye,
  FileText,
  History,
  KeyRound,
  Pencil,
  TestTube2,
  Wrench
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { shortId } from "../api";
import { SafeMarkdown } from "../components/SafeMarkdown";
import { Dialog } from "../components/Dialog";
import { Surface, SurfaceContent, SurfaceTabs, type SurfaceTab } from "../components/SurfaceTabs";
import { EmptyState, ErrorState, LoadingState, StatusPill } from "../components/StatePanel";
import { catalogDescription, localizedCatalogLabel, roleLabel, statusLabel } from "../presentation";
import { useSkillDetail } from "../skillHooks";
import type { SkillWriteResult } from "../types";
import { SkillEditor } from "./SkillEditor";
import { SkillForkPanel } from "./SkillForkPanel";

type SkillDetailTab = "overview" | "instruction" | "permissions" | "tools" | "tests" | "history";
type InstructionMode = "preview" | "raw";

const skillTabs: readonly SurfaceTab<SkillDetailTab>[] = [
  { id: "overview", label: "Обзор", icon: <ClipboardCheck size={15} />, panelId: "skill-detail-panel", tabId: "skill-tab-overview" },
  { id: "instruction", label: "Инструкция", icon: <FileText size={15} />, panelId: "skill-detail-panel", tabId: "skill-tab-instruction" },
  { id: "permissions", label: "Permissions", icon: <KeyRound size={15} />, panelId: "skill-detail-panel", tabId: "skill-tab-permissions" },
  { id: "tools", label: "Tools", icon: <Wrench size={15} />, panelId: "skill-detail-panel", tabId: "skill-tab-tools" },
  { id: "tests", label: "Tests", icon: <TestTube2 size={15} />, panelId: "skill-detail-panel", tabId: "skill-tab-tests" },
  { id: "history", label: "История", icon: <History size={15} />, panelId: "skill-detail-panel", tabId: "skill-tab-history" }
];

const readOnlyReasonCopy: Record<string, string> = {
  official_skill: "Официальный встроенный skill доступен только для чтения.",
  installed_pinned_pack: "Установленный закреплённый pack доступен только для чтения.",
  generated_skill: "Сгенерированный skill нельзя менять напрямую.",
  sensitivity_restricted: "Запись запрещена sensitivity policy.",
  skill_disabled: "Отключённый skill нельзя редактировать.",
  source_path_not_allowlisted: "Источник находится вне разрешённого skills/<skill_id>/SKILL.md.",
  unverified_provenance: "Происхождение skill не подтверждено.",
  non_local_pack: "Skill принадлежит внешнему или нередактируемому pack."
};

function availabilityLabel(value: string): string {
  if (value === "not_modeled") return "Не моделируется";
  if (value === "declared_ids_only") return "Только заявленные ID";
  if (value === "available") return "Доступно";
  return statusLabel(value);
}

function boundaryValue(section: { availability: string; items: string[] }): React.ReactNode {
  return section.items.length
    ? <span>{section.items.map((item) => <code key={item}>{item} </code>)}</span>
    : availabilityLabel(section.availability);
}

interface SkillDetailViewProps {
  skillId: string;
  expectedCatalogSha256: string;
  onBack: () => void;
  onRevisionChanged: (result: SkillWriteResult) => void;
  onForkCreated: (result: SkillWriteResult) => void;
}

function DetailList({ rows }: { rows: Array<[string, React.ReactNode]> }) {
  return <dl className="surface-detail-list">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

export function SkillDetailView({
  skillId,
  expectedCatalogSha256,
  onBack,
  onRevisionChanged,
  onForkCreated
}: SkillDetailViewProps) {
  const detailQuery = useSkillDetail(skillId, expectedCatalogSha256);
  const [activeTab, setActiveTab] = useState<SkillDetailTab>("overview");
  const [instructionMode, setInstructionMode] = useState<InstructionMode>("preview");
  const [editing, setEditing] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [forking, setForking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [backConfirmOpen, setBackConfirmOpen] = useState(false);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const editButtonRef = useRef<HTMLButtonElement | null>(null);
  const forkButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!detailQuery.data) return;
    const frame = window.requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
    return () => window.cancelAnimationFrame(frame);
  }, [detailQuery.data]);

  const closeEditor = useCallback(() => {
    setEditing(false);
    setEditorDirty(false);
    window.requestAnimationFrame(() => editButtonRef.current?.focus());
  }, []);

  const closeFork = useCallback(() => {
    setForking(false);
    window.requestAnimationFrame(() => forkButtonRef.current?.focus());
  }, []);

  if (detailQuery.isLoading) return <LoadingState label={`Открываем ${skillId}…`} />;
  if (detailQuery.isError || !detailQuery.data) {
    return (
      <section className="detail-error-stack">
        <button className="secondary-button" type="button" onClick={onBack}><ArrowLeft size={15} />К списку skills</button>
        <ErrorState error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />
      </section>
    );
  }

  const detail = detailQuery.data;
  const { skill, policy } = detail;
  const panelLabelledBy = `skill-tab-${activeTab}`;
  const saved = (result: SkillWriteResult) => {
    setEditing(false);
    setEditorDirty(false);
    setNotice(`Revision ${result.record_revision} сохранена. Test status: pending.`);
    onRevisionChanged(result);
  };
  const forked = (result: SkillWriteResult) => {
    setForking(false);
    onForkCreated(result);
  };
  const requestBack = () => {
    if (editorDirty) setBackConfirmOpen(true);
    else onBack();
  };

  return (
    <div className="skill-detail-stack">
      <section className="detail-hero skill-detail-hero panel">
        <span className="agent-aura skill-detail-aura"><Wrench size={27} /></span>
        <div className="detail-hero-copy">
          <span className="eyebrow">{localizedCatalogLabel(skill.pack_id, skill.pack_id)} · {skill.version}</span>
          <h2 ref={headingRef} tabIndex={-1}>{skill.skill_id}</h2>
          <p>{catalogDescription(skill.skill_id, skill.description)}</p>
        </div>
        <div className="detail-hero-state">
          <div><StatusPill status={skill.enabled ? "enabled" : "restricted"} /><StatusPill status={skill.test_status} /></div>
          <span>{policy.editable ? "Можно редактировать локально" : (readOnlyReasonCopy[policy.read_only_reason ?? ""] ?? "Только чтение")}</span>
        </div>
        <div className="detail-hero-actions">
          <button className="secondary-button" type="button" onClick={requestBack}><ArrowLeft size={15} />К списку</button>
          {policy.editable && detail.content !== null ? <button ref={editButtonRef} className="primary-button" type="button" onClick={() => { setActiveTab("instruction"); setEditing(true); setForking(false); }}><Pencil size={15} />Редактировать</button> : null}
          {!policy.editable && policy.forkable ? <button ref={forkButtonRef} className="primary-button" type="button" onClick={() => { setForking(true); setEditing(false); }}><Copy size={15} />Создать локальную копию</button> : null}
        </div>
      </section>

      {notice ? <div className="skill-save-notice" role="status"><CheckCircle2 size={16} />{notice}</div> : null}
      {editing ? <SkillEditor detail={detail} onCancel={closeEditor} onDirtyChange={setEditorDirty} onSaved={saved} /> : null}
      {forking ? <SkillForkPanel detail={detail} onCancel={closeFork} onCreated={forked} /> : null}

      {!editing && !forking ? (
        <Surface className="detail-tab-surface">
          <SurfaceTabs tabs={skillTabs} activeTab={activeTab} onTabChange={setActiveTab} ariaLabel="Разделы skill" id="skill-detail-tabs" />
          <SurfaceContent id="skill-detail-panel" labelledBy={panelLabelledBy}>
            {activeTab === "overview" ? (
              <div className="detail-section-grid">
                <section className="detail-section panel">
                  <h3>Определение</h3>
                  <DetailList rows={[
                    ["skill_id", <code>{skill.skill_id}</code>],
                    ["Имя в frontmatter", skill.name],
                    ["Описание", catalogDescription(skill.skill_id, skill.description)],
                    ["Версия", skill.version],
                    ["Пакет", localizedCatalogLabel(skill.pack_id, skill.pack_id)],
                    ["Доверие", statusLabel(skill.trust_class)],
                    ["Чувствительность", statusLabel(skill.sensitivity)],
                    ["Статус проверки", <StatusPill status={skill.test_status} />],
                    ["SHA-256 источника", <code title={skill.source_sha256}>{shortId(skill.source_sha256, 14, 10)}</code>],
                    ["Путь источника", <code>{policy.source_path}</code>]
                  ]} />
                </section>
                <section className="detail-section panel">
                  <h3>Связи</h3>
                  {detail.related_agents.length ? <div className="related-object-list static">{detail.related_agents.map((agent) => <div key={agent.agent_id}><span><strong>{agent.name}</strong><small>{roleLabel(agent.role)} · {agent.agent_id}</small></span></div>)}</div> : <EmptyState title="Нет назначенных агентов">Catalog не связывает этот skill ни с одним AgentDefinition.</EmptyState>}
                  <h3 className="section-subheading">Workflows</h3>
                  {detail.workflows.items.length ? <div className="related-object-list static">{detail.workflows.items.map((workflow) => <div key={workflow.workflow_id}><span><strong>{workflow.name}</strong><small>{workflow.workflow_id}</small></span><StatusPill status={workflow.active ? "running" : "disabled"} /></div>)}</div> : <p className="muted-copy">Typed workflow relationships пока не объявлены; UI не извлекает их из текста инструкции.</p>}
                </section>
              </div>
            ) : null}

            {activeTab === "instruction" ? (
              <section className="detail-section panel skill-instruction-section">
                <header className="instruction-toolbar">
                  <div><h3>SKILL.md</h3><p>Документ отображается как inert data: HTML и embeds не исполняются.</p></div>
                  <div className="document-mode-switch" role="group" aria-label="Режим инструкции">
                    <button type="button" aria-pressed={instructionMode === "preview"} className={instructionMode === "preview" ? "active" : ""} onClick={() => setInstructionMode("preview")}><Eye size={14} />Предпросмотр</button>
                    <button type="button" aria-pressed={instructionMode === "raw"} className={instructionMode === "raw" ? "active" : ""} onClick={() => setInstructionMode("raw")}><Code2 size={14} />Исходный Markdown</button>
                  </div>
                </header>
                {detail.content === null ? (
                  <EmptyState title="Содержимое ограничено">Sensitivity policy разрешает показать только безопасные метаданные этого skill.</EmptyState>
                ) : instructionMode === "preview" ? <SafeMarkdown content={detail.content} /> : <pre className="skill-raw-markdown"><code>{detail.content}</code></pre>}
              </section>
            ) : null}

            {activeTab === "permissions" ? (
              <div className="detail-section-grid">
                <section className="detail-section panel">
                  <h3>Объявленные permissions</h3>
                  {skill.permissions.length ? <ul className="permission-id-list">{skill.permissions.map((permission) => <li key={permission}><code>{permission}</code></li>)}</ul> : <EmptyState title="Permissions не объявлены">Пустой список не означает неограниченный доступ: действуют системные boundaries и approvals.</EmptyState>}
                </section>
                <section className="detail-section panel">
                  <h3>Фактические границы</h3>
                  <DetailList rows={[
                    ["Файловая система", boundaryValue(detail.permission_boundary.filesystem)],
                    ["Сеть и egress", boundaryValue(detail.permission_boundary.network)],
                    ["Инструменты", boundaryValue(detail.permission_boundary.tools)],
                    ["Требования к секретам", boundaryValue(detail.permission_boundary.secrets)],
                    ["Подтверждения", boundaryValue(detail.permission_boundary.approvals)],
                    ["Побочные эффекты", boundaryValue(detail.permission_boundary.side_effects)],
                    ["Чувствительность", statusLabel(detail.permission_boundary.sensitivity)]
                  ]} />
                </section>
              </div>
            ) : null}

            {activeTab === "tools" ? (
              <section className="detail-section panel">
                <h3>Tool Hub</h3>
                {detail.tools.items.length ? <div className="tool-detail-table">{detail.tools.items.map((tool) => <div key={tool.tool_id}><code>{tool.tool_id}</code><span>{tool.provider}</span><span>{tool.access}</span><span>{tool.approval_policy}</span><StatusPill status={tool.health} /></div>)}</div> : <EmptyState title="Связанные tools не объявлены">Typed Tool Hub relationships отсутствуют. raytsystem не угадывает инструменты из команд или prose в SKILL.md.</EmptyState>}
              </section>
            ) : null}

            {activeTab === "tests" ? (
              <div className="detail-section-grid">
                <section className="detail-section panel"><h3>Проверка</h3><DetailList rows={[["Test status", <StatusPill status={detail.tests.test_status} />], ["Последняя проверка", detail.tests.last_checked_at ?? "Нет подтверждённой проверки"], ["Evals", detail.tests.evals.length ? detail.tests.evals.map((item) => item.eval_id).join(", ") : "Не объявлены"]]} /></section>
                <section className="detail-section panel"><h3>Команды и ограничения</h3>{detail.tests.commands.length ? <pre className="safe-source-block">{detail.tests.commands.join("\n")}</pre> : <p className="muted-copy">Проверочная команда не объявлена typed metadata.</p>}{detail.tests.known_limitations.length ? <ul>{detail.tests.known_limitations.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted-copy">Known limitations не зарегистрированы.</p>}</section>
              </div>
            ) : null}

            {activeTab === "history" ? (
              <section className="detail-section panel">
                <h3>Revisions и hashes</h3>
                {detail.history.revisions.length ? <div className="skill-history-list">{detail.history.revisions.map((revision) => <div className="history-row" key={revision.skill_revision_id ?? revision.record_revision}><code>{revision.skill_revision_id ?? `revision-${revision.record_revision}`}</code><StatusPill status={revision.test_status ?? revision.record_state} /><span>{revision.operation ?? "revision"} · {shortId(revision.source_sha256, 10, 7)} · {revision.changed_at ?? "время не записано"}</span></div>)}</div> : <EmptyState title="История пока недоступна">Для исходного определения нет безопасных revision records.</EmptyState>}
              </section>
            ) : null}
          </SurfaceContent>
        </Surface>
      ) : null}
      {backConfirmOpen ? (
        <Dialog className="small-modal panel" role="alertdialog" labelledBy="leave-skill-title" describedBy="leave-skill-description" closeOnBackdrop={false} initialFocus="cancel" onClose={() => setBackConfirmOpen(false)}>
          <header><div><span className="eyebrow">Несохранённые изменения</span><h2 id="leave-skill-title">Вернуться к списку без сохранения?</h2></div></header>
          <p id="leave-skill-description">Изменённый Markdown не был записан. Skill и его история остались без изменений.</p>
          <footer><button type="button" data-dialog-cancel onClick={() => setBackConfirmOpen(false)}>Продолжить редактирование</button><button className="danger-button" type="button" onClick={onBack}>Вернуться без сохранения</button></footer>
        </Dialog>
      ) : null}
    </div>
  );
}
