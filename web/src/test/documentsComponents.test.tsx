import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentTree } from "../features/documents/DocumentTree";
import { DocumentActionDialog } from "../features/documents/DocumentActionDialog";
import { Documents, folderWithinRoot } from "../features/documents/Documents";
import { DocumentInspector } from "../features/documents/DocumentInspector";
import type { DocumentDetailEnvelope, DocumentSummary } from "../features/documents/documentTypes";
import { mockFetch } from "./mockApi";

function documentAt(index: number): DocumentSummary {
  const padded = String(index).padStart(3, "0");
  return {
    document_id: `doc_${padded}`,
    root_id: "notes",
    path: `${padded}.md`,
    filename: `${padded}.md`,
    extension: ".md",
    title: `Документ ${padded}`,
    kind: "notes",
    mode: "read_write",
    sensitivity: "internal",
    size_bytes: 10,
    content_sha256: `sha-${padded}`,
    modified_at: "2026-07-12T10:00:00Z",
    first_seen_at: "2026-07-12T09:00:00Z",
    is_modified: false,
    is_new: false,
    tags: [],
    aliases: [],
    headings: [],
    outgoing_link_count: 0,
    backlink_count: 0,
    can_edit: true
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("virtualized document tree keyboard navigation", () => {
  it("scrolls and focuses an offscreen End target", () => {
    const callbacks: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callbacks.push(callback);
      return callbacks.length;
    });
    render(<DocumentTree documents={Array.from({ length: 200 }, (_, index) => documentAt(index))} selectedDocumentId={null} onOpen={() => undefined} />);
    const tree = screen.getByRole("tree");
    Object.defineProperty(tree, "clientHeight", { configurable: true, value: 72 });
    const first = screen.getByRole("treeitem", { name: /Документ 000/ });
    first.focus();

    fireEvent.keyDown(first, { key: "End" });
    act(() => {
      while (callbacks.length) callbacks.shift()?.(performance.now());
    });

    expect(tree.scrollTop).toBeGreaterThan(0);
    expect(screen.getByRole("treeitem", { name: /Документ 199/ })).toHaveFocus();
    expect(screen.getByRole("treeitem", { name: /Документ 199/ })).not.toHaveAttribute("aria-setsize");
  });

  it("renders a projected empty folder with its bounded count", () => {
    const expand = vi.fn();
    render(<DocumentTree
      documents={[]}
      roots={[{ root_id: "notes", label: "Заметки", path: "notes", mode: "read_write", kind: "notes", editable: true }]}
      folders={[{ folder_id: "folder-empty", root_id: "notes", path: "notes/empty", name: "empty", parent_path: "notes", document_count: 0, descendant_count: 0, mode: "read_write", can_create: true }]}
      selectedDocumentId={null}
      onOpen={() => undefined}
      onExpandFolder={expand}
    />);

    const empty = screen.getByRole("treeitem", { name: "empty, 0 документов" });
    expect(empty).toBeInTheDocument();
    fireEvent.click(empty);
    expect(expand).toHaveBeenCalledWith("notes", "notes/empty");
  });
});

describe("document destination policy", () => {
  it("derives a move folder inside the configured root", () => {
    expect(folderWithinRoot("knowledge/manual/projects/note.md", "knowledge/manual")).toBe("projects");
    expect(folderWithinRoot("knowledge/manual/note.md", "knowledge/manual")).toBe("");
    expect(folderWithinRoot("docs/note.md", "knowledge/manual")).toBe("");
  });

  it("clears the source-root folder when the move destination root changes", () => {
    render(<DocumentActionDialog kind="move" roots={[{ id: "manual", label: "Manual", writable: true }, { id: "docs", label: "Docs", writable: true }]} initialRootId="manual" initialFolder="projects" onCancel={() => undefined} onSubmit={() => undefined} />);
    expect(screen.getByLabelText("Папка")).toHaveValue("projects");
    fireEvent.change(screen.getByLabelText("Разрешённый root"), { target: { value: "docs" } });
    expect(screen.getByLabelText("Папка")).toHaveValue("");
  });
});

describe("document properties draft binding", () => {
  it("keeps controlled values on the current draft and preserves source-only fields", () => {
    const initial = "---\ntitle: A\ntags: [one]\ndraft: false\nunknown: keep # preserve\n---\n# Note\n";
    const detail: DocumentDetailEnvelope = {
      snapshot_id: "docsnap_one",
      document: documentAt(1),
      content: initial,
      format: "markdown",
      content_sha256: "sha-001",
      line_ending: "lf",
      final_newline: true,
      warnings: [],
      frontmatter: [
        { key: "title", value: "A", type: "string", editable: true },
        { key: "tags", value: ["one"], type: "tags", editable: true },
        { key: "draft", value: false, type: "boolean", editable: true },
        { key: "unknown", value: "keep", type: "complex", editable: false }
      ]
    };
    function Harness() {
      const [content, setContent] = useState(initial);
      return <><DocumentInspector detail={detail} content={content} section="properties" links={undefined} backlinks={undefined} history={undefined} onSectionChange={() => undefined} onContentChange={setContent} onOpenDocument={() => undefined} onPreviewRevision={() => undefined} onRequestRestore={() => undefined} onShowInGraph={() => undefined} /><output data-testid="draft">{content}</output></>;
    }
    render(<Harness />);

    const title = screen.getByLabelText(/title/);
    fireEvent.change(title, { target: { value: "AB" } });
    fireEvent.change(screen.getByLabelText(/title/), { target: { value: "ABC" } });
    fireEvent.change(screen.getByLabelText(/tags/), { target: { value: "one, two" } });
    fireEvent.change(screen.getByLabelText(/draft/), { target: { value: "true" } });

    expect(screen.getByLabelText(/title/)).toHaveValue("ABC");
    expect(screen.getByLabelText(/tags/)).toHaveValue("one, two");
    expect(screen.getByTestId("draft")).toHaveTextContent("title: ABC");
    expect(screen.getByTestId("draft")).toHaveTextContent('tags: ["one", "two"]');
    expect(screen.getByTestId("draft")).toHaveTextContent("draft: true");
    expect(screen.getByTestId("draft")).toHaveTextContent("unknown: keep # preserve");
    expect(screen.getByLabelText(/unknown/)).toBeDisabled();
  });

  it("flushes a dirty draft to session storage on immediate route unmount", async () => {
    window.sessionStorage.clear();
    vi.stubGlobal("fetch", vi.fn(mockFetch));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const rendered = render(<QueryClientProvider client={client}><Documents onShowInGraph={() => undefined} /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("treeitem", { name: /Layout note/ }));
    const tags = await screen.findByLabelText(/tags/);
    fireEvent.change(tags, { target: { value: "layout, immediate-draft" } });
    await waitFor(() => expect(document.querySelector(".documents-route")).toHaveAttribute("data-unsaved-changes", "true"));

    rendered.unmount();

    expect(window.sessionStorage.getItem("raytsystem.documents.workspace.v1")).toContain("immediate-draft");
    client.clear();
  });
});
