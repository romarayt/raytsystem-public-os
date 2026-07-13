import { AlertTriangle, X } from "lucide-react";
import { useState } from "react";
import { Dialog } from "../../components/Dialog";
import type { DocumentConflictDetails } from "./documentTypes";
import { DocumentDiff } from "./DocumentDiff";

interface DocumentConflictDialogProps {
  conflict: DocumentConflictDetails;
  baseContent: string;
  draftContent: string;
  onCancel: () => void;
  onResolve: (content: string, currentSha256: string, snapshotId: string) => void;
}

export function DocumentConflictDialog({ conflict, baseContent, draftContent, onCancel, onResolve }: DocumentConflictDialogProps) {
  const [merged, setMerged] = useState(draftContent);
  const [manual, setManual] = useState(false);
  const diskContentAvailable = typeof conflict.current_content === "string";
  const proposedHash = conflict.proposed_sha256?.slice(0, 12) ?? "не предоставлен";

  return (
      <Dialog className="doc-conflict-dialog" backdropClassName="doc-modal-backdrop" labelledBy="doc-conflict-title" describedBy="doc-conflict-description" closeOnBackdrop={false} initialFocus="cancel" onClose={onCancel}>
        <header>
          <span className="doc-conflict-icon"><AlertTriangle size={20} aria-hidden="true" /></span>
          <div><span>Безопасная запись остановлена</span><h2 id="doc-conflict-title">Документ изменился на диске</h2></div>
          <button type="button" aria-label="Закрыть конфликт" onClick={onCancel}><X size={18} /></button>
        </header>
        <p id="doc-conflict-description">raytsystem не перезаписал новую версию. Сравните исходник при открытии, текущее состояние диска и свой черновик.</p>
        <div className="doc-conflict-hashes"><code>открыт {conflict.expected_sha256.slice(0, 12)}</code><code>диск {conflict.current_sha256.slice(0, 12)}</code><code>черновик {proposedHash}</code></div>
        <DocumentDiff original={baseContent} disk={conflict.current_content ?? null} current={draftContent} />
        {manual ? (
          <label className="doc-manual-merge"><span>Итоговый Markdown после ручного merge</span><textarea autoFocus value={merged} onChange={(event) => setMerged(event.target.value)} spellCheck={false} /></label>
        ) : null}
        {!diskContentAvailable ? <p className="doc-conflict-note" role="status">Текущий текст скрыт disclosure policy. Обновите документ и перенесите изменения вручную в Source mode.</p> : null}
        <footer>
          <button type="button" data-dialog-cancel onClick={onCancel}>Оставить черновик</button>
          <button type="button" onClick={() => setManual(true)} disabled={!diskContentAvailable}>Ручной merge</button>
          <button type="button" className="primary" disabled={!diskContentAvailable || !manual || merged === conflict.current_content} onClick={() => onResolve(merged, conflict.current_sha256, conflict.snapshot_id)}>Сохранить итог</button>
        </footer>
      </Dialog>
  );
}
