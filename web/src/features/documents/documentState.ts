import type {
  DocumentDetailEnvelope,
  DocumentDraft,
  DocumentMode,
  DocumentTabState,
  DocumentView,
  DocumentWorkspaceState
} from "./documentTypes";

const SESSION_KEY = "raytsystem.documents.workspace.v1";
const MAX_RECENTLY_CLOSED = 12;
const MAX_PERSISTED_DRAFT_BYTES = 512 * 1024;

type WorkspaceAction =
  | { type: "hydrate"; state: DocumentWorkspaceState }
  | { type: "open"; tab: DocumentTabState }
  | { type: "activate"; documentId: string }
  | { type: "close"; documentId: string; force?: boolean }
  | { type: "closeOthers"; documentId: string; force?: boolean }
  | { type: "reopen" }
  | { type: "pin"; documentId: string; pinned?: boolean }
  | { type: "mode"; documentId: string; mode: DocumentMode }
  | { type: "load"; detail: DocumentDetailEnvelope }
  | { type: "draft"; documentId: string; content: string; warnings?: string[] }
  | { type: "saved"; documentId: string; content: string; sha256: string; snapshotId: string }
  | { type: "title"; documentId: string; title: string }
  | { type: "view"; view: DocumentView }
  | { type: "inspector"; section: DocumentWorkspaceState["inspectorSection"] }
  | { type: "drawer"; drawer: DocumentWorkspaceState["mobileDrawer"] };

export function initialDocumentWorkspaceState(): DocumentWorkspaceState {
  return {
    tabs: [],
    activeDocumentId: null,
    recentlyClosed: [],
    drafts: {},
    view: "files",
    inspectorSection: "properties",
    mobileDrawer: null
  };
}

function updateTab(
  tabs: DocumentTabState[],
  documentId: string,
  update: (tab: DocumentTabState) => DocumentTabState
): DocumentTabState[] {
  return tabs.map((tab) => tab.documentId === documentId ? update(tab) : tab);
}

function nextActiveAfterClose(tabs: DocumentTabState[], index: number): string | null {
  return tabs[index + 1]?.documentId ?? tabs[index - 1]?.documentId ?? null;
}

export function documentWorkspaceReducer(
  state: DocumentWorkspaceState,
  action: WorkspaceAction
): DocumentWorkspaceState {
  switch (action.type) {
    case "hydrate":
      return action.state;
    case "open": {
      const existing = state.tabs.find((tab) => tab.documentId === action.tab.documentId);
      return {
        ...state,
        tabs: existing ? updateTab(state.tabs, existing.documentId, (tab) => ({ ...tab, title: action.tab.title || tab.title, readOnly: action.tab.readOnly })) : [...state.tabs, action.tab],
        activeDocumentId: action.tab.documentId,
        mobileDrawer: null
      };
    }
    case "activate":
      return state.tabs.some((tab) => tab.documentId === action.documentId)
        ? { ...state, activeDocumentId: action.documentId, mobileDrawer: null }
        : state;
    case "close": {
      const index = state.tabs.findIndex((tab) => tab.documentId === action.documentId);
      if (index < 0) return state;
      const closing = state.tabs[index];
      if (closing.dirty && !action.force) return state;
      const tabs = state.tabs.filter((tab) => tab.documentId !== action.documentId);
      const drafts = { ...state.drafts };
      delete drafts[action.documentId];
      return {
        ...state,
        tabs,
        drafts,
        activeDocumentId: state.activeDocumentId === action.documentId
          ? nextActiveAfterClose(state.tabs, index)
          : state.activeDocumentId,
        recentlyClosed: [closing, ...state.recentlyClosed.filter((tab) => tab.documentId !== closing.documentId)].slice(0, MAX_RECENTLY_CLOSED)
      };
    }
    case "closeOthers": {
      const keep = state.tabs.find((tab) => tab.documentId === action.documentId);
      if (!keep) return state;
      const closing = state.tabs.filter((tab) => tab.documentId !== action.documentId && !tab.pinned);
      if (!action.force && closing.some((tab) => tab.dirty)) return state;
      const retained = state.tabs.filter((tab) => tab.documentId === action.documentId || tab.pinned);
      const drafts = { ...state.drafts };
      for (const tab of closing) delete drafts[tab.documentId];
      return {
        ...state,
        tabs: retained,
        drafts,
        activeDocumentId: action.documentId,
        recentlyClosed: [...closing.reverse(), ...state.recentlyClosed].slice(0, MAX_RECENTLY_CLOSED)
      };
    }
    case "reopen": {
      const [tab, ...rest] = state.recentlyClosed;
      if (!tab) return state;
      return {
        ...state,
        tabs: state.tabs.some((item) => item.documentId === tab.documentId) ? state.tabs : [...state.tabs, { ...tab, dirty: false }],
        activeDocumentId: tab.documentId,
        recentlyClosed: rest
      };
    }
    case "pin":
      return { ...state, tabs: updateTab(state.tabs, action.documentId, (tab) => ({ ...tab, pinned: action.pinned ?? !tab.pinned })) };
    case "mode":
      return { ...state, tabs: updateTab(state.tabs, action.documentId, (tab) => ({ ...tab, mode: action.mode })) };
    case "load": {
      const { document, content: detailContent, content_sha256, snapshot_id, warnings } = action.detail;
      const content = typeof detailContent === "string" ? detailContent : "";
      const persistable = !["confidential", "restricted", "secret"].includes(document.sensitivity.toLowerCase());
      const restored = state.drafts[document.document_id];
      if (restored?.dirty) {
        const unchangedBase = restored.baseSha256 === content_sha256;
        const driftWarning = restored.baseSha256 !== content_sha256
          ? ["Файл изменился после сохранения session draft. Сохранение потребует ручного merge."]
          : [];
        return {
          ...state,
          drafts: {
            ...state.drafts,
            [document.document_id]: {
              ...restored,
              // Snapshot IDs are global projections. Rebasing is safe only when the
              // file fingerprint still matches the draft's base.
              baseSnapshotId: unchangedBase ? snapshot_id : restored.baseSnapshotId,
              persistable,
              warnings: [...new Set([...restored.warnings, ...warnings, ...driftWarning])]
            }
          },
          tabs: updateTab(state.tabs, document.document_id, (tab) => ({
            ...tab,
            title: document.title || document.filename,
            mode: action.detail.format === "markdown" ? tab.mode : "read",
            dirty: restored.dirty,
            readOnly: !document.can_edit || action.detail.format !== "markdown"
          }))
        };
      }
      const draft: DocumentDraft = {
        documentId: document.document_id,
        content,
        baseContent: content,
        baseSha256: content_sha256,
        baseSnapshotId: snapshot_id,
        dirty: false,
        persistable,
        warnings
      };
      return {
        ...state,
        drafts: { ...state.drafts, [document.document_id]: draft },
        tabs: updateTab(state.tabs, document.document_id, (tab) => ({
          ...tab,
          title: document.title || document.filename,
          mode: action.detail.format === "markdown" ? tab.mode : "read",
          dirty: false,
          readOnly: !document.can_edit || action.detail.format !== "markdown"
        }))
      };
    }
    case "draft": {
      const current = state.drafts[action.documentId];
      if (!current) return state;
      const dirty = action.content !== current.baseContent;
      return {
        ...state,
        drafts: {
          ...state.drafts,
          [action.documentId]: { ...current, content: action.content, dirty, warnings: action.warnings ?? current.warnings }
        },
        tabs: updateTab(state.tabs, action.documentId, (tab) => ({ ...tab, dirty }))
      };
    }
    case "saved": {
      const current = state.drafts[action.documentId];
      if (!current) return state;
      return {
        ...state,
        drafts: {
          ...state.drafts,
          [action.documentId]: {
            ...current,
            content: action.content,
            baseContent: action.content,
            baseSha256: action.sha256,
            baseSnapshotId: action.snapshotId,
            dirty: false,
            warnings: []
          }
        },
        tabs: updateTab(state.tabs, action.documentId, (tab) => ({ ...tab, dirty: false }))
      };
    }
    case "title":
      return { ...state, tabs: updateTab(state.tabs, action.documentId, (tab) => ({ ...tab, title: action.title })) };
    case "view":
      return { ...state, view: action.view, mobileDrawer: null };
    case "inspector":
      return { ...state, inspectorSection: action.section };
    case "drawer":
      return { ...state, mobileDrawer: action.drawer };
  }
}

interface PersistedWorkspace {
  version: 1;
  tabs: Array<Pick<DocumentTabState, "documentId" | "mode" | "pinned">>;
  activeDocumentId: string | null;
  recentlyClosed: Array<Pick<DocumentTabState, "documentId" | "mode" | "pinned">>;
  drafts: Array<Pick<DocumentDraft, "documentId" | "content" | "baseContent" | "baseSha256" | "baseSnapshotId" | "warnings">>;
  view: DocumentView;
}

function safeSessionStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function persistDocumentWorkspace(state: DocumentWorkspaceState): void {
  const storage = safeSessionStorage();
  if (!storage) return;
  let draftBytes = 0;
  const drafts: PersistedWorkspace["drafts"] = [];
  for (const draft of Object.values(state.drafts)) {
    if (!draft.persistable || !draft.dirty) continue;
    const bytes = new TextEncoder().encode(draft.content).byteLength;
    if (bytes > MAX_PERSISTED_DRAFT_BYTES || draftBytes + bytes > MAX_PERSISTED_DRAFT_BYTES) continue;
    draftBytes += bytes;
    drafts.push({
      documentId: draft.documentId,
      content: draft.content,
      baseContent: draft.baseContent,
      baseSha256: draft.baseSha256,
      baseSnapshotId: draft.baseSnapshotId,
      warnings: draft.warnings
    });
  }
  const payload: PersistedWorkspace = {
    version: 1,
    tabs: state.tabs.map(({ documentId, mode, pinned }) => ({ documentId, mode, pinned })),
    activeDocumentId: state.activeDocumentId,
    recentlyClosed: state.recentlyClosed.map(({ documentId, mode, pinned }) => ({ documentId, mode, pinned })),
    drafts,
    view: state.view
  };
  try {
    storage.setItem(SESSION_KEY, JSON.stringify(payload));
  } catch {
    // Session storage is a best-effort crash draft, never a source of truth.
  }
}

function restoredTab(tab: PersistedWorkspace["tabs"][number]): DocumentTabState {
  return {
    documentId: tab.documentId,
    title: "Документ",
    mode: tab.mode,
    pinned: tab.pinned,
    dirty: false,
    readOnly: true
  };
}

export function restoreDocumentWorkspace(): DocumentWorkspaceState {
  const fallback = initialDocumentWorkspaceState();
  const storage = safeSessionStorage();
  if (!storage) return fallback;
  try {
    const parsed = JSON.parse(storage.getItem(SESSION_KEY) ?? "null") as PersistedWorkspace | null;
    if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.tabs)) return fallback;
    const tabs = parsed.tabs.filter((tab) => typeof tab.documentId === "string").map(restoredTab);
    const drafts: Record<string, DocumentDraft> = {};
    for (const draft of Array.isArray(parsed.drafts) ? parsed.drafts : []) {
      if (!tabs.some((tab) => tab.documentId === draft.documentId)) continue;
      drafts[draft.documentId] = { ...draft, dirty: draft.content !== draft.baseContent, persistable: true };
    }
    return {
      ...fallback,
      tabs: tabs.map((tab) => ({ ...tab, dirty: Boolean(drafts[tab.documentId]?.dirty) })),
      activeDocumentId: tabs.some((tab) => tab.documentId === parsed.activeDocumentId) ? parsed.activeDocumentId : tabs[0]?.documentId ?? null,
      recentlyClosed: (parsed.recentlyClosed ?? []).filter((tab) => typeof tab.documentId === "string").map(restoredTab).slice(0, MAX_RECENTLY_CLOSED),
      drafts,
      view: parsed.view ?? "files"
    };
  } catch {
    return fallback;
  }
}

export type { WorkspaceAction };
