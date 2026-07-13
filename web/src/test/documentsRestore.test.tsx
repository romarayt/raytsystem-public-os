import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentRestoreDialog } from "../features/documents/DocumentRestoreDialog";
import { DocumentConflictDialog } from "../features/documents/DocumentConflictDialog";
import type { DocumentConflictDetails, DocumentHistoryEntry, DocumentRestorePreviewEnvelope } from "../features/documents/documentTypes";

const revision: DocumentHistoryEntry = {
  history_id: "history_1234567890",
  source: "raytsystem",
  recorded_at: "2026-07-12T10:00:00Z",
  content_sha256: "restored-sha",
  author: null,
  summary: "previous version"
};

afterEach(cleanup);

function preview(restoredContent: string | null): DocumentRestorePreviewEnvelope {
  return {
    preview_token: "preview-token-cryptographically-bound",
    snapshot_id: "snapshot-current",
    document_id: "doc_a",
    history_id: revision.history_id,
    current_sha256: "current-sha",
    restored_sha256: "restored-sha",
    current_content: "current\n",
    restored_content: restoredContent
  };
}

describe("document restore confirmation", () => {
  it("requires an explicit confirmation after preview, including an empty historical file", () => {
    const confirm = vi.fn();
    render(<DocumentRestoreDialog revision={revision} preview={preview("")} fallbackCurrentContent="fallback\n" pending={false} onCancel={() => undefined} onConfirm={confirm} />);

    const button = screen.getByRole("button", { name: "Подтвердить восстановление" });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(confirm).toHaveBeenCalledOnce();
  });

  it("fails closed when the revision source does not disclose content", () => {
    const confirm = vi.fn();
    render(<DocumentRestoreDialog revision={revision} preview={preview(null)} fallbackCurrentContent="fallback\n" pending={false} onCancel={() => undefined} onConfirm={confirm} />);

    expect(screen.getByText("Восстановление недоступно")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить восстановление" })).toBeDisabled();
  });
});

describe("document conflict disclosure gate", () => {
  it("does not allow merge when the current disk body is redacted", () => {
    const resolve = vi.fn();
    const conflict: DocumentConflictDetails = {
      document_id: "doc_a",
      expected_sha256: "a".repeat(64),
      current_sha256: "b".repeat(64),
      proposed_sha256: null,
      snapshot_id: "snapshot-current",
      current_content: null
    };

    render(<DocumentConflictDialog conflict={conflict} baseContent="base\n" draftContent="draft\n" onCancel={() => undefined} onResolve={resolve} />);

    expect(screen.getByText(/скрыт disclosure policy/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ручной merge" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Сохранить итог" })).toBeDisabled();
    expect(screen.getByText(/не предоставлен/i)).toBeInTheDocument();
  });
});
