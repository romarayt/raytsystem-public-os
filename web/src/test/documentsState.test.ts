import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  documentWorkspaceReducer,
  initialDocumentWorkspaceState,
  persistDocumentWorkspace
} from "../features/documents/documentState";
import type { DocumentDetailEnvelope, DocumentSummary, DocumentWorkspaceState } from "../features/documents/documentTypes";

function summary(documentId = "doc_a", overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    document_id: documentId,
    root_id: "manual",
    path: `knowledge/manual/${documentId}.md`,
    filename: `${documentId}.md`,
    extension: ".md",
    title: documentId,
    kind: "notes",
    mode: "read_write",
    sensitivity: "internal",
    size_bytes: 12,
    content_sha256: "sha-one",
    modified_at: "2026-07-12T10:00:00Z",
    first_seen_at: "2026-07-12T09:00:00Z",
    is_modified: false,
    is_new: false,
    tags: [],
    aliases: [],
    headings: [],
    outgoing_link_count: 0,
    backlink_count: 0,
    can_edit: true,
    ...overrides
  };
}

function detail(documentId: string, content: string, sha: string, snapshot: string, overrides: Partial<DocumentSummary> = {}): DocumentDetailEnvelope {
  return {
    snapshot_id: snapshot,
    document: summary(documentId, { content_sha256: sha, ...overrides }),
    content,
    format: "markdown",
    content_sha256: sha,
    line_ending: "lf",
    final_newline: content.endsWith("\n"),
    warnings: []
  };
}

function openAndLoad(documentId: string, payload: DocumentDetailEnvelope, state = initialDocumentWorkspaceState()): DocumentWorkspaceState {
  const opened = documentWorkspaceReducer(state, {
    type: "open",
    tab: { documentId, title: documentId, mode: "read", pinned: false, dirty: false, readOnly: false }
  });
  return documentWorkspaceReducer(opened, { type: "load", detail: payload });
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("document workspace draft integrity", () => {
  it("replaces a clean draft when the disk detail changes", () => {
    let state = openAndLoad("doc_a", detail("doc_a", "old\n", "sha-old", "snap-1"));
    state = documentWorkspaceReducer(state, { type: "load", detail: detail("doc_a", "external\n", "sha-new", "snap-2") });

    expect(state.drafts.doc_a).toMatchObject({
      content: "external\n",
      baseContent: "external\n",
      baseSha256: "sha-new",
      baseSnapshotId: "snap-2",
      dirty: false
    });
  });

  it("normalizes a binary image detail to a read-only empty draft", () => {
    const image = detail("doc_image", "", "sha-image", "snap-1", { extension: ".png", can_edit: false });
    image.format = "image";
    image.content = null;
    image.asset_url = "/api/v1/documents/assets/image-1";
    const state = openAndLoad("doc_image", image);

    expect(state.drafts.doc_image.content).toBe("");
    expect(state.tabs[0]).toMatchObject({ mode: "read", readOnly: true, dirty: false });
  });

  it("keeps a dirty draft but rebases a global snapshot when its file hash is unchanged", () => {
    let state = openAndLoad("doc_a", detail("doc_a", "base\n", "sha-base", "snap-1"));
    state = documentWorkspaceReducer(state, { type: "draft", documentId: "doc_a", content: "mine\n" });
    state = documentWorkspaceReducer(state, { type: "load", detail: detail("doc_a", "base\n", "sha-base", "snap-2") });

    expect(state.drafts.doc_a).toMatchObject({
      content: "mine\n",
      baseContent: "base\n",
      baseSha256: "sha-base",
      baseSnapshotId: "snap-2",
      dirty: true
    });
    expect(state.drafts.doc_a.warnings).not.toContain(expect.stringContaining("ручного merge"));
  });

  it("does not silently rebase a dirty draft after external content drift", () => {
    let state = openAndLoad("doc_a", detail("doc_a", "base\n", "sha-base", "snap-1"));
    state = documentWorkspaceReducer(state, { type: "draft", documentId: "doc_a", content: "mine\n" });
    state = documentWorkspaceReducer(state, { type: "load", detail: detail("doc_a", "theirs\n", "sha-theirs", "snap-2") });

    expect(state.drafts.doc_a.content).toBe("mine\n");
    expect(state.drafts.doc_a.baseSha256).toBe("sha-base");
    expect(state.drafts.doc_a.baseSnapshotId).toBe("snap-1");
    expect(state.drafts.doc_a.warnings.join(" ")).toContain("ручного merge");
  });

  it("rebases another dirty tab after saving the first tab publishes a new snapshot", () => {
    let state = openAndLoad("doc_a", detail("doc_a", "A\n", "sha-a", "snap-1"));
    state = openAndLoad("doc_b", detail("doc_b", "B\n", "sha-b", "snap-1"), state);
    state = documentWorkspaceReducer(state, { type: "draft", documentId: "doc_b", content: "B edited\n" });
    state = documentWorkspaceReducer(state, { type: "saved", documentId: "doc_a", content: "A saved\n", sha256: "sha-a2", snapshotId: "snap-2" });
    state = documentWorkspaceReducer(state, { type: "load", detail: detail("doc_b", "B\n", "sha-b", "snap-2") });

    expect(state.drafts.doc_b.content).toBe("B edited\n");
    expect(state.drafts.doc_b.baseSnapshotId).toBe("snap-2");
  });
});

describe("document draft persistence", () => {
  it("uses session storage and never persists restricted draft content", () => {
    let state = openAndLoad("doc_public", detail("doc_public", "base\n", "sha-public", "snap-1", { sensitivity: "internal" }));
    state = openAndLoad("doc_secret", detail("doc_secret", "secret base\n", "sha-secret", "snap-1", { sensitivity: "restricted" }), state);
    state = documentWorkspaceReducer(state, { type: "draft", documentId: "doc_public", content: "public draft marker\n" });
    state = documentWorkspaceReducer(state, { type: "draft", documentId: "doc_secret", content: "secret draft marker\n" });
    const localWrite = vi.spyOn(window.localStorage, "setItem");

    persistDocumentWorkspace(state);

    const persisted = window.sessionStorage.getItem("raytsystem.documents.workspace.v1") ?? "";
    expect(persisted).toContain("public draft marker");
    expect(persisted).not.toContain("secret draft marker");
    expect(localWrite).not.toHaveBeenCalled();
  });
});
