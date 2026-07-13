import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentApiError, listingPath, retryDocumentListing, useDocumentBacklinks, useDocumentDetail, useDocumentHistory, useDocumentLinks } from "../features/documents/documentHooks";
import type { DocumentQuery } from "../features/documents/documentTypes";

function BoundReads({ snapshot }: { snapshot: string | null }) {
  useDocumentDetail("doc_deep_link", snapshot);
  useDocumentLinks("doc_deep_link", snapshot);
  useDocumentBacklinks("doc_deep_link", snapshot);
  useDocumentHistory("doc_deep_link", snapshot);
  return null;
}

afterEach(() => vi.restoreAllMocks());

describe("snapshot-bound document reads", () => {
  it("does not emit unbound deep-link requests before listing returns a snapshot", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><BoundReads snapshot={null} /></QueryClientProvider>);

    await Promise.resolve();
    expect(fetchSpy).not.toHaveBeenCalled();
    client.clear();
  });
});

describe("document listing route selection", () => {
  const base: DocumentQuery = { view: "files", query: "", mode: "all", sort: "modified_desc", limit: 50 };

  it("keeps recent kind separate from document kind and state search syntax", () => {
    const recent = new URL(listingPath({ ...base, view: "added" }, null), "https://raytsystem.invalid");
    expect(recent.pathname).toBe("/api/v1/documents/recent");
    expect(recent.searchParams.get("kind")).toBe("added");

    const searched = new URL(listingPath({ ...base, view: "modified", query: "alpha", kind: "notes" }, null), "https://raytsystem.invalid");
    expect(searched.pathname).toBe("/api/v1/documents/search");
    expect(searched.searchParams.get("q")).toBe("alpha is:modified");
    expect(searched.searchParams.get("kind")).toBe("notes");

    const modified = new URL(listingPath({ ...base, view: "modified" }, null), "https://raytsystem.invalid");
    expect(modified.pathname).toBe("/api/v1/documents/search");
    expect(modified.searchParams.get("q")).toBe("is:modified");

    const unfilteredRecent = new URL(listingPath({ ...base, view: "recent" }, null), "https://raytsystem.invalid");
    expect(unfilteredRecent.pathname).toBe("/api/v1/documents/recent");
    expect(unfilteredRecent.searchParams.get("kind")).toBe("recent");
  });

  it("uses search for filtered recent views and clamps page size", () => {
    const path = new URL(listingPath({ ...base, view: "added", rootId: "manual", limit: 999 }, null), "https://raytsystem.invalid");
    expect(path.pathname).toBe("/api/v1/documents/search");
    expect(path.searchParams.get("q")).toBe("is:new");
    expect(path.searchParams.get("root_id")).toBe("manual");
    expect(path.searchParams.get("limit")).toBe("50");
  });

  it("fetches favorites by repeated opaque IDs instead of filtering unrelated pages", () => {
    const path = new URL(listingPath({ ...base, view: "files", documentIds: ["doc_a", "doc_b"] }, null), "https://raytsystem.invalid");
    expect(path.pathname).toBe("/api/v1/documents");
    expect(path.searchParams.getAll("document_ids")).toEqual(["doc_a", "doc_b"]);
  });

  it("polls only the typed initial-index state", () => {
    const initializing = new DocumentApiError(503, "document_index_initializing", "preparing");
    expect(retryDocumentListing(59, initializing)).toBe(true);
    expect(retryDocumentListing(60, initializing)).toBe(false);
    expect(retryDocumentListing(0, new DocumentApiError(409, "document_index_stale", "stale"))).toBe(true);
    expect(retryDocumentListing(1, new DocumentApiError(409, "document_index_stale", "stale"))).toBe(false);
  });
});
