import { Pin, PinOff, RotateCcw, X } from "lucide-react";
import { useRef } from "react";
import type { DocumentTabState } from "./documentTypes";

interface DocumentTabsProps {
  tabs: DocumentTabState[];
  activeDocumentId: string | null;
  canReopen: boolean;
  onActivate: (documentId: string) => void;
  onClose: (documentId: string) => void;
  onCloseOthers: (documentId: string) => void;
  onPin: (documentId: string) => void;
  onReopen: () => void;
}

export function DocumentTabs({
  tabs,
  activeDocumentId,
  canReopen,
  onActivate,
  onClose,
  onCloseOthers,
  onPin,
  onReopen
}: DocumentTabsProps) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const activeIndex = Math.max(0, tabs.findIndex((tab) => tab.documentId === activeDocumentId));

  const move = (event: React.KeyboardEvent, index: number) => {
    let next: number | null = null;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else if ((event.key === "Delete" || event.key === "Backspace") && !tabs[index]?.pinned) {
      event.preventDefault();
      onClose(tabs[index].documentId);
      return;
    }
    if (next === null || !tabs[next]) return;
    event.preventDefault();
    onActivate(tabs[next].documentId);
    refs.current[next]?.focus();
  };

  return (
    <div className="doc-tabs-bar">
      <div className="doc-tabs-scroll" role="tablist" aria-label="Открытые документы" aria-orientation="horizontal">
        {tabs.map((tab, index) => {
          const selected = tab.documentId === activeDocumentId;
          return (
            <div className={`doc-tab-shell ${selected ? "active" : ""}`} key={tab.documentId}>
              <button
                ref={(node) => { refs.current[index] = node; }}
                className="doc-tab"
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls="document-workbench"
                tabIndex={index === activeIndex ? 0 : -1}
                onClick={() => onActivate(tab.documentId)}
                onDoubleClick={() => onPin(tab.documentId)}
                onKeyDown={(event) => move(event, index)}
                title={tab.title}
              >
                {tab.pinned ? <Pin size={12} aria-label="Закреплена" /> : null}
                <span>{tab.title}</span>
                {tab.dirty ? <i aria-label="Есть несохранённые изменения" /> : null}
              </button>
              <button className="doc-tab-pin" type="button" onClick={() => onPin(tab.documentId)} aria-label={tab.pinned ? `Открепить «${tab.title}»` : `Закрепить «${tab.title}»`}>
                {tab.pinned ? <PinOff size={12} /> : <Pin size={12} />}
              </button>
              <button className="doc-tab-close" type="button" onClick={() => onClose(tab.documentId)} aria-label={`Закрыть «${tab.title}»`} disabled={tab.pinned}>
                <X size={13} />
              </button>
              {selected && tabs.length > 1 ? <button className="doc-tab-close-others" type="button" onClick={() => onCloseOthers(tab.documentId)} aria-label="Закрыть другие вкладки">Остальные</button> : null}
            </div>
          );
        })}
      </div>
      <button className="doc-reopen-tab" type="button" onClick={onReopen} disabled={!canReopen} aria-label="Вернуть недавно закрытую вкладку" title="Вернуть вкладку">
        <RotateCcw size={14} />
      </button>
    </div>
  );
}

