import { ChevronRight, FileText, Folder, FolderOpen, LockKeyhole, Shield } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DocumentFolderSummary, DocumentRootMode, DocumentRootSummary, DocumentSummary } from "./documentTypes";

interface TreeFolder {
  id: string;
  name: string;
  path: string;
  rootId: string;
  workspacePath: string;
  folders: Map<string, TreeFolder>;
  documents: DocumentSummary[];
  mode?: DocumentRootMode;
  documentCount?: number;
  descendantCount?: number;
}

interface VisibleEntry {
  id: string;
  type: "folder" | "document";
  name: string;
  depth: number;
  parentId: string | null;
  expanded?: boolean;
  folder?: TreeFolder;
  document?: DocumentSummary;
  count?: number;
}

interface DocumentTreeProps {
  documents: DocumentSummary[];
  folders?: DocumentFolderSummary[];
  roots?: DocumentRootSummary[];
  selectedDocumentId: string | null;
  loading?: boolean;
  onOpen: (document: DocumentSummary, disposition: "current" | "new") => void;
  onExpandFolder?: (rootId: string, parentPath: string) => void;
}

const ROW_HEIGHT = 36;
const OVERSCAN = 8;

function relativeToRoot(path: string, rootPath: string): string {
  const normalizedRoot = rootPath.replace(/^\/+|\/+$/g, "");
  const normalizedPath = path.replace(/^\/+|\/+$/g, "");
  return normalizedRoot && (normalizedPath === normalizedRoot || normalizedPath.startsWith(`${normalizedRoot}/`))
    ? normalizedPath.slice(normalizedRoot.length).replace(/^\/+/, "")
    : normalizedPath;
}

function buildTree(documents: DocumentSummary[], projections: DocumentFolderSummary[], roots: DocumentRootSummary[]): TreeFolder {
  const root: TreeFolder = { id: "folder:/", name: "workspace", path: "", rootId: "", workspacePath: "", folders: new Map(), documents: [] };
  const rootMeta = new Map(roots.map((item) => [item.root_id, item]));
  const rootFolders = new Map<string, TreeFolder>();
  const ensureRoot = (rootId: string): TreeFolder => {
    const existing = rootFolders.get(rootId);
    if (existing) return existing;
    const meta = rootMeta.get(rootId);
    const folder: TreeFolder = { id: `folder-root:${rootId}`, name: meta?.label ?? rootId, path: "", rootId, workspacePath: meta?.path ?? "", folders: new Map(), documents: [], mode: meta?.mode };
    rootFolders.set(rootId, folder);
    root.folders.set(rootId, folder);
    return folder;
  };
  const ensureFolder = (rootId: string, workspacePath: string, projection?: DocumentFolderSummary): TreeFolder => {
    const rootFolder = ensureRoot(rootId);
    const relativePath = relativeToRoot(workspacePath, rootMeta.get(rootId)?.path ?? "");
    const parts = relativePath.split("/").filter(Boolean);
    let folder = rootFolder;
    let path = "";
    for (const part of parts) {
      path = path ? `${path}/${part}` : part;
      let next = folder.folders.get(part);
      if (!next) {
        const rootPath = rootMeta.get(rootId)?.path.replace(/^\/+|\/+$/g, "") ?? "";
        next = { id: `folder:${rootId}:${path}`, name: part, path, rootId, workspacePath: [rootPath, path].filter(Boolean).join("/"), folders: new Map(), documents: [] };
        folder.folders.set(part, next);
      }
      folder = next;
    }
    if (projection) {
      folder.id = projection.folder_id;
      folder.name = projection.name || folder.name;
      folder.workspacePath = projection.path;
      folder.mode = projection.mode;
      folder.documentCount = projection.document_count;
      folder.descendantCount = projection.descendant_count;
    }
    return folder;
  };
  for (const projection of projections) ensureFolder(projection.root_id, projection.path, projection);
  for (const document of documents) {
    const parts = document.path.split("/").filter(Boolean);
    parts.pop();
    const folder = ensureFolder(document.root_id, parts.join("/"));
    folder.documents.push(document);
  }
  return root;
}

function countDocuments(folder: TreeFolder): number {
  if (folder.descendantCount !== undefined) return folder.descendantCount;
  let count = 0;
  const stack = [folder];
  while (stack.length) {
    const current = stack.pop();
    if (!current) continue;
    count += current.documents.length;
    for (const child of current.folders.values()) {
      if (child.descendantCount !== undefined) count += child.descendantCount;
      else stack.push(child);
    }
  }
  return count;
}

function flattenTree(folder: TreeFolder, expanded: ReadonlySet<string>, depth = 1, parentId: string | null = null): VisibleEntry[] {
  const entries: VisibleEntry[] = [];
  const folders = [...folder.folders.values()].sort((a, b) => a.name.localeCompare(b.name, "ru-RU"));
  for (const child of folders) {
    const isExpanded = expanded.has(child.id);
    entries.push({ id: child.id, type: "folder", name: child.name, depth, parentId, expanded: isExpanded, folder: child, count: countDocuments(child) });
    if (isExpanded) entries.push(...flattenTree(child, expanded, depth + 1, child.id));
  }
  for (const document of [...folder.documents].sort((a, b) => a.filename.localeCompare(b.filename, "ru-RU"))) {
    entries.push({ id: document.document_id, type: "document", name: document.title || document.filename, depth, parentId, document });
  }
  return entries;
}

export function DocumentTree({ documents, folders = [], roots = [], selectedDocumentId, loading, onOpen, onExpandFolder }: DocumentTreeProps) {
  const tree = useMemo(() => buildTree(documents, folders, roots), [documents, folders, roots]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([...tree.folders.values()].map((folder) => folder.id)));
  const [focusedId, setFocusedId] = useState<string | null>(selectedDocumentId);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(520);
  const viewportRef = useRef<HTMLDivElement>(null);
  const visible = useMemo(() => flattenTree(tree, expanded), [expanded, tree]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setViewportHeight(entry.contentRect.height));
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!selectedDocumentId) return;
    const frame = requestAnimationFrame(() => setFocusedId(selectedDocumentId));
    return () => cancelAnimationFrame(frame);
  }, [selectedDocumentId]);

  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const count = Math.ceil(viewportHeight / ROW_HEIGHT) + OVERSCAN * 2;
  const rendered = visible.slice(first, first + count);

  const focusEntry = (index: number) => {
    const boundedIndex = Math.max(0, Math.min(visible.length - 1, index));
    const entry = visible[boundedIndex];
    if (!entry) return;
    setFocusedId(entry.id);
    const viewport = viewportRef.current;
    if (viewport) {
      const top = boundedIndex * ROW_HEIGHT;
      const bottom = top + ROW_HEIGHT;
      if (top < viewport.scrollTop) viewport.scrollTop = top;
      else if (bottom > viewport.scrollTop + viewport.clientHeight) viewport.scrollTop = Math.max(0, bottom - viewport.clientHeight);
      setScrollTop(viewport.scrollTop);
    }
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const next = [...(viewportRef.current?.querySelectorAll<HTMLElement>("[data-tree-id]") ?? [])]
        .find((candidate) => candidate.dataset.treeId === entry.id);
      next?.focus();
    }));
  };

  const toggleFolder = (entry: VisibleEntry, force?: boolean) => {
    if (entry.type !== "folder") return;
    const opening = force ?? !expanded.has(entry.id);
    if (opening && entry.folder && !entry.id.startsWith("folder-root:")) onExpandFolder?.(entry.folder.rootId, entry.folder.workspacePath);
    setExpanded((current) => {
      const next = new Set(current);
      const open = force ?? !next.has(entry.id);
      if (open) next.add(entry.id); else next.delete(entry.id);
      return next;
    });
  };

  const onKeyDown = (event: React.KeyboardEvent, entry: VisibleEntry) => {
    const index = visible.findIndex((item) => item.id === entry.id);
    if (event.key === "ArrowDown") { event.preventDefault(); focusEntry(index + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); focusEntry(index - 1); }
    else if (event.key === "Home") { event.preventDefault(); focusEntry(0); }
    else if (event.key === "End") { event.preventDefault(); focusEntry(visible.length - 1); }
    else if (event.key === "ArrowRight" && entry.type === "folder") {
      event.preventDefault();
      if (entry.expanded) focusEntry(index + 1);
      else toggleFolder(entry, true);
    }
    else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (entry.type === "folder" && entry.expanded) toggleFolder(entry, false);
      else if (entry.parentId) focusEntry(visible.findIndex((item) => item.id === entry.parentId));
    } else if ((event.key === "Enter" || event.key === " ") && entry.document) {
      event.preventDefault();
      onOpen(entry.document, event.ctrlKey || event.metaKey ? "new" : "current");
    }
  };

  if (!documents.length && !folders.length && !roots.length && !loading) return <div className="doc-tree-empty">Разрешённые документы не найдены.</div>;
  return (
    <div
      ref={viewportRef}
      className="doc-tree-viewport"
      role="tree"
      aria-label="Файлы документного workspace"
      aria-busy={loading || undefined}
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
    >
      <div className="doc-tree-spacer">
        {first > 0 ? <svg className="doc-tree-virtual-space" width="1" height={first * ROW_HEIGHT} aria-hidden="true" /> : null}
        {rendered.map((entry, renderedIndex) => {
          const index = first + renderedIndex;
          const selected = entry.document?.document_id === selectedDocumentId;
          return (
            <div
              key={entry.id}
              className={`doc-tree-row ${selected ? "selected" : ""}`}
              data-tree-id={entry.id}
              data-clickable="true"
              role="treeitem"
              aria-label={entry.type === "folder" ? `${entry.name}, ${entry.count ?? 0} документов` : undefined}
              aria-level={entry.depth}
              aria-expanded={entry.type === "folder" ? entry.expanded : undefined}
              aria-selected={entry.type === "document" ? selected : undefined}
              tabIndex={focusedId === entry.id || (!focusedId && index === 0) ? 0 : -1}
              onFocus={() => setFocusedId(entry.id)}
              onKeyDown={(event) => onKeyDown(event, entry)}
              onClick={() => entry.type === "folder" ? toggleFolder(entry) : entry.document && onOpen(entry.document, "current")}
              onDoubleClick={() => entry.document && onOpen(entry.document, "new")}
            >
              {Array.from({ length: Math.max(0, entry.depth - 1) }, (_, depth) => <span className="doc-tree-indent" key={depth} aria-hidden="true" />)}
              {entry.type === "folder" ? <ChevronRight className={entry.expanded ? "expanded" : ""} size={14} aria-hidden="true" /> : <span className="doc-tree-chevron" />}
              {entry.type === "folder" ? (entry.expanded ? <FolderOpen size={15} aria-hidden="true" /> : <Folder size={15} aria-hidden="true" />) : <FileText size={15} aria-hidden="true" />}
              <span title={entry.document?.path ?? entry.folder?.workspacePath}>{entry.name}</span>
              {entry.type === "folder" ? <small className="doc-tree-count" aria-label={`${entry.count ?? 0} документов`}>{entry.count ?? 0}</small> : null}
              {entry.folder?.mode === "protected_read_only" ? <Shield size={13} aria-label="Защищено" /> : entry.folder?.mode === "read_only" ? <LockKeyhole size={13} aria-label="Только чтение" /> : null}
              {entry.document?.mode === "protected_read_only" ? <Shield size={13} aria-label="Защищено" /> : entry.document && !entry.document.can_edit ? <LockKeyhole size={13} aria-label="Только чтение" /> : null}
              {entry.document?.is_modified ? <i className="doc-tree-modified" aria-label="Изменён" /> : null}
            </div>
          );
        })}
        {first + rendered.length < visible.length ? <svg className="doc-tree-virtual-space" width="1" height={(visible.length - first - rendered.length) * ROW_HEIGHT} aria-hidden="true" /> : null}
      </div>
    </div>
  );
}
