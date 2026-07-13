import "@testing-library/jest-dom/vitest";
import { EditorState } from "@codemirror/state";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SourceEditor, { sourceLineSeparator } from "../features/documents/SourceEditor";

Object.defineProperties(Range.prototype, {
  getClientRects: { configurable: true, value: () => [] },
  getBoundingClientRect: { configurable: true, value: () => new DOMRect(0, 0, 0, 0) }
});

function addNonce(value: string): HTMLMetaElement {
  const meta = document.createElement("meta");
  meta.name = "raytsystem-csp-nonce";
  meta.content = value;
  document.head.append(meta);
  return meta;
}

afterEach(() => {
  cleanup();
  document.querySelectorAll('meta[name="raytsystem-csp-nonce"]').forEach((node) => node.remove());
});

describe("SourceEditor CSP", () => {
  it("applies the local session nonce to CodeMirror generated styles", async () => {
    addNonce("test-nonce-123");
    const { container } = render(<SourceEditor value="# Safe\n" readOnly={false} issues={[]} lineNumbers onChange={() => undefined} onSave={() => undefined} onToggleVisual={() => undefined} />);

    await waitFor(() => expect(container.querySelector(".cm-editor")).toBeInTheDocument());
    const codeMirrorStyles = [...document.head.querySelectorAll<HTMLStyleElement>('style[nonce="test-nonce-123"]')];
    expect(codeMirrorStyles.length).toBeGreaterThan(0);
    expect(codeMirrorStyles.every((style) => style.getAttribute("nonce") === "test-nonce-123")).toBe(true);
    expect(container.querySelector('img[src^="data:"]')).toBeNull();
  });

  it("fails closed instead of generating an un-nonced editor style sheet", () => {
    const styleCount = document.head.querySelectorAll("style").length;
    const { container } = render(<SourceEditor value="# Safe\n" readOnly={false} issues={[]} onChange={() => undefined} onSave={() => undefined} onToggleVisual={() => undefined} />);

    expect(screen.getByRole("alert")).toHaveTextContent("CSP nonce");
    expect(container.querySelector(".cm-editor")).toBeNull();
    expect(document.head.querySelectorAll("style")).toHaveLength(styleCount);
  });

  it("preserves CRLF and final-newline bytes through CodeMirror state and prop synchronization", async () => {
    const original = "first\r\nsecond\r\n";
    expect(sourceLineSeparator(original)).toBe("\r\n");
    const state = EditorState.create({ doc: original, extensions: [EditorState.lineSeparator.of("\r\n")] });
    expect(state.sliceDoc()).toBe(original);
    expect(state.update({ changes: { from: 5, insert: " edited" } }).state.sliceDoc()).toBe("first edited\r\nsecond\r\n");

    addNonce("test-nonce-crlf");
    const onChange = vi.fn();
    const rendered = render(<SourceEditor value={original} readOnly={false} issues={[]} onChange={onChange} onSave={() => undefined} onToggleVisual={() => undefined} />);
    rendered.rerender(<SourceEditor value={"first\r\nchanged\r\n"} readOnly={false} issues={[]} onChange={onChange} onSave={() => undefined} onToggleVisual={() => undefined} />);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("first\r\nchanged\r\n"));
  });

  it("fails closed for mixed line endings", () => {
    addNonce("test-nonce-mixed");
    render(<SourceEditor value={"first\r\nsecond\n"} readOnly={false} issues={[]} onChange={() => undefined} onSave={() => undefined} onToggleVisual={() => undefined} />);
    expect(screen.getByRole("alert")).toHaveTextContent("смешанные");
  });
});
