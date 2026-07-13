import {
  ArrowDownAZ,
  BookOpen,
  Clock3,
  FileDiff,
  FilePlus2,
  Files,
  FolderPlus,
  GitCompare,
  Menu,
  Move,
  PanelRight,
  Pencil,
  RefreshCw,
  Save,
  Search,
  Shield,
  Sparkles,
  Star,
  X
} from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "../../components/StatePanel";
import { Dialog } from "../../components/Dialog";
import {
  DocumentApiError,
  documentGet,
  newIntentKey,
  queryString,
  useCreateDocument,
  useCreateDocumentFolder,
  useDocumentBacklinks,
  useDocumentDetail,
  useDocumentHistory,
  useDocumentIndexMutation,
  useDocumentLinks,
  useDocumentListing,
  useDocumentRestorePreview,
  useDocumentRevisionDetail,
  useMoveDocument,
  useRenameDocument,
  useRestoreDocument,
  useUpdateDocument
} from "./documentHooks";
import {
  documentWorkspaceReducer,
  persistDocumentWorkspace,
  restoreDocumentWorkspace
} from "./documentState";
import type {
  DocumentConflictDetails,
  DocumentDetailEnvelope,
  DocumentFolderSummary,
  DocumentFoldersEnvelope,
  DocumentHistoryEntry,
  DocumentLink,
  DocumentMode,
  DocumentQuery,
  DocumentRootMode,
  DocumentRootSummary,
  DocumentRestorePreviewEnvelope,
  DocumentSort,
  DocumentSummary,
  DocumentTabState,
  DocumentView
} from "./documentTypes";
import { inspectMarkdownForVisualEditing, visualEditorBlockReason, type MarkdownIssue } from "./markdownCodec";
import { DocumentActionDialog, type DocumentActionKind, type DocumentActionValue, type DocumentRootOption } from "./DocumentActionDialog";
import { DocumentConflictDialog } from "./DocumentConflictDialog";
import { DocumentDiff } from "./DocumentDiff";
import { DocumentInspector } from "./DocumentInspector";
import { DocumentRestoreDialog } from "./DocumentRestoreDialog";
import { DocumentTabs } from "./DocumentTabs";
import { DocumentTree } from "./DocumentTree";
import { headingId, SafeMarkdownView, safeImageUrl, type WikilinkTarget } from "./SafeMarkdownView";
import "./documents.css";

const SourceEditor = lazy(() => import("./SourceEditor"));
const VisualEditor = lazy(() => import("./VisualEditor"));

export interface DocumentsProps {
  onShowInGraph: (documentId: string) => void;
  initialDocumentId?: string | null;
}

const views: Array<{ id: DocumentView; label: string; icon: typeof Files }> = [
  { id: "files", label: "Файлы", icon: Files },
  { id: "recent", label: "Недавние", icon: Clock3 },
  { id: "added", label: "Добавленные", icon: Sparkles },
  { id: "modified", label: "Изменённые", icon: GitCompare },
  { id: "favorites", label: "Избранное", icon: Star }
];

const modes: Array<{ id: DocumentMode; label: string; icon: typeof BookOpen }> = [
  { id: "read", label: "Чтение", icon: BookOpen },
  { id: "visual", label: "Визуально", icon: Sparkles },
  { id: "source", label: "Markdown", icon: Files },
  { id: "diff", label: "Diff", icon: FileDiff }
];

function routeDocumentId(initialDocumentId?: string | null): string | null {
  if (initialDocumentId) return initialDocumentId;
  const candidate = new URLSearchParams(window.location.search).get("document");
  return candidate && /^doc_[A-Za-z0-9_-]+$/.test(candidate) ? candidate : null;
}

export function folderWithinRoot(path: string, rootPath: string): string {
  const parts = path.split("/").filter(Boolean);
  const rootParts = rootPath.split("/").filter(Boolean);
  if (!rootParts.length || parts.length <= rootParts.length) return "";
  if (rootParts.some((part, index) => parts[index] !== part)) return "";
  return parts.slice(rootParts.length, -1).join("/");
}

function wikilinkBase(value: string): string {
  return value.split("|", 1)[0].split("#", 1)[0].trim();
}

export function matchingDocumentLink(target: WikilinkTarget, links: DocumentLink[]): DocumentLink | undefined {
  return links.find((link) => {
    const sameTarget = wikilinkBase(link.target).localeCompare(target.target, "ru-RU", { sensitivity: "base" }) === 0;
    const sameHeading = !target.heading || Boolean(link.heading && link.heading.localeCompare(target.heading, "ru-RU", { sensitivity: "base" }) === 0);
    return sameTarget && sameHeading;
  });
}

export function focusMarkdownHeading(scope: ParentNode, heading: string): boolean {
  const id = headingId(heading);
  const element = [...scope.querySelectorAll<HTMLElement>("[id]")].find((candidate) => candidate.id === id);
  if (!element) return false;
  element.tabIndex = -1;
  element.scrollIntoView({ block: "start" });
  element.focus({ preventScroll: true });
  return true;
}

function tabFor(document: DocumentSummary): DocumentTabState {
  return {
    documentId: document.document_id,
    title: document.title || document.filename,
    mode: "read",
    pinned: false,
    dirty: false,
    readOnly: !document.can_edit
  };
}

function placeholderTab(documentId: string): DocumentTabState {
  return { documentId, title: "Документ", mode: "read", pinned: false, dirty: false, readOnly: true };
}

function mutationMessage(error: unknown): string {
  return error instanceof DocumentApiError ? error.message : "Операция с документом не выполнена.";
}

function restoreFavorites(): Set<string> {
  try {
    const values = JSON.parse(window.sessionStorage.getItem("raytsystem.documents.favorites.v1") ?? "[]") as unknown;
    return new Set(Array.isArray(values) ? values.filter((value): value is string => typeof value === "string").slice(0, 100) : []);
  } catch {
    return new Set();
  }
}

function DocumentImageView({ detail }: { detail: DocumentDetailEnvelope }) {
  const source = safeImageUrl(detail.asset_url ?? "");
  return source ? (
    <figure className="doc-image-view">
      <img src={source} alt={detail.document.title || detail.document.filename} loading="lazy" decoding="async" />
      <figcaption><strong>{detail.document.title || detail.document.filename}</strong><span>{detail.image?.mime_type ?? detail.mime_type ?? detail.document.extension} · {detail.document.size_bytes.toLocaleString("ru-RU")} байт{detail.image?.width && detail.image.height ? ` · ${detail.image.width}×${detail.image.height}` : ""}</span><span>Вложение доступно только для безопасного просмотра.</span></figcaption>
    </figure>
  ) : <div className="doc-visual-unavailable" role="alert"><strong>Изображение заблокировано</strong><p>Сервер не выдал разрешённый opaque asset URL.</p></div>;
}

export function Documents({ onShowInGraph, initialDocumentId }: DocumentsProps) {
  const [workspace, dispatch] = useReducer(documentWorkspaceReducer, undefined, restoreDocumentWorkspace);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [rootId, setRootId] = useState("");
  const [kind, setKind] = useState("");
  const [policyMode, setPolicyMode] = useState<DocumentRootMode | "all">("all");
  const [sort, setSort] = useState<DocumentSort>("modified_desc");
  const [action, setAction] = useState<DocumentActionKind | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState<DocumentConflictDetails | null>(null);
  const [revisionTarget, setRevisionTarget] = useState<DocumentHistoryEntry | null>(null);
  const [pendingHeading, setPendingHeading] = useState<{ documentId: string; heading: string } | null>(null);
  const [restoreRevision, setRestoreRevision] = useState<DocumentHistoryEntry | null>(null);
  const [restorePreview, setRestorePreview] = useState<DocumentRestorePreviewEnvelope | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [visualBlocked, setVisualBlocked] = useState<Record<string, boolean>>({});
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(restoreFavorites);
  const [pendingTabClose, setPendingTabClose] = useState<{ kind: "one" | "others"; documentId: string; label: string } | null>(null);
  const initializedRoute = useRef(false);
  const workspaceRef = useRef(workspace);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const navigationDrawerRef = useRef<HTMLElement>(null);
  const inspectorDrawerRef = useRef<HTMLDivElement>(null);
  const drawerReturnFocusRef = useRef<HTMLElement | null>(null);
  const previousDrawerRef = useRef<"navigation" | "inspector" | null>(null);
  const folderRequests = useRef(new Set<string>());
  const [expandedFolderProjection, setExpandedFolderProjection] = useState<{ snapshotId: string; items: DocumentFolderSummary[] }>({ snapshotId: "", items: [] });

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 180);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const timer = window.setTimeout(() => persistDocumentWorkspace(workspace), 350);
    return () => window.clearTimeout(timer);
  }, [workspace]);

  useLayoutEffect(() => {
    workspaceRef.current = workspace;
  }, [workspace]);

  useEffect(() => () => persistDocumentWorkspace(workspaceRef.current), []);

  useEffect(() => {
    try { window.sessionStorage.setItem("raytsystem.documents.favorites.v1", JSON.stringify([...favoriteIds])); } catch { /* best effort */ }
  }, [favoriteIds]);

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (window.location.pathname !== "/documents" || !(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "f") return;
      if ((event.target as HTMLElement | null)?.closest("[data-editor-scope]")) return;
      event.preventDefault();
      searchInputRef.current?.focus();
      searchInputRef.current?.select();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);

  useEffect(() => {
    const dirty = workspace.tabs.some((tab) => tab.dirty);
    const listener = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  }, [workspace.tabs]);

  useEffect(() => {
    const drawer = workspace.mobileDrawer;
    if (!drawer) {
      if (previousDrawerRef.current) {
        const frame = requestAnimationFrame(() => drawerReturnFocusRef.current?.focus());
        previousDrawerRef.current = null;
        return () => cancelAnimationFrame(frame);
      }
      return undefined;
    }
    previousDrawerRef.current = drawer;
    const frame = requestAnimationFrame(() => {
      const panel = drawer === "navigation" ? navigationDrawerRef.current : inspectorDrawerRef.current;
      panel?.querySelector<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])")?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [workspace.mobileDrawer]);

  useEffect(() => {
    if (!workspace.mobileDrawer) return;
    const background = Array.from(document.querySelectorAll<HTMLElement>(".sidebar, .topbar, .activity-strip, .mobile-nav"));
    const previous = background.map((element) => [element, element.inert] as const);
    for (const element of background) element.inert = true;
    return () => {
      for (const [element, inert] of previous) element.inert = inert;
    };
  }, [workspace.mobileDrawer]);

  useEffect(() => {
    if (initializedRoute.current) return;
    initializedRoute.current = true;
    const documentId = routeDocumentId(initialDocumentId);
    if (documentId && !workspace.tabs.some((tab) => tab.documentId === documentId)) dispatch({ type: "open", tab: placeholderTab(documentId) });
  }, [initialDocumentId, workspace.tabs]);

  useEffect(() => {
    if (window.location.pathname !== "/documents") return;
    const url = new URL(window.location.href);
    if (workspace.activeDocumentId) url.searchParams.set("document", workspace.activeDocumentId);
    else url.searchParams.delete("document");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, [workspace.activeDocumentId]);

  const listingQuery: DocumentQuery = {
    view: workspace.view === "favorites" ? "files" : workspace.view,
    query: debouncedQuery,
    documentIds: workspace.view === "favorites" ? [...favoriteIds].slice(0, 100) : undefined,
    rootId: rootId || undefined,
    kind: kind || undefined,
    mode: policyMode,
    sort,
    limit: 50
  };
  const listing = useDocumentListing(listingQuery);
  const listedDocuments = useMemo(() => listing.data?.pages.flatMap((page) => page.items) ?? [], [listing.data?.pages]);
  const documents = useMemo(() => {
    if (workspace.view !== "favorites") return listedDocuments;
    const needle = debouncedQuery.trim().toLocaleLowerCase("ru-RU");
    return listedDocuments.filter((document) => favoriteIds.has(document.document_id) && (!needle || [document.filename, document.path, document.title, ...document.tags, ...document.aliases, ...document.headings].some((value) => value.toLocaleLowerCase("ru-RU").includes(needle))));
  }, [debouncedQuery, favoriteIds, listedDocuments, workspace.view]);
  const index = listing.data?.pages[0]?.index;
  const snapshotId = listing.data?.pages[0]?.snapshot_id ?? index?.snapshot_id ?? "";
  const activeId = workspace.activeDocumentId;
  const activeTab = workspace.tabs.find((tab) => tab.documentId === activeId) ?? null;
  const detail = useDocumentDetail(activeId, snapshotId || null);
  const links = useDocumentLinks(activeId, snapshotId || null);
  const backlinks = useDocumentBacklinks(activeId, snapshotId || null);
  const history = useDocumentHistory(activeId, snapshotId || null);
  const revisionDetail = useDocumentRevisionDetail(activeId, revisionTarget?.history_id ?? null, snapshotId || null);
  const activeDraft = activeId ? workspace.drafts[activeId] : undefined;
  const activeIsImage = detail.data?.format === "image";
  const activeUnsupported = detail.data?.format === "unsupported";
  const visualBlockReason = activeDraft ? visualEditorBlockReason(activeDraft.content, detail.data?.visual_qualification) : "Документ ещё не загружен.";
  const updateDocument = useUpdateDocument();
  const createDocument = useCreateDocument();
  const renameDocument = useRenameDocument();
  const moveDocument = useMoveDocument();
  const previewRestore = useDocumentRestorePreview();
  const restoreDocument = useRestoreDocument();
  const createFolder = useCreateDocumentFolder();
  const refreshIndex = useDocumentIndexMutation("refresh");

  useEffect(() => {
    if (detail.data) dispatch({ type: "load", detail: detail.data });
  }, [detail.data]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      setRevisionTarget(null);
      setRestoreRevision(null);
      setRestorePreview(null);
      setRestoreError(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [activeId]);

  useEffect(() => {
    if (!pendingHeading || pendingHeading.documentId !== activeId || activeTab?.mode !== "read" || !activeDraft) return;
    const frame = requestAnimationFrame(() => {
      const scope = document.querySelector<HTMLElement>(".documents-route .document-content");
      if (scope && focusMarkdownHeading(scope, pendingHeading.heading)) setPendingHeading(null);
      else {
        setPendingHeading(null);
        setNotice(`Раздел «${pendingHeading.heading}» не найден в открытом документе.`);
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [activeDraft, activeId, activeTab?.mode, pendingHeading]);

  const rootSummaries = useMemo<DocumentRootSummary[]>(() => {
    const map = new Map<string, DocumentRootSummary>();
    for (const page of listing.data?.pages ?? []) for (const root of page.roots ?? []) map.set(root.root_id, root);
    return [...map.values()];
  }, [listing.data?.pages]);
  const listedFolderSummaries = useMemo<DocumentFolderSummary[]>(() => {
    const map = new Map<string, DocumentFolderSummary>();
    for (const page of listing.data?.pages ?? []) for (const folder of page.folders ?? []) map.set(folder.folder_id, folder);
    return [...map.values()];
  }, [listing.data?.pages]);
  const folderSummaries = useMemo<DocumentFolderSummary[]>(() => {
    const map = new Map(listedFolderSummaries.map((folder) => [folder.folder_id, folder]));
    if (expandedFolderProjection.snapshotId === snapshotId) for (const folder of expandedFolderProjection.items) map.set(folder.folder_id, folder);
    return [...map.values()];
  }, [expandedFolderProjection, listedFolderSummaries, snapshotId]);
  const roots = useMemo<DocumentRootOption[]>(() => {
    const map = new Map<string, DocumentRootOption>();
    for (const root of rootSummaries) map.set(root.root_id, { id: root.root_id, label: root.label, writable: root.editable });
    for (const page of listing.data?.pages ?? []) {
      for (const document of page.items) if (!map.has(document.root_id)) map.set(document.root_id, { id: document.root_id, label: document.root_id, writable: document.can_edit });
    }
    return [...map.values()];
  }, [listing.data?.pages, rootSummaries]);
  const kinds = useMemo(() => [...new Set(documents.map((document) => document.kind))].sort(), [documents]);

  const expandFolder = useCallback((expandRootId: string, parentPath: string | null) => {
    if (!snapshotId) return;
    const requestKey = `${snapshotId}:${expandRootId}:${parentPath ?? "<root>"}`;
    if (folderRequests.current.has(requestKey)) return;
    folderRequests.current.add(requestKey);
    void (async () => {
      const collected: DocumentFolderSummary[] = [];
      let cursor: string | null = null;
      let pages = 0;
      do {
        const response: DocumentFoldersEnvelope = await documentGet<DocumentFoldersEnvelope>(`/api/v1/documents/folders${queryString({ root_id: expandRootId, parent_path: parentPath, limit: 500, cursor })}`);
        if (response.snapshot_id !== snapshotId) throw new Error("Folder projection snapshot changed");
        collected.push(...response.items);
        cursor = response.next_cursor;
        pages += 1;
      } while (cursor && pages < 4);
      setExpandedFolderProjection((current) => {
        const map = new Map((current.snapshotId === snapshotId ? current.items : []).map((folder) => [folder.folder_id, folder]));
        for (const folder of collected) map.set(folder.folder_id, folder);
        return { snapshotId, items: [...map.values()] };
      });
      if (cursor) setNotice("В папке больше 2 000 вложенных папок; показана bounded первая часть.");
    })().catch(() => setNotice("Не удалось загрузить вложенные папки для текущего snapshot.")).finally(() => folderRequests.current.delete(requestKey));
  }, [snapshotId]);

  useEffect(() => {
    for (const root of rootSummaries) expandFolder(root.root_id, null);
  }, [expandFolder, rootSummaries]);

  const openDocument = useCallback((document: DocumentSummary) => {
    dispatch({ type: "open", tab: tabFor(document) });
    setPendingHeading(null);
    setRevisionTarget(null);
  }, []);

  const openById = useCallback((documentId: string, heading?: string | null) => {
    const known = documents.find((document) => document.document_id === documentId);
    dispatch({ type: "open", tab: known ? tabFor(known) : placeholderTab(documentId) });
    setPendingHeading(heading ? { documentId, heading } : null);
    setRevisionTarget(null);
  }, [documents]);

  const closeTab = (documentId: string) => {
    const tab = workspace.tabs.find((item) => item.documentId === documentId);
    if (tab?.dirty) {
      setPendingTabClose({ kind: "one", documentId, label: tab.title });
      return;
    }
    dispatch({ type: "close", documentId, force: true });
  };

  const closeOthers = (documentId: string) => {
    const dirtyCount = workspace.tabs.filter((tab) => tab.documentId !== documentId && !tab.pinned && tab.dirty).length;
    if (dirtyCount) {
      setPendingTabClose({ kind: "others", documentId, label: `${dirtyCount} несохранённ${dirtyCount === 1 ? "ый черновик" : "ых черновика"}` });
      return;
    }
    dispatch({ type: "closeOthers", documentId, force: true });
  };

  const changeDraft = (content: string, issues: MarkdownIssue[] = []) => {
    if (!activeId) return;
    dispatch({ type: "draft", documentId: activeId, content, warnings: issues.map((issue) => issue.message) });
    setVisualBlocked((current) => ({ ...current, [activeId]: issues.some((issue) => issue.severity === "error") }));
    setNotice(null);
  };

  const saveContent = (content = activeDraft?.content, expectedSha = activeDraft?.baseSha256, expectedSnapshot = activeDraft?.baseSnapshotId) => {
    if (!activeId || !activeTab || content === undefined || !expectedSha || !expectedSnapshot || activeTab.readOnly || updateDocument.isPending) return;
    if (activeTab.mode === "visual" && visualBlocked[activeId]) {
      setNotice("Визуальное сохранение заблокировано round-trip квалификацией. Переключитесь в Source mode.");
      return;
    }
    const idempotencyKey = newIntentKey("document-save");
    updateDocument.mutate({ payload: { document_id: activeId, content, expected_sha256: expectedSha, expected_snapshot_id: expectedSnapshot, format: "markdown" }, idempotencyKey }, {
      onSuccess: (result) => {
        dispatch({ type: "saved", documentId: activeId, content, sha256: result.document.content_sha256, snapshotId: result.snapshot_id });
        setConflict(null);
        setNotice(result.no_op ? "Изменений для сохранения нет." : "Документ сохранён атомарно.");
      },
      onError: (error) => {
        const typed = error instanceof DocumentApiError ? error.conflict() : null;
        if (typed) setConflict(typed);
        else setNotice(mutationMessage(error));
      }
    });
  };

  const resolveWikilink = (target: WikilinkTarget) => {
    const match = matchingDocumentLink(target, links.data?.items ?? []);
    if (match?.target_document_id) openById(match.target_document_id, target.heading ?? match.heading);
    else if (match?.candidates?.length === 1) openById(match.candidates[0].document_id, target.heading ?? match.heading);
    else setNotice(match?.ambiguous ? "Wikilink неоднозначен — выберите цель в панели «Ссылки»." : "Цель wikilink не найдена в разрешённых roots.");
  };

  const submitAction = (value: DocumentActionValue) => {
    setActionError(null);
    if (!snapshotId) return;
    if (value.kind === "create") {
      createDocument.mutate({ payload: { root_id: value.rootId, folder: value.folder, name: value.name, template: value.template, properties: value.properties, tags: value.tags, expected_snapshot_id: snapshotId } }, {
        onSuccess: (result) => { setAction(null); openDocument(result.document); setNotice("Документ создан."); },
        onError: (error) => setActionError(mutationMessage(error))
      });
    } else if (value.kind === "folder") {
      createFolder.mutate({ payload: { root_id: value.rootId, folder: value.folder, expected_snapshot_id: snapshotId } }, {
        onSuccess: () => { setAction(null); setNotice("Папка создана в разрешённом root."); },
        onError: (error) => setActionError(mutationMessage(error))
      });
    } else if (activeId && activeDraft && detail.data) {
      if (value.kind === "rename") renameDocument.mutate({ payload: { document_id: activeId, name: value.name, expected_sha256: activeDraft.baseSha256, expected_snapshot_id: activeDraft.baseSnapshotId } }, {
        onSuccess: (result) => { dispatch({ type: "title", documentId: activeId, title: result.document.title }); setAction(null); setNotice("Документ переименован."); },
        onError: (error) => setActionError(mutationMessage(error))
      });
      else moveDocument.mutate({ payload: { document_id: activeId, destination_root_id: value.rootId, destination_folder: value.folder, expected_sha256: activeDraft.baseSha256, expected_snapshot_id: activeDraft.baseSnapshotId } }, {
        onSuccess: () => { setAction(null); setNotice("Документ перемещён."); },
        onError: (error) => setActionError(mutationMessage(error))
      });
    }
  };

  const requestRestore = (entry: DocumentHistoryEntry) => {
    if (!activeId || !activeDraft || activeTab?.readOnly) return;
    setRestoreRevision(entry);
    setRestorePreview(null);
    setRestoreError(null);
    setNotice("Проверяем immutable revision и текущий fingerprint…");
    previewRestore.mutate({
      payload: {
        document_id: activeId,
        history_id: entry.history_id,
        expected_sha256: activeDraft.baseSha256,
        expected_snapshot_id: activeDraft.baseSnapshotId
      }
    }, {
      onSuccess: (preview) => {
        if (preview.document_id !== activeId || preview.history_id !== entry.history_id) {
          setRestoreError("Restore preview не связан с выбранным документом или history record.");
          setNotice("Restore preview отклонён из-за несовпадения binding.");
          return;
        }
        setRestorePreview(preview);
        setNotice(null);
      },
      onError: (error) => {
        const message = mutationMessage(error);
        setRestoreError(message);
        setNotice(message);
      }
    });
  };

  const confirmRestore = () => {
    if (!activeId || !activeDraft || !restoreRevision || !restorePreview || typeof restorePreview.restored_content !== "string") return;
    restoreDocument.mutate({
      payload: {
        document_id: activeId,
        history_id: restoreRevision.history_id,
        preview_token: restorePreview.preview_token,
        expected_sha256: restorePreview.current_sha256,
        expected_snapshot_id: restorePreview.snapshot_id,
        confirmed: true
      }
    }, {
      onSuccess: (result) => {
        dispatch({ type: "saved", documentId: activeId, content: restorePreview.restored_content ?? "", sha256: result.document.content_sha256, snapshotId: result.snapshot_id });
        setRestoreRevision(null);
        setRestorePreview(null);
        setRestoreError(null);
        setNotice("Revision восстановлена атомарно; audit event записан. Git commit не создавался.");
      },
      onError: (error) => {
        const typed = error instanceof DocumentApiError ? error.conflict() : null;
        if (typed) setConflict(typed);
        setRestoreError(mutationMessage(error));
      }
    });
  };

  const actionPending = createDocument.isPending || createFolder.isPending || renameDocument.isPending || moveDocument.isPending;
  const closeMobileDrawer = () => dispatch({ type: "drawer", drawer: null });
  const openMobileDrawer = (drawer: "navigation" | "inspector", trigger: HTMLElement) => {
    drawerReturnFocusRef.current = trigger;
    dispatch({ type: "drawer", drawer });
  };
  const trapDrawerFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeMobileDrawer();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])")]
      .filter((element) => element.getClientRects().length > 0);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="route documents-route" data-editor-scope="documents" data-unsaved-changes={workspace.tabs.some((tab) => tab.dirty) ? "true" : "false"} data-editor-location={`${window.location.pathname}${window.location.search}`} onKeyDown={(event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (activeTab?.dirty) saveContent(); }
    }}>
      <div className="documents-commandbar" inert={workspace.mobileDrawer ? true : undefined}>
        <label className="documents-search"><Search size={16} aria-hidden="true" /><input ref={searchInputRef} value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Найти документ" placeholder={'Название, путь, текст, tag: или is:modified'} /><kbd>⌘ F</kbd></label>
        <div className="documents-quick-views" aria-label="Представление документов">{views.slice(1, 4).map((view) => <button type="button" aria-pressed={workspace.view === view.id} key={view.id} onClick={() => dispatch({ type: "view", view: view.id })}>{view.label}</button>)}</div>
        <button type="button" className="documents-index-button" onClick={() => refreshIndex.mutate({ expectedSnapshotId: snapshotId || null })} disabled={refreshIndex.isPending}><RefreshCw className={refreshIndex.isPending ? "spin" : ""} size={15} /><span>{index?.state === "current" ? `${index.file_count} · актуален` : index?.state ?? "индекс"}</span></button>
        <button type="button" className="documents-mobile-panel" onClick={(event) => openMobileDrawer("navigation", event.currentTarget)} aria-controls="documents-navigation-drawer" aria-expanded={workspace.mobileDrawer === "navigation"} aria-label="Открыть файлы"><Menu size={18} /></button>
        <button type="button" className="documents-mobile-panel" onClick={(event) => openMobileDrawer("inspector", event.currentTarget)} aria-controls="documents-inspector-drawer" aria-expanded={workspace.mobileDrawer === "inspector"} aria-label="Открыть свойства" disabled={!detail.data || !activeDraft}><PanelRight size={18} /></button>
      </div>

      <div className="documents-layout">
        <aside ref={navigationDrawerRef} id="documents-navigation-drawer" className={`documents-navigation ${workspace.mobileDrawer === "navigation" ? "drawer-open" : ""}`} aria-label="Навигация по документам" role={workspace.mobileDrawer === "navigation" ? "dialog" : undefined} aria-modal={workspace.mobileDrawer === "navigation" ? true : undefined} onKeyDown={workspace.mobileDrawer === "navigation" ? trapDrawerFocus : undefined}>
          <header><strong>Документы</strong><button type="button" className="documents-drawer-close" onClick={closeMobileDrawer} aria-label="Закрыть файлы"><X size={17} /></button><div><button type="button" onClick={() => setAction("create")} disabled={!roots.some((root) => root.writable)}><FilePlus2 size={15} />Новый</button><button type="button" aria-label="Создать папку" onClick={() => setAction("folder")} disabled={!roots.some((root) => root.writable)}><FolderPlus size={15} /></button></div></header>
          <nav aria-label="Срезы документов">{views.map(({ id, label, icon: Icon }) => <button type="button" className={workspace.view === id ? "active" : ""} key={id} onClick={() => dispatch({ type: "view", view: id })}><Icon size={15} aria-hidden="true" /><span>{label}</span></button>)}</nav>
          <div className="documents-filters"><label><span className="sr-only">Root</span><select value={rootId} onChange={(event) => setRootId(event.target.value)}><option value="">Все roots</option>{roots.map((root) => <option key={root.id} value={root.id}>{root.label}</option>)}</select></label><label><span className="sr-only">Тип</span><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="">Все типы</option>{kinds.map((item) => <option value={item} key={item}>{item}</option>)}</select></label><label><span className="sr-only">Политика</span><select value={policyMode} onChange={(event) => setPolicyMode(event.target.value as DocumentRootMode | "all")}><option value="all">Любой доступ</option><option value="read_write">Редактируемые</option><option value="read_only">Только чтение</option><option value="protected_read_only">Защищённые</option></select></label><label><ArrowDownAZ size={14} /><select aria-label="Сортировка документов" value={sort} onChange={(event) => setSort(event.target.value as DocumentSort)}><option value="modified_desc">Недавно изменённые</option><option value="added_desc">Недавно добавленные</option><option value="name_asc">Имя A–Z</option><option value="name_desc">Имя Z–A</option><option value="size_desc">Размер</option><option value="folder_asc">Папка</option><option value="backlinks_desc">Backlinks</option><option value="links_desc">Исходящие ссылки</option></select></label></div>
          {listing.isLoading ? <LoadingState label="Индексируем разрешённые roots…" /> : listing.isError ? <ErrorState error={listing.error} onRetry={() => void listing.refetch()} /> : <DocumentTree documents={documents} folders={folderSummaries} roots={rootSummaries} selectedDocumentId={activeId} loading={listing.isFetching} onOpen={openDocument} onExpandFolder={(expandRootId, parentPath) => expandFolder(expandRootId, parentPath)} />}
          {listing.hasNextPage ? <button type="button" className="documents-load-more" onClick={() => void listing.fetchNextPage()} disabled={listing.isFetchingNextPage}>{listing.isFetchingNextPage ? "Загружаем…" : "Показать ещё"}</button> : null}
          <footer aria-live="polite"><span>{documents.length} показано</span><span>{index?.last_refresh_at ? `обновлён ${new Date(index.last_refresh_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}` : "ещё не обновлялся"}</span></footer>
        </aside>

        <section className="documents-workspace" id="document-workbench" role="tabpanel" aria-label="Открытый документ" inert={workspace.mobileDrawer ? true : undefined}>
          <DocumentTabs tabs={workspace.tabs} activeDocumentId={activeId} canReopen={workspace.recentlyClosed.length > 0} onActivate={(documentId) => dispatch({ type: "activate", documentId })} onClose={closeTab} onCloseOthers={closeOthers} onPin={(documentId) => dispatch({ type: "pin", documentId })} onReopen={() => dispatch({ type: "reopen" })} />
          {!activeId ? <EmptyState title="Откройте документ" action={<button type="button" className="primary-button" onClick={() => setAction("create")} disabled={!roots.some((root) => root.writable)}>Новый документ</button>}>Выберите файл слева или найдите его по названию, содержимому, тегам и свойствам.</EmptyState> : detail.isLoading && !activeDraft ? <LoadingState label="Открываем активный документ…" /> : detail.isError && !activeDraft ? <ErrorState error={detail.error} onRetry={() => void detail.refetch()} /> : activeTab && activeDraft ? (
            <section className="document-stage" aria-label={activeTab.title}>
              <header className="document-stage-header"><div><span>{detail.data?.document.path ?? activeTab.title}</span><h2>{activeTab.title}</h2><small>{activeTab.readOnly ? <><Shield size={13} /> только чтение</> : activeTab.dirty ? "Есть несохранённые изменения" : "Сохранено"}</small></div><div><button type="button" onClick={() => setFavoriteIds((current) => { const next = new Set(current); if (next.has(activeId)) next.delete(activeId); else if (next.size < 100) next.add(activeId); else setNotice("Можно хранить не более 100 избранных документов в session preferences."); return next; })} aria-label={favoriteIds.has(activeId) ? "Убрать из избранного" : "Добавить в избранное"}><Star size={14} fill={favoriteIds.has(activeId) ? "currentColor" : "none"} /></button><button type="button" onClick={() => setAction("rename")} disabled={activeTab.readOnly}><Pencil size={14} />Переименовать</button><button type="button" onClick={() => setAction("move")} disabled={activeTab.readOnly}><Move size={14} />Переместить</button><button type="button" className="document-save" onClick={() => saveContent()} disabled={activeTab.readOnly || !activeTab.dirty || updateDocument.isPending}><Save size={15} />{updateDocument.isPending ? "Сохраняем…" : "Сохранить"}</button></div></header>
              <div className="document-modebar" role="toolbar" aria-label="Режим документа">{modes.map(({ id, label, icon: Icon }) => <button type="button" aria-pressed={activeTab.mode === id} key={id} disabled={((activeIsImage || activeUnsupported) && id !== "read") || (id === "visual" && Boolean(visualBlockReason))} title={(activeIsImage || activeUnsupported) && id !== "read" ? "Вложения доступны только для чтения" : id === "visual" ? visualBlockReason ?? undefined : undefined} onClick={() => { dispatch({ type: "mode", documentId: activeId, mode: id }); if (id !== "diff") setRevisionTarget(null); }}><Icon size={14} aria-hidden="true" />{label}</button>)}</div>
              {notice ? <div className="documents-notice" role="status">{notice}<button type="button" onClick={() => setNotice(null)} aria-label="Скрыть сообщение"><X size={14} /></button></div> : null}
              <div className="document-content">
                {activeIsImage && detail.data ? <DocumentImageView detail={detail.data} /> : null}
                {activeUnsupported ? <div className="doc-visual-unavailable" role="status"><strong>Для этого формата нет безопасного viewer</strong><p>Файл виден в управляемом workspace, но его содержимое не передано браузеру.</p></div> : null}
                {!activeIsImage && !activeUnsupported && activeTab.mode === "read" ? <SafeMarkdownView content={activeDraft.content} onOpenSource={() => dispatch({ type: "mode", documentId: activeId, mode: "source" })} onOpenWikilink={resolveWikilink} onOpenRelativeLink={(target) => { const match = links.data?.items.find((link) => link.target === target); if (match?.target_document_id) openById(match.target_document_id, match.heading); else setNotice("Относительная ссылка не разрешена или не найдена."); }} resolveImage={(target) => { const asset = detail.data?.assets?.[target]; return typeof asset === "string" ? asset : asset?.url ?? (target.startsWith("/api/v1/documents/") ? target : null); }} /> : null}
                {!activeIsImage && !activeUnsupported && activeTab.mode === "source" ? <Suspense fallback={<LoadingState label="Загружаем Source editor…" />}><SourceEditor key={`${activeId}:${activeDraft.baseSha256}:${detail.data?.line_ending ?? "unknown"}`} value={activeDraft.content} readOnly={activeTab.readOnly} issues={inspectMarkdownForVisualEditing(activeDraft.content)} lineNumbers onChange={(content) => changeDraft(content)} onSave={() => saveContent()} onToggleVisual={() => dispatch({ type: "mode", documentId: activeId, mode: "visual" })} /></Suspense> : null}
                {!activeIsImage && !activeUnsupported && activeTab.mode === "visual" ? visualBlockReason ? <div className="doc-visual-unavailable" role="alert"><strong>Визуальный редактор не открыт</strong><p>{visualBlockReason}</p><button type="button" onClick={() => dispatch({ type: "mode", documentId: activeId, mode: "source" })}>Открыть Source mode</button></div> : <Suspense fallback={<LoadingState label="Загружаем визуальный editor…" />}><VisualEditor key={`${activeId}:${activeDraft.baseSha256}`} value={activeDraft.content} readOnly={activeTab.readOnly} qualification={detail.data?.visual_qualification} onChange={changeDraft} onSave={() => saveContent()} onToggleSource={() => dispatch({ type: "mode", documentId: activeId, mode: "source" })} /></Suspense> : null}
                {!activeIsImage && !activeUnsupported && activeTab.mode === "diff" ? revisionTarget ? revisionDetail.isLoading ? <LoadingState label="Загружаем immutable revision…" /> : revisionDetail.isError ? <ErrorState error={revisionDetail.error} onRetry={() => void revisionDetail.refetch()} /> : revisionDetail.data ? <DocumentDiff original={revisionDetail.data.content} current={activeDraft.content} /> : null : <DocumentDiff original={activeDraft.baseContent} current={activeDraft.content} /> : null}
              </div>
            </section>
          ) : null}
        </section>

        {detail.data && activeDraft ? <div ref={inspectorDrawerRef} id="documents-inspector-drawer" className={`documents-inspector-shell ${workspace.mobileDrawer === "inspector" ? "drawer-open" : ""}`} role={workspace.mobileDrawer === "inspector" ? "dialog" : undefined} aria-modal={workspace.mobileDrawer === "inspector" ? true : undefined} aria-label={workspace.mobileDrawer === "inspector" ? "Сведения о документе" : undefined} onKeyDown={workspace.mobileDrawer === "inspector" ? trapDrawerFocus : undefined}><DocumentInspector detail={detail.data} content={activeDraft.content} section={workspace.inspectorSection} links={links.data} backlinks={backlinks.data} history={history.data} propertyEditingDisabled={activeTab?.mode === "visual"} onSectionChange={(section) => dispatch({ type: "inspector", section })} onContentChange={(content, warning) => { changeDraft(content); if (warning) setNotice(warning); }} onOpenDocument={openById} onPreviewRevision={(entry) => { setRevisionTarget(entry); dispatch({ type: "mode", documentId: activeId!, mode: "diff" }); }} onRequestRestore={requestRestore} onShowInGraph={() => onShowInGraph(activeId!)} onClose={closeMobileDrawer} /></div> : null}
      </div>

      {action ? <DocumentActionDialog kind={action} roots={roots} initialName={action === "rename" ? detail.data?.document.filename : ""} initialRootId={detail.data?.document.root_id} initialFolder={action === "move" ? folderWithinRoot(detail.data?.document.path ?? "", rootSummaries.find((root) => root.root_id === detail.data?.document.root_id)?.path ?? "") : ""} pending={actionPending} error={actionError} onCancel={() => { setAction(null); setActionError(null); }} onSubmit={submitAction} /> : null}
      {conflict && activeDraft ? <DocumentConflictDialog key={`${conflict.document_id}:${conflict.current_sha256}:${conflict.proposed_sha256}`} conflict={conflict} baseContent={activeDraft.baseContent} draftContent={activeDraft.content} onCancel={() => setConflict(null)} onResolve={(content, sha256, currentSnapshot) => saveContent(content, sha256, currentSnapshot)} /> : null}
      {restoreRevision && restorePreview && activeDraft ? <DocumentRestoreDialog revision={restoreRevision} preview={restorePreview} fallbackCurrentContent={activeDraft.baseContent} pending={restoreDocument.isPending} error={restoreError} onCancel={() => { setRestoreRevision(null); setRestorePreview(null); setRestoreError(null); }} onConfirm={confirmRestore} /> : null}
      {pendingTabClose ? (
        <Dialog className="small-modal panel" role="alertdialog" labelledBy="close-document-tabs-title" describedBy="close-document-tabs-description" closeOnBackdrop={false} initialFocus="cancel" onClose={() => setPendingTabClose(null)}>
          <header><div><span className="eyebrow">Session draft</span><h2 id="close-document-tabs-title">Закрыть без сохранения?</h2></div></header>
          <p id="close-document-tabs-description">{pendingTabClose.kind === "one" ? `Черновик «${pendingTabClose.label}» будет удалён из текущей сессии.` : `${pendingTabClose.label} будут удалены из текущей сессии.`} Файлы на диске не изменятся.</p>
          <footer><button type="button" data-dialog-cancel onClick={() => setPendingTabClose(null)}>Оставить вкладки открытыми</button><button className="danger-button" type="button" onClick={() => { if (pendingTabClose.kind === "one") dispatch({ type: "close", documentId: pendingTabClose.documentId, force: true }); else dispatch({ type: "closeOthers", documentId: pendingTabClose.documentId, force: true }); setPendingTabClose(null); }}>Закрыть без сохранения</button></footer>
        </Dialog>
      ) : null}
      {workspace.mobileDrawer ? <button className="documents-drawer-scrim" type="button" onClick={closeMobileDrawer} aria-label="Закрыть боковую панель" tabIndex={-1} /> : null}
    </div>
  );
}

export default Documents;
