import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ensureSession } from "../../api";
import type {
  DocumentBacklinksEnvelope,
  DocumentConflictDetails,
  DocumentCreateInput,
  DocumentDetailEnvelope,
  DocumentFolderInput,
  DocumentHistoryEnvelope,
  DocumentLinksEnvelope,
  DocumentListEnvelope,
  DocumentMoveInput,
  DocumentQuery,
  DocumentRenameInput,
  DocumentRestoreInput,
  DocumentRestorePreviewEnvelope,
  DocumentRestorePreviewInput,
  DocumentRevisionDetailEnvelope,
  DocumentUpdateInput,
  DocumentWriteResult
} from "./documentTypes";

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
}

export class DocumentApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "DocumentApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  conflict(): DocumentConflictDetails | null {
    if (this.code !== "document_conflict" || !this.details || typeof this.details !== "object") return null;
    const value = this.details as Partial<DocumentConflictDetails>;
    return typeof value.document_id === "string" && typeof value.current_sha256 === "string" && typeof value.snapshot_id === "string"
      ? value as DocumentConflictDetails
      : null;
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({})) as T & ErrorEnvelope;
  if (!response.ok) {
    throw new DocumentApiError(
      response.status,
      payload.error?.code ?? "document_request_failed",
      payload.error?.message ?? "Документный workspace не ответил на запрос.",
      payload.error?.details
    );
  }
  return payload;
}

async function documentGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  await ensureSession();
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal
  });
  return parseResponse<T>(response);
}

async function documentPost<T>(path: string, body: unknown, idempotencyKey: string): Promise<T> {
  const csrf = await ensureSession();
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
      "X-CSRF-Token": csrf
    },
    body: JSON.stringify(body)
  });
  return parseResponse<T>(response);
}

function queryString(values: Record<string, string | number | boolean | readonly string[] | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) for (const item of value as readonly string[]) query.append(key, item);
    else query.set(key, String(value));
  }
  const rendered = query.toString();
  return rendered ? `?${rendered}` : "";
}

export function listingPath(query: DocumentQuery, cursor: string | null): string {
  const stateToken = query.view === "added" ? "is:new" : query.view === "modified" ? "is:modified" : "";
  const hasFacetFilters = Boolean(query.rootId || query.folder || query.kind || query.tag || (query.mode && query.mode !== "all"));
  const favoriteLookup = Boolean(query.documentIds?.length);
  const useSearch = !favoriteLookup && Boolean(query.query.trim() || query.view === "modified" || (stateToken && hasFacetFilters));
  const endpoint = useSearch
    ? "/api/v1/documents/search"
    : ["recent", "added", "modified"].includes(query.view)
      ? "/api/v1/documents/recent"
      : "/api/v1/documents";
  const recentKind = query.view === "added" ? "added" : query.view === "modified" ? "modified" : query.view === "recent" ? "recent" : undefined;
  const effectiveQuery = favoriteLookup ? "" : [query.query.trim(), useSearch ? stateToken : ""].filter(Boolean).join(" ");
  return endpoint + queryString({
    q: effectiveQuery || undefined,
    document_ids: query.documentIds,
    view: query.view,
    root_id: query.rootId,
    folder: query.folder,
    kind: endpoint === "/api/v1/documents/recent" ? recentKind : query.kind,
    tag: query.tag,
    mode: query.mode === "all" ? undefined : query.mode,
    sort: query.sort,
    cursor,
    limit: Math.min(query.limit ?? 50, 50)
  });
}

export function retryDocumentListing(failureCount: number, error: unknown): boolean {
  if (error instanceof DocumentApiError && error.code === "document_index_initializing") return failureCount < 60;
  return failureCount < 1;
}

export function useDocumentListing(query: DocumentQuery) {
  return useInfiniteQuery({
    queryKey: ["documents", "listing", query],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => documentGet<DocumentListEnvelope>(listingPath(query, pageParam), signal),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    staleTime: 2_000,
    retry: retryDocumentListing,
    retryDelay: (attempt, error) => error instanceof DocumentApiError && error.code === "document_index_initializing" ? 1_000 : Math.min(250 * 2 ** attempt, 2_000),
    refetchOnWindowFocus: true
  });
}

export function useDocumentDetail(documentId: string | null, expectedSnapshotId?: string | null) {
  return useQuery({
    queryKey: ["documents", "detail", documentId, expectedSnapshotId],
    queryFn: ({ signal }) => documentGet<DocumentDetailEnvelope>(`/api/v1/documents/${encodeURIComponent(documentId ?? "")}${queryString({ expected_snapshot_id: expectedSnapshotId })}`, signal),
    enabled: Boolean(documentId && expectedSnapshotId),
    staleTime: 5_000,
    retry: 1,
    refetchOnWindowFocus: false
  });
}

export function useDocumentLinks(documentId: string | null, expectedSnapshotId?: string | null) {
  return useQuery({
    queryKey: ["documents", "links", documentId, expectedSnapshotId],
    queryFn: ({ signal }) => documentGet<DocumentLinksEnvelope>(`/api/v1/documents/${encodeURIComponent(documentId ?? "")}/links${queryString({ expected_snapshot_id: expectedSnapshotId })}`, signal),
    enabled: Boolean(documentId && expectedSnapshotId),
    staleTime: 3_000
  });
}

export function useDocumentBacklinks(documentId: string | null, expectedSnapshotId?: string | null) {
  return useQuery({
    queryKey: ["documents", "backlinks", documentId, expectedSnapshotId],
    queryFn: ({ signal }) => documentGet<DocumentBacklinksEnvelope>(`/api/v1/documents/${encodeURIComponent(documentId ?? "")}/backlinks${queryString({ expected_snapshot_id: expectedSnapshotId })}`, signal),
    enabled: Boolean(documentId && expectedSnapshotId),
    staleTime: 3_000
  });
}

export function useDocumentHistory(documentId: string | null, expectedSnapshotId?: string | null) {
  return useQuery({
    queryKey: ["documents", "history", documentId, expectedSnapshotId],
    queryFn: ({ signal }) => documentGet<DocumentHistoryEnvelope>(`/api/v1/documents/${encodeURIComponent(documentId ?? "")}/history${queryString({ expected_snapshot_id: expectedSnapshotId })}`, signal),
    enabled: Boolean(documentId && expectedSnapshotId),
    staleTime: 3_000
  });
}

export function useDocumentRevisionDetail(documentId: string | null, revisionId: string | null, expectedSnapshotId?: string | null) {
  return useQuery({
    queryKey: ["documents", "history-detail", documentId, revisionId, expectedSnapshotId],
    queryFn: ({ signal }) => documentGet<DocumentRevisionDetailEnvelope>(`/api/v1/documents/${encodeURIComponent(documentId ?? "")}/history/${encodeURIComponent(revisionId ?? "")}${queryString({ expected_snapshot_id: expectedSnapshotId })}`, signal),
    enabled: Boolean(documentId && revisionId && expectedSnapshotId),
    staleTime: 30_000,
    retry: 1
  });
}

function newIntentKey(prefix: string): string {
  const suffix = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${suffix}`;
}

interface WriteIntent<T> {
  payload: T;
  idempotencyKey?: string;
}

function useDocumentWrite<TPayload>(
  operation: (payload: TPayload) => string,
  body: (payload: TPayload) => unknown,
  prefix: string
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: WriteIntent<TPayload>) => documentPost<DocumentWriteResult>(operation(payload), body(payload), idempotencyKey ?? newIntentKey(prefix)),
    onSuccess: async (result) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["documents", "listing"] }),
        // A successful write publishes a new global index snapshot. Revalidate every
        // open/cached detail so clean drafts can adopt it and dirty drafts whose base
        // hash is unchanged can safely rebase before their next write.
        client.invalidateQueries({ queryKey: ["documents", "detail"] }),
        client.invalidateQueries({ queryKey: ["documents", "links"] }),
        client.invalidateQueries({ queryKey: ["documents", "backlinks"] }),
        client.invalidateQueries({ queryKey: ["documents", "history", result.document.document_id] }),
        client.invalidateQueries({ queryKey: ["universe"] })
      ]);
    }
  });
}

export const useCreateDocument = () => useDocumentWrite<DocumentCreateInput>(() => "/api/v1/documents", (payload) => payload, "document-create");
export const useUpdateDocument = () => useDocumentWrite<DocumentUpdateInput>((payload) => `/api/v1/documents/${encodeURIComponent(payload.document_id)}/update`, (payload) => ({ content: payload.content, expected_sha256: payload.expected_sha256, expected_snapshot_id: payload.expected_snapshot_id, format: payload.format }), "document-update");
export const useRenameDocument = () => useDocumentWrite<DocumentRenameInput>((payload) => `/api/v1/documents/${encodeURIComponent(payload.document_id)}/rename`, (payload) => ({ name: payload.name, expected_sha256: payload.expected_sha256, expected_snapshot_id: payload.expected_snapshot_id }), "document-rename");
export const useMoveDocument = () => useDocumentWrite<DocumentMoveInput>((payload) => `/api/v1/documents/${encodeURIComponent(payload.document_id)}/move`, (payload) => ({ destination_root_id: payload.destination_root_id, destination_folder: payload.destination_folder, expected_sha256: payload.expected_sha256, expected_snapshot_id: payload.expected_snapshot_id }), "document-move");
export const useRestoreDocument = () => useDocumentWrite<DocumentRestoreInput>((payload) => `/api/v1/documents/${encodeURIComponent(payload.document_id)}/restore`, (payload) => ({ history_id: payload.history_id, preview_token: payload.preview_token, expected_sha256: payload.expected_sha256, expected_snapshot_id: payload.expected_snapshot_id, confirmed: payload.confirmed }), "document-restore");

export function useDocumentRestorePreview() {
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: WriteIntent<DocumentRestorePreviewInput>) => documentPost<DocumentRestorePreviewEnvelope>(
      `/api/v1/documents/${encodeURIComponent(payload.document_id)}/restore-preview`,
      { history_id: payload.history_id, expected_sha256: payload.expected_sha256, expected_snapshot_id: payload.expected_snapshot_id },
      idempotencyKey ?? newIntentKey("document-restore-preview")
    )
  });
}

export function useCreateDocumentFolder() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: WriteIntent<DocumentFolderInput>) => documentPost<{ snapshot_id: string; folder: string; no_op: boolean }>("/api/v1/documents/folders", payload, idempotencyKey ?? newIntentKey("document-folder")),
    onSuccess: () => client.invalidateQueries({ queryKey: ["documents", "listing"] })
  });
}

export function useDocumentIndexMutation(operation: "refresh" | "rebuild") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ expectedSnapshotId, documentId, idempotencyKey }: { expectedSnapshotId: string | null; documentId?: string; idempotencyKey?: string }) => documentPost<DocumentIndexStatusEnvelope>(
      `/api/v1/documents/index/${operation}`,
      { expected_snapshot_id: expectedSnapshotId, document_id: documentId },
      idempotencyKey ?? newIntentKey(`document-index-${operation}`)
    ),
    onSuccess: async () => client.invalidateQueries({ queryKey: ["documents"] })
  });
}

interface DocumentIndexStatusEnvelope {
  snapshot_id: string;
  index: DocumentListEnvelope["index"];
}

export { documentGet, documentPost, newIntentKey, queryString };
