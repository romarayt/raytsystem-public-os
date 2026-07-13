import { Clock3, ExternalLink, GitBranch, Link2, ListTree, Network, RotateCcw, Tags, X } from "lucide-react";
import { useRef } from "react";
import { formatDate } from "../../api";
import type {
  DocumentBacklinksEnvelope,
  DocumentDetailEnvelope,
  DocumentHistoryEnvelope,
  DocumentHistoryEntry,
  DocumentLinksEnvelope,
  DocumentWorkspaceState,
  FrontmatterField
} from "./documentTypes";
import { deriveFrontmatterFields, updateFrontmatterField } from "./markdownCodec";

interface DocumentInspectorProps {
  detail: DocumentDetailEnvelope;
  content: string;
  section: DocumentWorkspaceState["inspectorSection"];
  links?: DocumentLinksEnvelope;
  backlinks?: DocumentBacklinksEnvelope;
  history?: DocumentHistoryEnvelope;
  onSectionChange: (section: DocumentWorkspaceState["inspectorSection"]) => void;
  onContentChange: (content: string, warning?: string) => void;
  onOpenDocument: (documentId: string, heading?: string | null) => void;
  onPreviewRevision: (entry: DocumentHistoryEntry) => void;
  onRequestRestore: (entry: DocumentHistoryEntry) => void;
  onShowInGraph: () => void;
  propertyEditingDisabled?: boolean;
  onClose?: () => void;
}

const sections: Array<{ id: DocumentWorkspaceState["inspectorSection"]; label: string; icon: typeof Tags }> = [
  { id: "properties", label: "Свойства", icon: Tags },
  { id: "links", label: "Ссылки", icon: Link2 },
  { id: "backlinks", label: "Backlinks", icon: GitBranch },
  { id: "history", label: "История", icon: Clock3 }
];

function fieldValue(field: FrontmatterField): string | number {
  if (Array.isArray(field.value)) return field.value.join(", ");
  if (typeof field.value === "boolean") return field.value ? "true" : "false";
  if (field.value && typeof field.value === "object") return JSON.stringify(field.value).slice(0, 4_096);
  return field.value ?? "";
}

function parsedValue(field: FrontmatterField, input: string): FrontmatterField["value"] {
  if (["list", "tags", "aliases"].includes(field.type)) return input.split(",").map((item) => item.trim()).filter(Boolean);
  if (field.type === "number") return Number.isFinite(Number(input)) ? Number(input) : field.value;
  if (field.type === "boolean") return input === "true";
  return input;
}

export function DocumentInspector({
  detail,
  content,
  section,
  links,
  backlinks,
  history,
  onSectionChange,
  onContentChange,
  onOpenDocument,
  onPreviewRevision,
  onRequestRestore,
  onShowInGraph,
  propertyEditingDisabled = false,
  onClose
}: DocumentInspectorProps) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const serverFields = new Map((detail.frontmatter ?? []).map((field) => [field.key, field]));
  const fields = deriveFrontmatterFields(content).map((field) => {
    const server = serverFields.get(field.key);
    if (!server) return field;
    const complex = field.type === "complex" || server.type === "complex";
    return {
      ...field,
      type: complex ? "complex" as const : server.type,
      editable: !complex && field.editable && server.editable
    };
  });
  const updateField = (field: FrontmatterField, input: string) => {
    const result = updateFrontmatterField(content, field, parsedValue(field, input));
    onContentChange(result.content, result.warning ?? undefined);
  };
  return (
    <aside className="doc-inspector" aria-label="Сведения о документе">
      <header><div><span>{detail.document.kind}</span><strong>{detail.document.title}</strong></div>{onClose ? <button type="button" onClick={onClose} aria-label="Закрыть сведения"><X size={17} /></button> : null}</header>
      <div className="doc-inspector-tabs" role="tablist" aria-label="Сведения о документе" aria-orientation="horizontal">
        {sections.map(({ id, label, icon: Icon }, index) => <button ref={(node) => { tabRefs.current[index] = node; }} id={`doc-inspector-tab-${id}`} type="button" role="tab" aria-selected={section === id} aria-controls="doc-inspector-panel" tabIndex={section === id ? 0 : -1} key={id} onClick={() => onSectionChange(id)} onKeyDown={(event) => {
          let next: number | undefined;
          if (event.key === "ArrowRight") next = (index + 1) % sections.length;
          else if (event.key === "ArrowLeft") next = (index - 1 + sections.length) % sections.length;
          else if (event.key === "Home") next = 0;
          else if (event.key === "End") next = sections.length - 1;
          if (next === undefined) return;
          event.preventDefault();
          onSectionChange(sections[next].id);
          tabRefs.current[next]?.focus();
        }}><Icon size={14} aria-hidden="true" /><span>{label}</span></button>)}
      </div>
      <div id="doc-inspector-panel" className="doc-inspector-content" role="tabpanel" aria-labelledby={`doc-inspector-tab-${section}`} tabIndex={0}>
        {section === "properties" ? (
          <div className="doc-property-list">
            <dl><div><dt>Путь</dt><dd><code>{detail.document.path}</code></dd></div><div><dt>Режим</dt><dd>{detail.document.mode}</dd></div><div><dt>SHA-256</dt><dd><code>{detail.content_sha256.slice(0, 16)}</code></dd></div><div><dt>Изменён</dt><dd>{formatDate(detail.document.modified_at)}</dd></div></dl>
            {propertyEditingDisabled ? <p role="status">Свойства доступны для изменения в Source mode: открытый визуальный editor хранит собственную модель документа.</p> : null}
            {fields.length ? fields.map((field) => (
              <label key={field.key}><span>{field.key}{!field.editable || propertyEditingDisabled ? <small>Source only</small> : null}</span>
                {field.type === "boolean" ? <select value={field.value === true ? "true" : "false"} disabled={propertyEditingDisabled || !field.editable || !detail.document.can_edit} onChange={(event) => updateField(field, event.target.value)}><option value="true">Да</option><option value="false">Нет</option></select>
                  : <input type={field.type === "date" ? "date" : field.type === "number" ? "number" : "text"} value={fieldValue(field)} disabled={propertyEditingDisabled || !field.editable || !detail.document.can_edit} onChange={(event) => updateField(field, event.target.value)} />}
              </label>
            )) : <p>Frontmatter отсутствует.</p>}
          </div>
        ) : null}
        {section === "links" ? (
          <div className="doc-link-list">
            {links?.items.length ? links.items.map((link, index) => (
              <article key={`${link.target}:${index}`}><div><Link2 size={14} /><strong>{link.label || link.target}</strong>{link.heading ? <small>#{link.heading}</small> : null}</div><p>{link.context}</p>
                {link.target_document_id ? <button type="button" onClick={() => onOpenDocument(link.target_document_id!, link.heading)}>Открыть</button> : link.ambiguous ? <div>{link.candidates?.map((candidate) => <button type="button" key={candidate.document_id} onClick={() => onOpenDocument(candidate.document_id, link.heading)}>{candidate.title}<small>{candidate.path}</small></button>)}</div> : <span>Цель не найдена</span>}
              </article>
            )) : <p>Исходящих ссылок нет.</p>}
            {links?.next_cursor ? <p role="status">Показана первая страница ссылок. Уточните фильтр или откройте граф для полного bounded neighborhood.</p> : null}
          </div>
        ) : null}
        {section === "backlinks" ? (
          <div className="doc-link-list">
            {backlinks?.items.length ? backlinks.items.map((backlink, index) => <article key={`${backlink.source_document_id}:${index}`}><div><GitBranch size={14} /><strong>{backlink.source_title}</strong></div><small>{backlink.source_path}{backlink.line ? `:${backlink.line}` : ""}</small><p>{backlink.context}</p><button type="button" onClick={() => onOpenDocument(backlink.source_document_id)}>Перейти</button></article>) : <p>Обратных ссылок нет.</p>}
            {backlinks?.next_cursor ? <p role="status">Backlinks ограничены первой страницей; полный контекст доступен через граф.</p> : null}
          </div>
        ) : null}
        {section === "history" ? (
          <div className="doc-history-list">
            {history?.items.length ? history.items.map((entry) => <article key={entry.history_id}><div><Clock3 size={14} /><strong>{formatDate(entry.recorded_at)}</strong><span>{entry.source}</span></div><code>{entry.content_sha256?.slice(0, 14) ?? "hash при открытии"}</code>{entry.author ? <small>{entry.author}</small> : null}{entry.summary ? <p>{entry.summary}</p> : null}<footer><button type="button" onClick={() => onPreviewRevision(entry)}>Diff и копия</button><button type="button" disabled={!detail.document.can_edit} onClick={() => onRequestRestore(entry)}><RotateCcw size={13} />Восстановить…</button></footer></article>) : <p>История пока недоступна.</p>}
            {history?.next_cursor ? <p role="status">Показана первая страница истории.</p> : null}
          </div>
        ) : null}
      </div>
      <footer><button type="button" onClick={onShowInGraph}><Network size={15} />Показать в графе</button><button type="button" onClick={() => void navigator.clipboard.writeText(detail.document.path)}><ExternalLink size={15} />Скопировать путь</button><span><ListTree size={14} />{detail.document.backlink_count} / {detail.document.outgoing_link_count}</span></footer>
    </aside>
  );
}
