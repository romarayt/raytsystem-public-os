import { AlertTriangle, CheckCircle2, Code2, Eye, Save, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../api";
import { SafeMarkdown } from "../components/SafeMarkdown";
import { useSkillSave, useSkillSavePreview } from "../skillHooks";
import type { SkillDetailSnapshot, SkillSavePreview, SkillValidationIssue, SkillWriteResult } from "../types";
import { ErrorState, StatusPill } from "../components/StatePanel";
import { Dialog } from "../components/Dialog";

interface SkillEditorProps {
  detail: SkillDetailSnapshot;
  onCancel: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: (result: SkillWriteResult) => void;
}

type EditorMode = "markdown" | "preview";

function issuesFrom(error: unknown): SkillValidationIssue[] {
  if (!(error instanceof ApiError) || !Array.isArray(error.details.errors)) return [];
  return error.details.errors.filter((item): item is SkillValidationIssue => (
    typeof item === "object" && item !== null && "code" in item && "field" in item
  ));
}

function conflictDetails(error: unknown): Record<string, unknown> | null {
  return error instanceof ApiError && error.code === "skill_edit_conflict" ? error.details : null;
}

function conflictText(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

export function SkillEditor({ detail, onCancel, onDirtyChange, onSaved }: SkillEditorProps) {
  const originalContent = detail.content ?? "";
  const [baseContent, setBaseContent] = useState(originalContent);
  const [baseCatalogSha256, setBaseCatalogSha256] = useState(detail.catalog_sha256);
  const [baseSourceSha256, setBaseSourceSha256] = useState(detail.skill.source_sha256);
  const [draft, setDraft] = useState(originalContent);
  const [mode, setMode] = useState<EditorMode>("markdown");
  const [preview, setPreview] = useState<SkillSavePreview | null>(null);
  const [recovery, setRecovery] = useState<Record<string, unknown> | null>(null);
  const [discardConfirm, setDiscardConfirm] = useState(false);
  const previewMutation = useSkillSavePreview();
  const saveMutation = useSkillSave();
  const saveIdempotency = useRef(crypto.randomUUID());
  const dirty = draft !== baseContent;
  const previewCurrent = preview?.proposed_source_sha256 === preview?.validation.source_sha256;
  const validationIssues = useMemo(
    () => issuesFrom(previewMutation.error ?? saveMutation.error),
    [previewMutation.error, saveMutation.error]
  );
  const conflict = recovery ?? conflictDetails(saveMutation.error ?? previewMutation.error);
  const [editorLocation] = useState(() => `${window.location.pathname}${window.location.search}`);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [dirty]);

  const requestCancel = () => {
    if (previewMutation.isPending || saveMutation.isPending) return;
    if (dirty) setDiscardConfirm(true);
    else onCancel();
  };

  const requestPreview = () => {
    previewMutation.reset();
    saveMutation.reset();
    void previewMutation.mutateAsync({
      skillId: detail.skill.skill_id,
      content: draft,
      expectedCatalogSha256: baseCatalogSha256,
      expectedSourceSha256: baseSourceSha256,
      idempotencyKey: crypto.randomUUID()
    }).then((result) => {
      setRecovery(null);
      setPreview(result);
      setMode("preview");
    }).catch((error: unknown) => {
      const details = conflictDetails(error);
      if (details) setRecovery(details);
    });
  };

  const requestSave = () => {
    if (!preview || !previewCurrent) return;
    void saveMutation.mutateAsync({
      skillId: detail.skill.skill_id,
      content: draft,
      expectedCatalogSha256: baseCatalogSha256,
      expectedSourceSha256: baseSourceSha256,
      idempotencyKey: saveIdempotency.current
    }).then(onSaved).catch((error: unknown) => {
      const details = conflictDetails(error);
      if (details) setRecovery(details);
    });
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (preview && previewCurrent) requestSave();
        else requestPreview();
      } else if (event.key === "Escape") {
        requestCancel();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const updateDraft = (value: string) => {
    setDraft(value);
    setPreview(null);
    previewMutation.reset();
    saveMutation.reset();
    saveIdempotency.current = crypto.randomUUID();
  };

  const loadCurrentBase = () => {
    if (!conflict || conflict.content_withheld) return;
    const currentContent = conflict.current_content;
    const currentCatalogSha256 = conflict.current_catalog_sha256;
    const currentSourceSha256 = conflict.current_source_sha256;
    if (
      typeof currentContent !== "string" ||
      typeof currentCatalogSha256 !== "string" ||
      typeof currentSourceSha256 !== "string"
    ) return;
    setBaseContent(currentContent);
    setBaseCatalogSha256(currentCatalogSha256);
    setBaseSourceSha256(currentSourceSha256);
    setDraft(currentContent);
    setPreview(null);
    previewMutation.reset();
    saveMutation.reset();
    saveIdempotency.current = crypto.randomUUID();
    setMode("markdown");
  };

  return (
    <>
    <section
      className="skill-editor panel"
      aria-label={`Редактор ${detail.skill.skill_id}`}
      data-editor-scope="skill"
      data-unsaved-changes={dirty ? "true" : "false"}
      data-editor-location={editorLocation}
    >
      <header className="skill-editor-header">
        <div>
          <span className="eyebrow">Локальный skill · CAS-защита</span>
          <h3>Редактирование {detail.skill.skill_id}</h3>
          <small>{detail.policy.source_path}</small>
        </div>
        <button className="icon-button" type="button" onClick={requestCancel} aria-label="Закрыть редактор"><X size={18} /></button>
      </header>

      <div className="skill-editor-toolbar" role="group" aria-label="Режим редактора">
        <button aria-pressed={mode === "markdown"} className={mode === "markdown" ? "active" : ""} type="button" onClick={() => setMode("markdown")}><Code2 size={15} />Markdown</button>
        <button aria-pressed={mode === "preview"} className={mode === "preview" ? "active" : ""} type="button" onClick={() => setMode("preview")}><Eye size={15} />Предпросмотр</button>
        <span className={dirty ? "editor-dirty" : "editor-clean"}>{dirty ? "Есть несохранённые изменения" : "Изменений нет"}</span>
      </div>

      {mode === "markdown" ? (
        <textarea
          className="skill-markdown-editor"
          aria-label="Исходный Markdown skill"
          value={draft}
          onChange={(event) => updateDraft(event.target.value)}
          spellCheck={false}
          autoFocus
        />
      ) : (
        <div className="skill-editor-preview"><SafeMarkdown content={preview?.normalized_content ?? draft} /></div>
      )}

      {validationIssues.length ? (
        <section className="editor-validation" role="alert">
          <h4><AlertTriangle size={16} />Исправьте ошибки в frontmatter</h4>
          <ul>{validationIssues.map((issue, index) => <li key={`${issue.field}-${issue.code}-${index}`}><code>{issue.field}</code> — {issue.message}</li>)}</ul>
        </section>
      ) : null}

      {preview ? (
        <section className="editor-diff" aria-label="Предпросмотр изменений">
          <header>
            <span><CheckCircle2 size={16} />Проверка пройдена</span>
            <StatusPill status={preview.validation.effective_test_status} />
          </header>
          <p>После записи test status будет <strong>pending</strong>. Skill автоматически не запускается.</p>
          {preview.affected_agents.length ? <p>Изменение затронет агентов: {preview.affected_agents.map((agent) => agent.name).join(", ")}.</p> : null}
          <p><AlertTriangle size={14} /> Typed связь с активными workflows пока не моделируется. Считайте, что изменение может повлиять на использующий этот skill workflow, и проверьте его вручную.</p>
          <pre><code>{preview.diff || "Содержимое не изменилось."}</code></pre>
        </section>
      ) : null}

      {conflict ? (
        <section className="editor-conflict" role="alert">
          <h4><AlertTriangle size={17} />Файл изменился после открытия редактора</h4>
          <p>raytsystem ничего не перезаписал. Сравните версии и примените нужные изменения вручную.</p>
          {conflict.content_withheld ? <p>Текущее содержимое скрыто sensitivity policy.</p> : (
            <div className="conflict-versions">
              <div><strong>Ваша версия</strong><pre>{conflictText(conflict.proposed_content, draft)}</pre></div>
              <div><strong>Актуальная версия</strong><pre>{conflictText(conflict.current_content, "Недоступно")}</pre></div>
            </div>
          )}
          {typeof conflict.diff === "string" ? <pre className="conflict-diff"><code>{conflict.diff}</code></pre> : null}
          {!conflict.content_withheld && typeof conflict.current_catalog_sha256 === "string" && typeof conflict.current_source_sha256 === "string" ? (
            <button className="secondary-button" type="button" onClick={loadCurrentBase}>
              Загрузить актуальную основу
            </button>
          ) : null}
          <p>Автоматического merge нет. После загрузки основы вручную перенесите нужные фрагменты из вашей предыдущей версии.</p>
        </section>
      ) : null}

      {previewMutation.isError && !validationIssues.length && !conflict ? <ErrorState error={previewMutation.error} /> : null}
      {saveMutation.isError && !validationIssues.length && !conflict ? <ErrorState error={saveMutation.error} /> : null}

      <footer className="skill-editor-actions">
        <span><kbd>⌘/Ctrl S</kbd> проверить или сохранить · <kbd>Esc</kbd> отменить</span>
        <button className="secondary-button" type="button" onClick={requestCancel}>Отмена</button>
        <button className="secondary-button" type="button" onClick={requestPreview} disabled={!dirty || previewMutation.isPending}>{previewMutation.isPending ? "Проверяем…" : "Проверить и показать diff"}</button>
        <button className="primary-button" type="button" onClick={requestSave} disabled={!preview || !previewCurrent || saveMutation.isPending}><Save size={15} />{saveMutation.isPending ? "Сохраняем…" : "Сохранить"}</button>
      </footer>
    </section>
    {discardConfirm ? (
      <Dialog className="small-modal panel" role="alertdialog" labelledBy="discard-skill-title" describedBy="discard-skill-description" closeOnBackdrop={false} initialFocus="cancel" onClose={() => setDiscardConfirm(false)}>
        <header><div><span className="eyebrow">Несохранённые изменения</span><h2 id="discard-skill-title">Закрыть редактор без сохранения?</h2></div></header>
        <p id="discard-skill-description">Изменённый Markdown не был записан. Исходный skill и его история остались без изменений.</p>
        <footer><button type="button" data-dialog-cancel onClick={() => setDiscardConfirm(false)}>Продолжить редактирование</button><button className="danger-button" type="button" onClick={onCancel}>Закрыть без сохранения</button></footer>
      </Dialog>
    ) : null}
    </>
  );
}
