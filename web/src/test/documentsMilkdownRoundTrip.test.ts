// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import { defaultValueCtx, Editor, editorViewCtx, remarkStringifyOptionsCtx, rootCtx, serializerCtx } from "@milkdown/kit/core";
import { commonmark } from "@milkdown/kit/preset/commonmark";
import { gfm, remarkGFMPlugin } from "@milkdown/kit/preset/gfm";
import {
  prepareVisualMarkdown,
  restoreVisualMarkdown,
  visualEditorBlockReason
} from "../features/documents/markdownCodec";

const mountedRoots: HTMLElement[] = [];

async function milkdownRoundTrip(source: string): Promise<string> {
  const envelope = prepareVisualMarkdown(source);
  const root = document.createElement("div");
  document.body.append(root);
  mountedRoots.push(root);
  const editor = await Editor.make()
    .config((ctx) => {
      ctx.set(rootCtx, root);
      ctx.set(defaultValueCtx, envelope.editorMarkdown);
      ctx.update(remarkStringifyOptionsCtx, (previous) => ({ ...previous, ...envelope.serialization.stringify }));
      ctx.set(remarkGFMPlugin.options.key, envelope.serialization.gfm);
    })
    .use(commonmark)
    .use(gfm)
    .create();
  try {
    const serialized = editor.action((ctx) => ctx.get(serializerCtx)(ctx.get(editorViewCtx).state.doc));
    return restoreVisualMarkdown(serialized, envelope).content;
  } finally {
    await editor.destroy();
  }
}

afterEach(() => {
  for (const root of mountedRoots.splice(0)) root.remove();
});

describe("Milkdown Markdown qualification corpus", () => {
  it.each([
    ["CommonMark", "# Heading\n\nParagraph with **bold**, *italic*, [link](https://example.test), and `code`.\n\n* alpha\n\n* beta\n\n1. one\n2. two\n\n> quote\n\n***\n\n```ts\nconst value = 1;\n```\n"],
    ["GFM", "# GFM\n\n~~removed~~\n\n* [ ] open\n\n* [x] done\n\n| Name  | Value |\n| ----- | ----- |\n| Alpha | 1     |\n"],
    ["raytsystem extensions and frontmatter", "---\ntags: [тест, 🚀]\naliases: [Пример]\n---\n# Кириллица\n\n[[Документ|Ссылка]]\n\n> [!NOTE] Важно\n> Содержимое\n"]
  ])("round-trips the canonical %s fixture through Milkdown parse and serialize", async (_name, source) => {
    expect(await milkdownRoundTrip(source)).toBe(source);
  });

  it("keeps guarded unknown syntax out of the Milkdown parser", async () => {
    const source = "<custom-element onclick=\"bad()\">opaque</custom-element>\n";
    const reason = visualEditorBlockReason(source);
    expect(reason).not.toBeNull();
    if (!reason) expect(await milkdownRoundTrip(source)).toBe(source);
  });

  it.each([
    ["dash bullets", "- one\n\n- two\n"],
    ["one-space-style nested list indentation", "- outer\n\n  - inner\n"],
    ["tab-stop nested list indentation", "-   outer\n\n    -   inner\n"],
    ["dash thematic break", "Before\n\n---\n\nAfter\n"],
    ["unpadded GFM table", "|A|B|\n|-|-|\n|1|2|\n"]
  ])("preserves the document's byte-exact %s style", async (_name, source) => {
    expect(await milkdownRoundTrip(source)).toBe(source);
  });

  it.each([
    ["mixed bullet markers", "- one\n\n* two\n"],
    ["tight-list spacing that Milkdown's model normalizes", "- one\n- two\n"],
    ["four-space nesting with one space after the marker", "- outer\n\n    - inner\n"]
  ])("still rejects %s when one serializer profile cannot preserve it", async (_name, source) => {
    expect(await milkdownRoundTrip(source)).not.toBe(source);
  });
});
