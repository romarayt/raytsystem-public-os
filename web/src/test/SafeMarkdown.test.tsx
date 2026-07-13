import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeMarkdown } from "../components/SafeMarkdown";

describe("SafeMarkdown", () => {
  it("renders the supported document structure as React nodes", () => {
    const { container } = render(
      <SafeMarkdown content={`---\nname: demo\n---\n# Heading\n\nParagraph with \`code\` and **bold**.\n\n- one\n- two\n\n> quote\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n\`\`\`sh\necho inert\n\`\`\``} />
    );

    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(container.querySelector("blockquote")).toHaveTextContent("quote");
    expect(container.querySelector(".skill-markdown-frontmatter")).toHaveTextContent("name: demo");
    expect(container.querySelector(".skill-markdown-code")).toHaveTextContent("echo inert");
  });

  it("never creates executable HTML, embeds, event handlers or images", () => {
    (window as Window & { __skillPwned?: boolean }).__skillPwned = false;
    const { container } = render(
      <SafeMarkdown content={'<script>window.__skillPwned = true</script>\n\n<img src="https://example.test/pixel" onerror="window.__skillPwned=true">\n\n<iframe src="https://example.test"></iframe>\n\n<object data="https://example.test/payload"></object>\n\n<embed src="https://example.test/payload">'} />
    );

    expect(container.querySelector("script, iframe, img, object, embed")).toBeNull();
    expect(container).toHaveTextContent("<script>window.__skillPwned = true</script>");
    expect(container.querySelector("[onerror], [onclick], [onload]")).toBeNull();
    expect((window as Window & { __skillPwned?: boolean }).__skillPwned).toBe(false);
    delete (window as Window & { __skillPwned?: boolean }).__skillPwned;
  });

  it("allows ordinary links but does not create unsafe protocol links", () => {
    render(<SafeMarkdown content={'[Docs](https://example.test) [Local](/docs) [Bad](javascript:alert(1)) [Protocol](//example.test/path)'} />);

    expect(screen.getByRole("link", { name: "Docs" })).toHaveAttribute("href", "https://example.test");
    expect(screen.getByRole("link", { name: "Docs" })).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.getByRole("link", { name: "Local" })).toHaveAttribute("href", "/docs");
    expect(screen.queryByRole("link", { name: "Bad" })).not.toBeInTheDocument();
    expect(screen.getByText("Bad")).toHaveClass("unsafe-markdown-link");
    expect(screen.queryByRole("link", { name: "Protocol" })).not.toBeInTheDocument();
  });

  it("bounds deeply nested blockquotes without losing inert content", () => {
    const deeplyNestedQuote = `${"> ".repeat(10_000)}deep quote`;
    const { container } = render(<SafeMarkdown content={deeplyNestedQuote} />);

    expect(container.querySelectorAll("blockquote")).toHaveLength(16);
    expect(container).toHaveTextContent("deep quote");
    expect(container.querySelector("script, iframe, img, object, embed")).toBeNull();
  });

  it("bounds adversarially wide and tall tables without throwing or expanding the DOM", () => {
    const wideHeader = Array.from({ length: 50_000 }, (_, index) => `H${index}`).join("|");
    const wideDivider = Array.from({ length: 50_000 }, () => "---").join("|");
    const rows = Array.from({ length: 1_000 }, (_, row) => `R${row}|value`).join("\n");
    const { container } = render(<SafeMarkdown content={`${wideHeader}\n${wideDivider}\n${rows}`} />);

    expect(container.querySelectorAll("th, td").length).toBeLessThanOrEqual(1_024);
    expect(container.querySelectorAll("tbody tr").length).toBeLessThanOrEqual(128);
    expect(container.querySelectorAll("*").length).toBeLessThanOrEqual(4_100);
    expect(container.querySelector('[role="status"]')).toHaveTextContent("Предпросмотр сокращён");
    expect(container.querySelector("script, iframe, img, object, embed")).toBeNull();
  });

  it("bounds documents with many top-level blocks deterministically", () => {
    const manyBlocks = Array.from({ length: 20_000 }, (_, index) => `# Heading ${index}`).join("\n");
    const { container } = render(<SafeMarkdown content={manyBlocks} />);

    expect(container.querySelectorAll("h1").length).toBeLessThanOrEqual(2_048);
    expect(container.querySelectorAll("*").length).toBeLessThanOrEqual(4_100);
    expect(container.querySelector('[role="status"]')).toHaveTextContent("безопасный лимит отображения");
    expect(container).not.toHaveTextContent("Heading 19999");
  });
});
