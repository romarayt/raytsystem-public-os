import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SafeMarkdownView, safeImageUrl, safeLink } from "../features/documents/SafeMarkdownView";
import { focusMarkdownHeading, matchingDocumentLink } from "../features/documents/Documents";
import {
  deriveFrontmatterFields,
  inspectMarkdownForVisualEditing,
  prepareVisualMarkdown,
  registerProtectedVisualToken,
  restoreVisualMarkdown,
  updateFrontmatterField,
  visualEditorBlockReason
} from "../features/documents/markdownCodec";

describe("Documents safe Markdown rendering", () => {
  it("allows bounded links and rejects protocol-relative, credential, script and backslash forms", () => {
    expect(safeLink("https://example.test/docs")).toEqual({ href: "https://example.test/docs", external: true });
    expect(safeLink("/documents")).toEqual({ href: "/documents", external: false });
    expect(safeLink("//example.test/pixel")).toBeNull();
    expect(safeLink("https://user:pass@example.test/")).toBeNull();
    expect(safeLink("javascript:alert(1)")).toBeNull();
    expect(safeLink("\\\\server\\share")).toBeNull();
  });

  it("matches plain and aliased heading wikilinks and focuses the rendered heading", () => {
    const links = [
      { target: "Другой#Раздел", target_document_id: "doc_wrong", label: "Название", heading: "Раздел", line: 1, context: "ctx" },
      { target: "Документ#Раздел", target_document_id: "doc_target", label: "Название", heading: "Раздел", line: 1, context: "ctx" }
    ];
    expect(matchingDocumentLink({ target: "Документ", label: "Документ", heading: "Раздел", embed: false }, links)?.target_document_id).toBe("doc_target");
    expect(matchingDocumentLink({ target: "Документ", label: "Название", heading: "Раздел", embed: false }, links)?.target_document_id).toBe("doc_target");
    render(<SafeMarkdownView content={"# Раздел\n"} />);
    const heading = screen.getByRole("heading", { name: "Раздел" });
    const scrollIntoView = vi.fn();
    heading.scrollIntoView = scrollIntoView;
    expect(focusMarkdownHeading(document, "Раздел")).toBe(true);
    expect(heading).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("only renders images resolved to an opaque raytsystem asset URL", () => {
    expect(safeImageUrl("/api/v1/documents/assets/opaque-123")).toBe("/api/v1/documents/assets/opaque-123");
    expect(safeImageUrl("https://example.test/pixel.png")).toBeNull();
    const { container } = render(
      <SafeMarkdownView
        content={'![Allowed](image.png)\n\n![Remote](https://example.test/pixel.png)'}
        resolveImage={(target) => target === "image.png" ? "/api/v1/documents/assets/opaque-123" : null}
      />
    );

    expect(screen.getByRole("img", { name: "Allowed" })).toHaveAttribute("src", "/api/v1/documents/assets/opaque-123");
    expect(screen.getByText(/Изображение заблокировано: Remote/)).toBeInTheDocument();
    expect(container.querySelector('img[src^="http"]')).toBeNull();
  });

  it("renders footnotes, wikilinks and inert HTML without executable nodes", () => {
    const open = vi.fn();
    const { container } = render(
      <SafeMarkdownView
        content={'Текст со сноской[^one] и [[Документ#Раздел|ссылкой]].\n\n[^one]: Безопасное **примечание**.\n\n<img src="x" onerror="alert(1)">'}
        onOpenWikilink={open}
      />
    );

    expect(screen.getByRole("link", { name: "Сноска one" })).toHaveAttribute("href", "#doc-footnote-one");
    expect(container.querySelector("#doc-footnote-one")).toHaveTextContent("Безопасное примечание");
    fireEvent.click(screen.getByRole("button", { name: /ссылкой/ }));
    expect(open).toHaveBeenCalledWith({ target: "Документ", label: "ссылкой", heading: "Раздел", embed: false });
    expect(container.querySelector("img, script, iframe")).toBeNull();
    expect(container.querySelector(".doc-inert-html")).toHaveTextContent("<img");
  });

  it("bounds a five-megabyte read view and offers Source mode", () => {
    const openSource = vi.fn();
    const content = "- item\n".repeat(700_000);
    const { container } = render(<SafeMarkdownView content={content} onOpenSource={openSource} />);

    expect(screen.getByText("Показана ограниченная часть большого документа")).toBeInTheDocument();
    expect(container.querySelectorAll("li").length).toBeLessThanOrEqual(2_500);
    fireEvent.click(screen.getByRole("button", { name: "Открыть Source mode" }));
    expect(openSource).toHaveBeenCalledOnce();
  });

  it("bounds adversarial nested blockquotes without recursive overflow", () => {
    const { container } = render(<SafeMarkdownView content={`${">".repeat(50_000)} nested`} />);

    expect(container.querySelectorAll("blockquote").length).toBeLessThanOrEqual(24);
    expect(container.querySelector(".doc-nesting-limit")).toBeInTheDocument();
  });
});

describe("Documents Markdown lossless guards", () => {
  it("preserves frontmatter, raytsystem extensions, CRLF and final newline exactly", () => {
    const original = "---\r\ntags: [тест, emoji-🚀]\r\naliases: [Пример]\r\n---\r\n# Заголовок\r\n\r\n[[Документ|Ссылка]]\r\n\r\n> [!NOTE] Важно\r\n";
    const envelope = prepareVisualMarkdown(original);
    const restored = restoreVisualMarkdown(envelope.editorMarkdown, envelope);

    expect(restored.content).toBe(original);
    expect(restored.issues).toEqual([]);
    expect(envelope.lineEnding).toBe("crlf");
    expect(envelope.finalNewline).toBe(true);
  });

  it("blocks visual save if a protected token is lost or unknown syntax is present", () => {
    const envelope = prepareVisualMarkdown("[[Target]]\n");
    const restored = restoreVisualMarkdown("Target\n", envelope);
    expect(restored.issues).toEqual(expect.arrayContaining([expect.objectContaining({ code: "protected_token_lost", severity: "error" })]));

    const issues = inspectMarkdownForVisualEditing('<section onclick="bad()">x</section>\n\n```dataview\nLIST\n```\n');
    expect(issues.map((issue) => issue.code)).toEqual(expect.arrayContaining(["html_fragment", "executable_fence"]));
  });

  it("round-trips custom extensions registered by visual toolbar actions", () => {
    const envelope = prepareVisualMarkdown("# Existing\n");
    const wikilink = registerProtectedVisualToken(envelope, "[[New document]]");
    const callout = registerProtectedVisualToken(envelope, "\n> [!NOTE] New callout\n> \n");
    const task = registerProtectedVisualToken(envelope, "\n- [ ] New task\n");
    const restored = restoreVisualMarkdown(`${envelope.editorMarkdown.trimEnd()}\n\n${wikilink}${callout}${task}`, envelope);

    expect(restored.content).toContain("[[New document]]");
    expect(restored.content).toContain("> [!NOTE] New callout");
    expect(restored.content).toContain("- [ ] New task");
    expect(restored.issues).toEqual([]);
  });

  it("does not rewrite a YAML field that has a comment", () => {
    const source = "---\ntitle: Existing # keep this\n---\nBody\n";
    const result = updateFrontmatterField(source, { key: "title", value: "Existing", type: "string", editable: true }, "Changed");
    expect(result.content).toBe(source);
    expect(result.warning).toContain("комментарий");
  });

  it("derives only conservative editable fields when the API omits descriptors", () => {
    const fields = deriveFrontmatterFields("---\ntitle: Пример\ntags: [one, два]\ncount: 2\ndraft: false\nunsafe: value # keep\nnested:\n  child: value\n---\n");
    expect(fields).toEqual(expect.arrayContaining([
      expect.objectContaining({ key: "title", type: "string", editable: true }),
      expect.objectContaining({ key: "tags", type: "tags", value: ["one", "два"], editable: true }),
      expect.objectContaining({ key: "count", type: "number", value: 2 }),
      expect.objectContaining({ key: "draft", type: "boolean", value: false }),
      expect.objectContaining({ key: "unsafe", type: "complex", editable: false }),
      expect.objectContaining({ key: "nested", type: "complex", editable: false })
    ]));
  });

  it("does not qualify oversized or unknown documents for Milkdown mounting", () => {
    expect(visualEditorBlockReason("x".repeat(1_000_001))).toContain("лимит");
    expect(visualEditorBlockReason("<custom-element>unknown</custom-element>\n")).toContain("Source mode");
    expect(visualEditorBlockReason("# Safe\n", { can_open: false, can_save: false, round_trip_safe: false, warnings: [], unsupported_syntax: [] })).toContain("запретила");
    expect(visualEditorBlockReason("# Safe\n", { can_open: true, can_save: true, round_trip_safe: true, warnings: [], unsupported_syntax: [] })).toBeNull();
  });
});
