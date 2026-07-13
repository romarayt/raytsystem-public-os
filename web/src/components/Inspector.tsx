import { Check, Copy, Database, ExternalLink, FileCode2, FileText, GitBranch, GitCompareArrows, Network, RefreshCw, SearchCheck, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { formatDate, getJson, shortId } from "../api";
import { displayValue, fieldLabel, kindLabel } from "../presentation";
import type { Selection } from "../types";
import { ErrorState, LoadingState, StatusPill } from "./StatePanel";

function endpointFor(selection: Selection): string | null {
  if (!selection.snapshotId) return null;
  const expected = `?expected=${encodeURIComponent(selection.snapshotId)}`;
  if (selection.kind === "task") return `/api/v1/tasks/${selection.id}${expected}`;
  if (selection.kind === "skill") return `/api/v1/skills/${selection.id}${expected}`;
  if (selection.kind === "instruction") return `/api/v1/instructions/${selection.id}${expected}`;
  if (["claim", "entity", "source", "evidence"].includes(selection.kind)) {
    return `/api/v1/knowledge/${selection.kind}/${selection.id}${expected}`;
  }
  if (selection.plane === "code") return `/api/v1/code-graph/nodes/${selection.id}${expected}`;
  return null;
}

function detailRows(payload: unknown): Array<[string, string]> {
  if (!payload || typeof payload !== "object") return [];
  const root = payload as Record<string, unknown>;
  const nested = Object.values(root).find(
    (value) => value && typeof value === "object" && !Array.isArray(value)
  );
  const record = (nested ?? root) as Record<string, unknown>;
  return Object.entries(record)
    .filter(
      ([key, value]) =>
        !["content", "excerpt"].includes(key) &&
        (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
    )
    .slice(0, 12)
    .map(([key, value]) => [key, String(value)]);
}

function dispatchCodeAction(operation: string, nodeId: string, direction?: string) {
  window.dispatchEvent(new CustomEvent("raytsystem:code-action", { detail: { operation, nodeId, direction } }));
}

export function Inspector({ selection, onClose, onSelect, onCreateTask }: { selection: Selection | null; onClose: () => void; onSelect: (selection: Selection) => void; onCreateTask: () => void }) {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const endpoint = useMemo(() => (selection ? endpointFor(selection) : null), [selection]);
  const detail = useQuery({
    queryKey: ["detail", endpoint],
    queryFn: () => getJson<Record<string, unknown>>(endpoint!),
    enabled: endpoint !== null,
    staleTime: 2_000
  });

  if (!selection) return null;
  const claim = detail.data?.claim && typeof detail.data.claim === "object" ? detail.data.claim as Record<string, unknown> : null;
  const evidence = detail.data?.evidence && typeof detail.data.evidence === "object" ? detail.data.evidence as Record<string, unknown> : null;
  const evidenceIds = Array.isArray(claim?.evidence_ids) ? claim.evidence_ids.filter((item): item is string => typeof item === "string") : [];
  const content = typeof detail.data?.content === "string"
    ? detail.data.content
    : typeof evidence?.excerpt === "string"
      ? evidence.excerpt
      : null;
  const rows = detail.data ? detailRows(detail.data) : Object.entries(selection.metadata ?? {});
  const isCode = selection.plane === "code";
  const codeNode = isCode && detail.data?.node && typeof detail.data.node === "object" ? detail.data.node as Record<string, unknown> : null;
  const codePath = typeof codeNode?.path === "string" ? codeNode.path : selection.metadata?.path;

  return (
    <aside className="inspector" aria-label={`Инспектор: ${kindLabel(selection.kind)}`}>
      <header className="inspector-header">
        <div className="object-glyph" data-kind={selection.kind}><Database size={18} /></div>
        <div>
          <span className="eyebrow">{kindLabel(selection.kind)}</span>
          <h2>{selection.label}</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть инспектор">
          <X size={19} />
        </button>
      </header>
      <div className="inspector-identity">
        <StatusPill status={selection.status} />
        <button
          className="copy-id"
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(selection.id).then(() => setCopiedId(selection.id));
          }}
          aria-label={copiedId === selection.id ? "ID скопирован" : "Скопировать ID объекта"}
        >
          <code>{shortId(selection.id, 10, 7)}</code>
          {copiedId === selection.id ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
      <div className="inspector-section-label"><span>Обзор объекта</span><GitBranch size={14} aria-hidden="true" /></div>
      <div className="inspector-scroll">
        {selection.subtitle ? <p className="inspector-summary">{selection.subtitle}</p> : null}
        {detail.isLoading ? <LoadingState label="Сверяем объект с выбранным срезом…" /> : null}
        {detail.isError ? <ErrorState error={detail.error} /> : null}
        {rows.length ? (
          <dl className="detail-list">
            {rows.map(([field, value]) => (
              <div key={field}>
                <dt>{fieldLabel(field)}</dt>
                <dd title={value}>{field.endsWith("_at") ? formatDate(value) : displayValue(field, value)}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {content ? (
          <section className="inert-content">
            <div><FileText size={15} /> {selection.kind === "evidence" ? "Проверенный точный фрагмент" : "Безопасный просмотр источника"}</div>
            <pre>{content}</pre>
          </section>
        ) : null}
        {evidenceIds.length ? (
          <section className="related-evidence" aria-label="Путь к проверенному доказательству">
            <div><FileText size={15} /><strong>Проверенное доказательство</strong></div>
            <p>Откройте неизменяемый фрагмент источника, подтверждающий это утверждение.</p>
            {evidenceIds.map((evidenceId) => (
              <button
                type="button"
                key={evidenceId}
                onClick={() => onSelect({
                  id: evidenceId,
                  kind: "evidence",
                  label: `Доказательство ${shortId(evidenceId, 10, 7)}`,
                  status: "verified",
                  subtitle: "Точный фрагмент источника",
                  snapshotId: selection.snapshotId
                })}
              >
                <FileText size={14} /><code>{shortId(evidenceId, 12, 8)}</code><span>Открыть фрагмент</span>
              </button>
            ))}
          </section>
        ) : null}
        {evidence && typeof evidence.source_id === "string" ? (
          <section className="related-evidence" aria-label="Источник доказательства">
            <div><GitBranch size={15} /><strong>Источник доказательства</strong></div>
            <button
              type="button"
              onClick={() => onSelect({
                id: String(evidence.source_id),
                kind: "source",
                label: typeof evidence.source_label === "string" ? evidence.source_label : "Источник",
                status: "verified",
                subtitle: "Неизменяемая запись источника",
                snapshotId: selection.snapshotId
              })}
            >
              <Database size={14} /><code>{shortId(String(evidence.source_id), 12, 8)}</code><span>Открыть источник</span>
            </button>
          </section>
        ) : null}
        {isCode ? (
          <section className="code-node-actions" aria-label="Действия с узлом кода">
            <div><FileCode2 size={15} /><strong>Граф кода</strong></div>
            <div className="code-action-grid">
              <button type="button" onClick={() => dispatchCodeAction("neighbors", selection.id, "both")}><Network size={14} /> Показать соседей</button>
              <button type="button" onClick={() => dispatchCodeAction("path-source", selection.id)}><GitCompareArrows size={14} /> Найти путь</button>
              <button type="button" onClick={() => dispatchCodeAction("neighbors", selection.id, "out")}><GitBranch size={14} /> Зависимости</button>
              <button type="button" onClick={() => dispatchCodeAction("neighbors", selection.id, "in")}><GitBranch size={14} /> Обратные зависимости</button>
              <button type="button" onClick={() => dispatchCodeAction("impact", selection.id)}><SearchCheck size={14} /> Оценить влияние</button>
              <button type="button" onClick={() => dispatchCodeAction("refresh", selection.id)}><RefreshCw size={14} /> Обновить файл</button>
              <button type="button" onClick={() => dispatchCodeAction("ambiguous", selection.id)}><SearchCheck size={14} /> Неоднозначные связи</button>
              <button type="button" onClick={onCreateTask}><Database size={14} /> Создать задачу</button>
              <button type="button" disabled title="Настройте локальный editor adapter"><ExternalLink size={14} /> Открыть исходник</button>
              <button type="button" disabled={!codePath} onClick={() => { if (codePath) void navigator.clipboard.writeText(codePath); }}><Copy size={14} /> Скопировать путь</button>
            </div>
          </section>
        ) : null}
        <section className="snapshot-boundary">
          <span>Граница среза</span>
          <code>{shortId(selection.snapshotId)}</code>
          <p>Сервер отклоняет детали, которые не совпадают с отпечатком этого слоя. Содержимое никогда не исполняется.</p>
        </section>
      </div>
      <footer className="inspector-footer">
        <button className="secondary-button" type="button" disabled={!isCode} onClick={() => isCode && dispatchCodeAction("neighbors", selection.id, "both")}>
          <ExternalLink size={15} /> {isCode ? "Показать в графе" : "Действия среды выполнения недоступны"}
        </button>
      </footer>
    </aside>
  );
}
