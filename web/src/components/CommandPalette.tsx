import { ArrowRight, Command, FileSearch, ListTodo, Search, X } from "lucide-react";
import { useState } from "react";
import { Dialog } from "./Dialog";
import { useGlobalSearch } from "../hooks";
import { kindLabel, routeCopy, statusLabel, type RouteKey } from "../presentation";
import type { Selection } from "../types";

const routeKeys = Object.keys(routeCopy) as RouteKey[];

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onNavigate: (route: string) => void;
  onSelect: (selection: Selection) => void;
  onCreateTask: () => void;
}

export function CommandPalette({
  open,
  onClose,
  onNavigate,
  onSelect,
  onCreateTask
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const search = useGlobalSearch(query);

  if (!open) return null;
  const normalized = query.trim().toLowerCase();
  const filteredRoutes = routeKeys.filter((route) =>
    `${routeCopy[route].label} ${routeCopy[route].description}`.toLowerCase().includes(normalized)
  );

  const moveFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const rows = Array.from(document.querySelectorAll<HTMLButtonElement>(".command-palette .palette-row:not(:disabled)"));
    if (!rows.length) return;
    event.preventDefault();
    const current = rows.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? rows.length - 1
        : event.key === "ArrowDown"
          ? Math.min(rows.length - 1, current + 1)
          : Math.max(0, current < 0 ? rows.length - 1 : current - 1);
    rows[next]?.focus();
  };

  return (
    <Dialog className="command-palette" backdropClassName="modal-backdrop palette-backdrop" label="Палитра команд" describedBy="palette-help" onClose={onClose} onKeyDown={moveFocus}>
        <div className="palette-search">
          <Search size={20} aria-hidden="true" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Перейти или найти проверенный объект…"
            aria-label="Поиск команд и объектов"
          />
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть палитру команд">
            <X size={18} />
          </button>
        </div>
        <div className="palette-body">
          {normalized.length === 0 ? (
            <div className="palette-group">
              <div className="palette-label">Безопасные действия</div>
              <button
                className="palette-row"
                type="button"
                onClick={() => {
                  onCreateTask();
                  onClose();
                }}
              >
                <ListTodo size={17} aria-hidden="true" />
                <span><strong>Создать задачу</strong><small>Запись только в операционный журнал</small></span>
                <ArrowRight size={15} aria-hidden="true" />
              </button>
            </div>
          ) : null}
          {filteredRoutes.length ? (
            <div className="palette-group">
              <div className="palette-label">Навигация</div>
              {filteredRoutes.map((route) => (
                <button
                  className="palette-row"
                  type="button"
                  key={route}
                  onClick={() => {
                    onNavigate(route);
                    onClose();
                  }}
                >
                  <Command size={17} aria-hidden="true" />
                  <span><strong>{routeCopy[route].label}</strong><small>{routeCopy[route].description}</small></span>
                  <ArrowRight size={15} aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : null}
          {query.trim().length > 1 ? (
            <div className="palette-group">
              <div className="palette-label">Проверенные объекты</div>
              {search.isLoading ? <div className="palette-note">Ищем в локальном срезе…</div> : null}
              {search.data?.results.map((result) => (
                <button
                  className="palette-row"
                  type="button"
                  key={`${result.kind}:${result.id}`}
                  onClick={() => {
                    onSelect({
                      id: result.id,
                      kind: result.kind,
                      label: result.label,
                      status: result.status,
                      subtitle: result.subtitle,
                      snapshotId: result.snapshot_id
                    });
                    onClose();
                  }}
                >
                  <FileSearch size={17} aria-hidden="true" />
                  <span><strong>{result.label}</strong><small>{kindLabel(result.kind)} · {statusLabel(result.status)}</small></span>
                  <ArrowRight size={15} aria-hidden="true" />
                </button>
              ))}
              {search.data?.results.length === 0 ? (
                <div className="palette-note">В этом срезе совпадений нет.</div>
              ) : null}
            </div>
          ) : null}
        </div>
        <footer className="palette-footer" id="palette-help">
          <span>Введите запрос для фильтрации</span>
          <span><kbd>↑↓</kbd> выбор</span>
          <span><kbd>esc</kbd> закрыть</span>
          <span className="local-foot"><span /> только локальный индекс</span>
        </footer>
    </Dialog>
  );
}
