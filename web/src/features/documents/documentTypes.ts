export type DocumentRootMode = "read_write" | "read_only" | "protected_read_only" | "hidden";
export type DocumentSensitivity = "public" | "internal" | "personal" | "confidential" | "restricted" | "secret";
export type DocumentView = "files" | "recent" | "added" | "modified" | "favorites";
export type DocumentMode = "read" | "visual" | "source" | "diff";
export type DocumentSort = "modified_desc" | "added_desc" | "name_asc" | "name_desc" | "size_desc" | "folder_asc" | "backlinks_desc" | "links_desc";
export type DocumentIndexState = "current" | "refreshing" | "stale" | "error" | "missing";

export interface DocumentIndexStatus {
  state: DocumentIndexState;
  file_count: number;
  last_refresh_at: string | null;
  snapshot_id?: string;
  message?: string | null;
}

export interface DocumentRootSummary {
  root_id: string;
  label: string;
  path: string;
  mode: DocumentRootMode;
  kind: string;
  editable: boolean;
}

export interface DocumentFolderSummary {
  folder_id: string;
  root_id: string;
  path: string;
  name: string;
  parent_path: string | null;
  document_count: number;
  descendant_count: number;
  mode: DocumentRootMode;
  can_create: boolean;
}

export interface DocumentSummary {
  document_id: string;
  root_id: string;
  path: string;
  filename: string;
  extension: string;
  title: string;
  kind: string;
  mode: DocumentRootMode;
  sensitivity: DocumentSensitivity;
  size_bytes: number;
  content_sha256: string;
  modified_at: string;
  first_seen_at: string;
  modified_source?: string | null;
  added_source?: string | null;
  git_status?: string | null;
  is_modified: boolean;
  is_new: boolean;
  is_favorite?: boolean;
  tags: string[];
  aliases: string[];
  headings: string[];
  outgoing_link_count: number;
  backlink_count: number;
  can_edit: boolean;
}

export interface DocumentListEnvelope {
  snapshot_id: string;
  index: DocumentIndexStatus;
  roots?: DocumentRootSummary[];
  folders?: DocumentFolderSummary[];
  items: DocumentSummary[];
  next_cursor: string | null;
}

export interface DocumentFoldersEnvelope {
  snapshot_id: string;
  items: DocumentFolderSummary[];
  next_cursor: string | null;
}

export interface DocumentQuery {
  view: DocumentView;
  query: string;
  documentIds?: string[];
  rootId?: string;
  folder?: string;
  kind?: string;
  tag?: string;
  mode?: DocumentRootMode | "all";
  sort: DocumentSort;
  cursor?: string | null;
  limit?: number;
}

export interface FrontmatterField {
  key: string;
  value: string | number | boolean | string[] | Record<string, unknown> | null;
  type: "string" | "number" | "boolean" | "date" | "list" | "tags" | "aliases" | "complex";
  editable: boolean;
  source?: string;
}

export interface VisualQualification {
  can_open: boolean;
  can_save: boolean;
  round_trip_safe: boolean;
  warnings: string[];
  unsupported_syntax: string[];
}

export interface DocumentDetailEnvelope {
  snapshot_id: string;
  document: DocumentSummary;
  content: string | null;
  format: "markdown" | "text" | "image" | "unsupported";
  content_sha256: string;
  line_ending: "lf" | "crlf" | "mixed" | null;
  final_newline: boolean | null;
  warnings: string[];
  asset_url?: string;
  mime_type?: string;
  image?: { mime_type: string; width?: number; height?: number } | null;
  assets?: Record<string, string | { asset_id: string; url: string; mime_type?: string; size_bytes?: number }>;
  frontmatter?: FrontmatterField[];
  visual_qualification?: VisualQualification;
}

export interface DocumentLink {
  target: string;
  target_document_id: string | null;
  label: string;
  heading: string | null;
  line: number | null;
  context: string;
  ambiguous?: boolean;
  candidates?: Array<{ document_id: string; title: string; path: string }>;
}

export interface DocumentBacklink {
  source_document_id: string;
  source_title: string;
  source_path: string;
  line: number | null;
  context: string;
}

export interface DocumentLinksEnvelope {
  snapshot_id: string;
  document_id: string;
  items: DocumentLink[];
  next_cursor: string | null;
}

export interface DocumentBacklinksEnvelope {
  snapshot_id: string;
  document_id: string;
  items: DocumentBacklink[];
  next_cursor: string | null;
}

export interface DocumentHistoryEntry {
  history_id: string;
  source: "git" | "raytsystem" | "raytsystem_revision" | "unsaved";
  recorded_at: string;
  content_sha256: string | null;
  author: string | null;
  summary: string | null;
  content?: string;
}

export interface DocumentHistoryEnvelope {
  snapshot_id: string;
  document_id: string;
  items: DocumentHistoryEntry[];
  next_cursor: string | null;
}

export interface DocumentRevisionDetailEnvelope {
  snapshot_id: string;
  document_id: string;
  revision_id: string;
  content: string;
  content_sha256: string;
  current_sha256: string;
  diff?: string;
  diff_truncated?: boolean;
}

export interface DocumentRestorePreviewInput {
  document_id: string;
  history_id: string;
  expected_sha256: string;
  expected_snapshot_id: string;
}

export interface DocumentRestorePreviewEnvelope {
  preview_token: string;
  snapshot_id: string;
  document_id: string;
  history_id: string;
  current_sha256: string;
  restored_sha256: string;
  current_content?: string | null;
  restored_content: string | null;
  expires_at?: string | null;
}

export interface DocumentRestoreInput extends DocumentRestorePreviewInput {
  preview_token: string;
  confirmed: true;
}

export interface DocumentConflictDetails {
  document_id: string;
  expected_sha256: string;
  current_sha256: string;
  proposed_sha256?: string | null;
  snapshot_id: string;
  current_content?: string | null;
}

export interface DocumentWriteResult {
  snapshot_id: string;
  document: DocumentSummary;
  no_op: boolean;
  audit_event_id: string;
  revision_id: string | null;
}

export interface DocumentCreateInput {
  root_id: string;
  folder: string;
  name: string;
  template: string;
  properties: Record<string, string | number | boolean | string[]>;
  tags: string[];
  expected_snapshot_id: string;
}

export interface DocumentUpdateInput {
  document_id: string;
  content: string;
  expected_sha256: string;
  expected_snapshot_id: string;
  format: "markdown";
}

export interface DocumentRenameInput {
  document_id: string;
  name: string;
  expected_sha256: string;
  expected_snapshot_id: string;
}

export interface DocumentMoveInput {
  document_id: string;
  destination_root_id: string;
  destination_folder: string;
  expected_sha256: string;
  expected_snapshot_id: string;
}

export interface DocumentFolderInput {
  root_id: string;
  folder: string;
  expected_snapshot_id: string;
}

export interface DocumentTabState {
  documentId: string;
  title: string;
  mode: DocumentMode;
  pinned: boolean;
  dirty: boolean;
  readOnly: boolean;
}

export interface DocumentDraft {
  documentId: string;
  content: string;
  baseContent: string;
  baseSha256: string;
  baseSnapshotId: string;
  dirty: boolean;
  persistable: boolean;
  warnings: string[];
}

export interface DocumentWorkspaceState {
  tabs: DocumentTabState[];
  activeDocumentId: string | null;
  recentlyClosed: DocumentTabState[];
  drafts: Record<string, DocumentDraft>;
  view: DocumentView;
  inspectorSection: "properties" | "links" | "backlinks" | "history";
  mobileDrawer: "navigation" | "inspector" | null;
}
