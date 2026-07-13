import { AlertTriangle, Copy, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ErrorState, StatusPill } from "../components/StatePanel";
import { localizedCatalogLabel, statusLabel } from "../presentation";
import { useSkillFork, useSkillForkPreview } from "../skillHooks";
import type { SkillDetailSnapshot, SkillWriteResult } from "../types";

interface SkillForkPanelProps {
  detail: SkillDetailSnapshot;
  onCancel: () => void;
  onCreated: (result: SkillWriteResult) => void;
}

export function SkillForkPanel({ detail, onCancel, onCreated }: SkillForkPanelProps) {
  const previewMutation = useSkillForkPreview();
  const forkMutation = useSkillFork();
  const [newSkillId, setNewSkillId] = useState("");
  const commitKey = useRef(crypto.randomUUID());

  const requestPreview = (candidate?: string) => {
    previewMutation.reset();
    forkMutation.reset();
    void previewMutation.mutateAsync({
      skillId: detail.skill.skill_id,
      newSkillId: candidate?.trim() || undefined,
      expectedCatalogSha256: detail.catalog_sha256,
      expectedSourceSha256: detail.skill.source_sha256,
      idempotencyKey: crypto.randomUUID()
    }).then((preview) => setNewSkillId(preview.new_skill_id)).catch(() => undefined);
  };

  useEffect(() => {
    requestPreview();
    // Preview once for the immutable source revision used to open this panel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.catalog_sha256, detail.skill.skill_id, detail.skill.source_sha256]);

  const preview = previewMutation.data;
  const previewMatches = preview?.new_skill_id === newSkillId.trim();
  const create = () => {
    if (!preview || !previewMatches) return;
    void forkMutation.mutateAsync({
      skillId: detail.skill.skill_id,
      newSkillId: preview.new_skill_id,
      expectedCatalogSha256: detail.catalog_sha256,
      expectedSourceSha256: detail.skill.source_sha256,
      idempotencyKey: commitKey.current
    }).then(onCreated).catch(() => undefined);
  };

  return (
    <section className="skill-fork-panel panel" aria-label={`Локальная копия ${detail.skill.skill_id}`}>
      <header>
        <div><span className="eyebrow">Исходный skill доступен только для чтения и останется неизменным</span><h3>Создать локальную копию</h3></div>
        <button className="icon-button" type="button" onClick={onCancel} aria-label="Закрыть создание копии"><X size={18} /></button>
      </header>
      <div className="fork-destination">
        <label>Новый уникальный skill_id<input name="skill_id" autoFocus value={newSkillId} onChange={(event) => setNewSkillId(event.target.value)} pattern="[a-z][a-z0-9_-]{1,63}" /></label>
        <button className="secondary-button" type="button" onClick={() => requestPreview(newSkillId)} disabled={!newSkillId.trim() || previewMutation.isPending}>Обновить предпросмотр</button>
      </div>
      {previewMutation.isPending ? <p className="muted-copy">Проверяем место назначения и строим diff…</p> : null}
      {previewMutation.isError ? <ErrorState error={previewMutation.error} /> : null}
      {preview ? (
        <div className="fork-preview">
          <dl className="surface-detail-list">
            <div><dt>Источник</dt><dd>{detail.policy.source_path}</dd></div>
            <div><dt>Место копии</dt><dd><code>{preview.destination}</code></dd></div>
            <div><dt>Владение</dt><dd>{statusLabel(preview.ownership_after_create.trust_class)} · {localizedCatalogLabel(preview.ownership_after_create.pack_id, preview.ownership_after_create.pack_id)}</dd></div>
            <div><dt>Статус проверки</dt><dd><StatusPill status={preview.validation.effective_test_status} /></dd></div>
          </dl>
          <p><AlertTriangle size={15} /> Исходный skill не изменится. Новая копия появится только после подтверждения.</p>
          <pre><code>{preview.diff}</code></pre>
        </div>
      ) : null}
      {forkMutation.isError ? <ErrorState error={forkMutation.error} /> : null}
      <footer>
        <button className="secondary-button" type="button" onClick={onCancel}>Отмена</button>
        <button className="primary-button" type="button" onClick={create} disabled={!previewMatches || forkMutation.isPending}><Copy size={15} />{forkMutation.isPending ? "Создаём…" : "Подтвердить и создать"}</button>
      </footer>
    </section>
  );
}
