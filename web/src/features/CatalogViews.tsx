import { FileKey2, Search, ShieldCheck, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { shortId } from "../api";
import { useCatalog } from "../hooks";
import { localizedCatalogLabel } from "../presentation";
import type { Selection } from "../types";
import { EmptyState, ErrorState, LoadingState, StatusPill } from "../components/StatePanel";

function CatalogShell({
  query,
  setQuery,
  children
}: {
  query: string;
  setQuery: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="route route-list">
      <div className="route-tools">
        <label className="search-field"><Search size={16} /><input aria-label="Фильтр разрешённого каталога" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Фильтр разрешённого каталога" /></label>
        <span className="inert-badge"><ShieldCheck size={14} /> пассивные определения</span>
      </div>
      {children}
    </div>
  );
}

export function Context({ onSelect }: { onSelect: (selection: Selection) => void }) {
  const catalog = useCatalog();
  const [query, setQuery] = useState("");
  const documents = useMemo(
    () => (catalog.data?.instructions ?? []).filter((document) => `${document.label} ${localizedCatalogLabel(document.document_id, document.label)} ${document.kind}`.toLowerCase().includes(query.toLowerCase())),
    [catalog.data?.instructions, query]
  );
  if (catalog.isLoading) return <LoadingState label="Читаем отпечатки документов контекста…" />;
  if (catalog.isError) return <ErrorState error={catalog.error} />;
  return (
    <CatalogShell query={query} setQuery={setQuery}>
      <section className="context-intro panel"><Sparkles size={20} /><div><span className="eyebrow">Точный контекст виден заранее</span><h3>Проверьте, что может получить агент, ещё до появления среды выполнения.</h3></div></section>
      {!documents.length ? <EmptyState title={query ? "Документы не найдены" : "Нет разрешённых документов с инструкциями"} action={query ? <button className="secondary-button" type="button" onClick={() => setQuery("")}>Сбросить фильтр</button> : undefined}>{query ? "Измените запрос или сбросьте фильтр." : "Кандидатами корневого контекста могут быть только AGENTS.md, WORK.md и CLAUDE.md."}</EmptyState> : (
        <div className="context-grid">
          {documents.map((document) => (
            <button
              className="context-card panel"
              type="button"
              key={document.document_id}
              onClick={() => onSelect({
                id: document.document_id,
                kind: "instruction",
                label: localizedCatalogLabel(document.document_id, document.label),
                status: document.sensitivity,
                subtitle: "Документ инструкций",
                metadata: {
                  sha256: document.content_sha256,
                  size_bytes: String(document.size_bytes),
                  editable: String(document.editable)
                },
                snapshotId: catalog.data?.catalog_sha256
              })}
            >
              <span className="catalog-icon context"><FileKey2 size={20} /></span>
              <span className="eyebrow">документ инструкций</span>
              <h3>{localizedCatalogLabel(document.document_id, document.label)}</h3>
              <code>{shortId(document.content_sha256, 11, 8)}</code>
              <footer><span>{Math.ceil(document.size_bytes / 1024)} KB</span><StatusPill status={document.sensitivity} /></footer>
            </button>
          ))}
        </div>
      )}
    </CatalogShell>
  );
}
