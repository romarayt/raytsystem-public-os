import { Fragment, type ReactNode } from "react";

export interface WikilinkTarget {
  target: string;
  label: string;
  heading: string | null;
  embed: boolean;
}

interface SafeMarkdownViewProps {
  content: string;
  onOpenWikilink?: (target: WikilinkTarget) => void;
  onOpenRelativeLink?: (target: string) => void;
  resolveImage?: (target: string) => string | null;
  onOpenSource?: () => void;
  depth?: number;
}

interface FootnoteDefinition {
  id: string;
  content: string;
}

const MAX_RENDER_CHARACTERS = 200_000;
const MAX_RENDER_LINES = 2_500;
const MAX_INLINE_TOKENS = 2_000;
const MAX_TABLE_CELLS = 64;
const MAX_NESTING_DEPTH = 24;

function boundedMarkdown(content: string): { content: string; truncated: boolean } {
  const characterEnd = Math.min(content.length, MAX_RENDER_CHARACTERS);
  let end = characterEnd;
  let lines = 1;
  for (let index = 0; index < characterEnd; index += 1) {
    if (content.charCodeAt(index) !== 10) continue;
    lines += 1;
    if (lines > MAX_RENDER_LINES) {
      end = index;
      break;
    }
  }
  return { content: content.slice(0, end), truncated: end < content.length };
}

function safeLink(url: string): { href: string; external: boolean } | null {
  const trimmed = url.trim();
  const hasControlCharacter = [...trimmed].some((character) => character.charCodeAt(0) <= 31 || character.charCodeAt(0) === 127);
  if (!trimmed || hasControlCharacter || trimmed.includes("\\") || trimmed.startsWith("//")) return null;
  if (trimmed.startsWith("#") || (trimmed.startsWith("/") && !trimmed.startsWith("//"))) return { href: trimmed, external: false };
  try {
    const parsed = new URL(trimmed, "https://raytsystem.invalid/");
    if (parsed.username || parsed.password) return null;
    if (["https:", "http:", "mailto:"].includes(parsed.protocol)) return { href: trimmed, external: parsed.origin !== "https://raytsystem.invalid" };
  } catch {
    return null;
  }
  return null;
}

function safeImageUrl(url: string): string | null {
  if (!url.startsWith("/api/v1/documents/")) return null;
  if (!/^\/api\/v1\/documents\/(?:attachments|assets)\/[A-Za-z0-9._~-]+(?:\?[A-Za-z0-9._~=&%-]+)?$/.test(url)) return null;
  return url;
}

function wikiTarget(source: string, embed: boolean): WikilinkTarget {
  const [rawTarget, alias] = source.split("|", 2);
  const [target, heading] = rawTarget.split("#", 2);
  return {
    target: target.trim(),
    label: (alias || target).trim(),
    heading: heading?.trim() || null,
    embed
  };
}

function inlineNodes(
  text: string,
  props: Pick<SafeMarkdownViewProps, "onOpenWikilink" | "onOpenRelativeLink" | "resolveImage">,
  keyPrefix: string
): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(!?\[\[[^\]\n]+\]\]|!\[[^\]\n]*\]\([^)\n]+\)|\[\^[^\]\n]+\]|\[[^\]\n]+\]\([^)\n]+\)|\*\*[^*\n]+\*\*|~~[^~\n]+~~|`[^`\n]+`|(?<!\*)\*[^*\n]+\*(?!\*)|(?<!_)_[^_\n]+_(?!_))/g;
  let last = 0;
  let index = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (index >= MAX_INLINE_TOKENS) {
      nodes.push(<Fragment key={`${keyPrefix}-bounded`}>{text.slice(last)}</Fragment>);
      return nodes;
    }
    if (match.index > last) nodes.push(<Fragment key={`${keyPrefix}-${index++}`}>{text.slice(last, match.index)}</Fragment>);
    const token = match[0];
    const key = `${keyPrefix}-${index++}`;
    if (token.startsWith("![[") || token.startsWith("[[")) {
      const embed = token.startsWith("![[");
      const target = wikiTarget(token.slice(embed ? 3 : 2, -2), embed);
      const resolved = embed ? safeImageUrl(props.resolveImage?.(target.target) ?? "") : null;
      if (embed && resolved) {
        nodes.push(<img key={key} src={resolved} alt={target.label} loading="lazy" decoding="async" />);
      } else {
        nodes.push(
          <button key={key} type="button" className={embed ? "doc-wikilink doc-embed" : "doc-wikilink"} onClick={() => props.onOpenWikilink?.(target)}>
            {embed ? "Вложение: " : ""}{target.label}{target.heading ? ` · ${target.heading}` : ""}
          </button>
        );
      }
    } else if (token.startsWith("![")) {
      const altEnd = token.indexOf("]");
      const alt = token.slice(2, altEnd);
      const source = token.slice(altEnd + 2, -1).trim().replace(/\s+["'][^"']*["']$/, "");
      const resolved = safeImageUrl(props.resolveImage?.(source) ?? "");
      nodes.push(resolved
        ? <img key={key} src={resolved} alt={alt} loading="lazy" decoding="async" />
        : <span key={key} className="doc-blocked-image" role="note">Изображение заблокировано: {alt || source}</span>);
    } else if (token.startsWith("[^")) {
      const id = token.slice(2, -1).trim();
      const anchor = `doc-footnote-${headingId(id)}`;
      nodes.push(<sup key={key} className="doc-footnote-reference"><a href={`#${anchor}`} aria-label={`Сноска ${id}`}>{id}</a></sup>);
    } else if (token.startsWith("[")) {
      const labelEnd = token.indexOf("]");
      const label = token.slice(1, labelEnd);
      const url = token.slice(labelEnd + 2, -1).trim().replace(/\s+["'][^"']*["']$/, "");
      const safe = safeLink(url);
      const relativeDocument = /^(?:\.\.?\/)?[^:#?]+\.(?:md|mdx)(?:#.*)?$/i.test(url);
      if (relativeDocument && props.onOpenRelativeLink) {
        nodes.push(<button key={key} type="button" className="doc-relative-link" onClick={() => props.onOpenRelativeLink?.(url)}>{label}</button>);
      } else if (safe) {
        nodes.push(<a key={key} href={safe.href} target={safe.external ? "_blank" : undefined} rel={safe.external ? "noreferrer noopener" : undefined}>{label}</a>);
      } else {
        nodes.push(<span key={key} className="doc-unsafe-link" title="Небезопасная ссылка заблокирована">{label}</span>);
      }
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{inlineNodes(token.slice(2, -2), props, `${key}-b`)}</strong>);
    } else if (token.startsWith("~~")) {
      nodes.push(<del key={key}>{inlineNodes(token.slice(2, -2), props, `${key}-s`)}</del>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else {
      nodes.push(<em key={key}>{inlineNodes(token.slice(1, -1), props, `${key}-i`)}</em>);
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(<Fragment key={`${keyPrefix}-${index}`}>{text.slice(last)}</Fragment>);
  return nodes;
}

function headingId(text: string): string {
  return text
    .toLocaleLowerCase("ru-RU")
    .split("").filter((character) => !"`*_~[]()".includes(character)).join("")
    .trim()
    .replace(/[^a-zа-яё0-9]+/gi, "-")
    .replace(/^-|-$/g, "") || "section";
}

function tableCells(row: string): string[] {
  return row.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).slice(0, MAX_TABLE_CELLS).map((cell) => cell.trim().replaceAll("\\|", "|"));
}

function frontmatterBlock(lines: string[], key: string): ReactNode {
  const fields = lines.slice(1, -1).filter((line) => line.trim() && !line.trimStart().startsWith("#"));
  return (
    <details className="doc-frontmatter" key={key}>
      <summary>Свойства документа</summary>
      <dl>
        {fields.map((line, index) => {
          const separator = line.indexOf(":");
          return separator > 0 ? (
            <div key={`${key}-${index}`}><dt>{line.slice(0, separator).trim()}</dt><dd>{line.slice(separator + 1).trim() || "—"}</dd></div>
          ) : <div key={`${key}-${index}`}><dt>YAML</dt><dd><code>{line}</code></dd></div>;
        })}
      </dl>
    </details>
  );
}

function collectFootnotes(lines: string[], start: number): { definitions: FootnoteDefinition[]; consumed: Set<number> } {
  const definitions: FootnoteDefinition[] = [];
  const consumed = new Set<number>();
  let fence: string | null = null;
  for (let index = start; index < lines.length; index += 1) {
    const fenceMatch = /^\s*(```+|~~~+)/.exec(lines[index]);
    if (fenceMatch) {
      if (!fence) fence = fenceMatch[1][0];
      else if (fenceMatch[1][0] === fence) fence = null;
      continue;
    }
    if (fence) continue;
    const match = /^\[\^([^\]\n]+)\]:\s*(.*)$/.exec(lines[index]);
    if (!match) continue;
    consumed.add(index);
    const body = [match[2]];
    let continuation = index + 1;
    while (continuation < lines.length && /^(?: {2,}|\t)\S?/.test(lines[continuation])) {
      consumed.add(continuation);
      body.push(lines[continuation].replace(/^(?: {2,}|\t)/, ""));
      continuation += 1;
    }
    definitions.push({ id: match[1].trim(), content: body.join("\n") });
    index = continuation - 1;
  }
  return { definitions, consumed };
}

export function SafeMarkdownView({ content, onOpenWikilink, onOpenRelativeLink, resolveImage, onOpenSource, depth = 0 }: SafeMarkdownViewProps) {
  if (depth >= MAX_NESTING_DEPTH) return <pre className="doc-inert-html doc-nesting-limit" role="note"><code>{content.slice(0, MAX_RENDER_CHARACTERS)}</code></pre>;
  const bounded = boundedMarkdown(content);
  const normalized = bounded.content.replace(/\r\n?/g, "\n");
  const lines = normalized.split("\n");
  const blocks: ReactNode[] = [];
  const inlineProps = { onOpenWikilink, onOpenRelativeLink, resolveImage };
  let cursor = 0;
  let key = 0;

  if (lines[0] === "---") {
    const end = lines.slice(1).findIndex((line) => line === "---");
    if (end >= 0) {
      blocks.push(frontmatterBlock(lines.slice(0, end + 2), `fm-${key++}`));
      cursor = end + 2;
    }
  }

  const footnotes = collectFootnotes(lines, cursor);

  while (cursor < lines.length) {
    if (footnotes.consumed.has(cursor)) { cursor += 1; continue; }
    const line = lines[cursor];
    if (!line.trim()) { cursor += 1; continue; }
    const blockKey = `block-${key++}`;

    if (/^```|^~~~/.test(line)) {
      const marker = line.slice(0, 3);
      const language = line.slice(3).trim();
      const code: string[] = [];
      cursor += 1;
      while (cursor < lines.length && !lines[cursor].startsWith(marker)) code.push(lines[cursor++]);
      if (cursor < lines.length) cursor += 1;
      blocks.push(<pre key={blockKey} className="doc-code-block" data-language={language || undefined}><code>{code.join("\n")}</code></pre>);
      continue;
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const text = heading[2].replace(/\s+#+\s*$/, "").replace(/\s*\{#[^}]+\}\s*$/, "");
      const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      blocks.push(<Tag key={blockKey} id={headingId(text)}>{inlineNodes(text, inlineProps, blockKey)}</Tag>);
      cursor += 1;
      continue;
    }

    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push(<hr key={blockKey} />);
      cursor += 1;
      continue;
    }

    const callout = /^>\s*\[!([A-Za-z0-9_-]+)\][+-]?\s*(.*)$/.exec(line);
    if (callout) {
      const body: string[] = [];
      cursor += 1;
      while (cursor < lines.length && lines[cursor].startsWith(">")) body.push(lines[cursor++].replace(/^>\s?/, ""));
      blocks.push(
        <aside key={blockKey} className={`doc-callout doc-callout-${callout[1].toLowerCase()}`}>
          <strong>{callout[2] || callout[1]}</strong>
          {body.length ? <div>{renderBlocks(body.join("\n"), inlineProps, `${blockKey}-callout`, depth + 1)}</div> : null}
        </aside>
      );
      continue;
    }

    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (cursor < lines.length && lines[cursor].startsWith(">")) quote.push(lines[cursor++].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={blockKey}>{renderBlocks(quote.join("\n"), inlineProps, `${blockKey}-quote`, depth + 1)}</blockquote>);
      continue;
    }

    if (line.trimStart().startsWith("|") && cursor + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[cursor + 1])) {
      const rows: string[] = [];
      while (cursor < lines.length && lines[cursor].trimStart().startsWith("|")) rows.push(lines[cursor++]);
      const header = tableCells(rows[0]);
      const body = rows.slice(2).map(tableCells);
      blocks.push(
        <div key={blockKey} className="doc-table-scroll"><table><thead><tr>{header.map((cell, index) => <th key={index}>{inlineNodes(cell, inlineProps, `${blockKey}-h-${index}`)}</th>)}</tr></thead>
          <tbody>{body.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{inlineNodes(cell, inlineProps, `${blockKey}-${rowIndex}-${cellIndex}`)}</td>)}</tr>)}</tbody></table></div>
      );
      continue;
    }

    if (/^\s*[-+*]\s+/.test(line)) {
      const items: Array<{ text: string; checked: boolean | null }> = [];
      while (cursor < lines.length && /^\s*[-+*]\s+/.test(lines[cursor])) {
        const text = lines[cursor++].replace(/^\s*[-+*]\s+/, "");
        const task = /^\[([ xX])\]\s+(.*)$/.exec(text);
        items.push({ text: task ? task[2] : text, checked: task ? task[1].toLowerCase() === "x" : null });
      }
      blocks.push(<ul key={blockKey} className={items.some((item) => item.checked !== null) ? "doc-task-list" : undefined}>{items.map((item, index) => <li key={index}>{item.checked !== null ? <input type="checkbox" checked={item.checked} readOnly aria-label={item.checked ? "Выполнено" : "Не выполнено"} /> : null}{inlineNodes(item.text, inlineProps, `${blockKey}-${index}`)}</li>)}</ul>);
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (cursor < lines.length && /^\s*\d+[.)]\s+/.test(lines[cursor])) items.push(lines[cursor++].replace(/^\s*\d+[.)]\s+/, ""));
      blocks.push(<ol key={blockKey}>{items.map((item, index) => <li key={index}>{inlineNodes(item, inlineProps, `${blockKey}-${index}`)}</li>)}</ol>);
      continue;
    }

    if (/^\s*<[A-Za-z!/][^>]*>/.test(line)) {
      const html: string[] = [];
      while (cursor < lines.length && lines[cursor].trim()) html.push(lines[cursor++]);
      blocks.push(<pre key={blockKey} className="doc-inert-html" role="note"><code>{html.join("\n")}</code></pre>);
      continue;
    }

    const paragraph: string[] = [];
    while (cursor < lines.length && lines[cursor].trim()) {
      if (paragraph.length && (/^(#{1,6})\s+/.test(lines[cursor]) || /^```|^~~~|^>|^\s*[-+*]\s+|^\s*\d+[.)]\s+/.test(lines[cursor]))) break;
      paragraph.push(lines[cursor++]);
    }
    blocks.push(<p key={blockKey}>{inlineNodes(paragraph.join("\n"), inlineProps, blockKey)}</p>);
  }
  if (footnotes.definitions.length) {
    blocks.push(
      <section className="doc-footnotes" aria-label="Сноски" key={`footnotes-${key}`}>
        <h2>Сноски</h2>
        <ol>{footnotes.definitions.map((footnote, index) => (
          <li id={`doc-footnote-${headingId(footnote.id)}`} key={`${footnote.id}-${index}`}>
            {renderBlocks(footnote.content, inlineProps, `footnote-${index}`, depth + 1)}
          </li>
        ))}</ol>
      </section>
    );
  }
  return <div className="doc-markdown">
    {bounded.truncated ? <div className="doc-render-limit" role="status"><strong>Показана ограниченная часть большого документа</strong><span>Безопасный просмотр остановлен после {MAX_RENDER_CHARACTERS.toLocaleString("ru-RU")} символов или {MAX_RENDER_LINES.toLocaleString("ru-RU")} строк.</span>{onOpenSource ? <button type="button" onClick={onOpenSource}>Открыть Source mode</button> : null}</div> : null}
    {blocks}
  </div>;
}

function renderBlocks(
  content: string,
  props: Pick<SafeMarkdownViewProps, "onOpenWikilink" | "onOpenRelativeLink" | "resolveImage">,
  key: string,
  depth: number
): ReactNode {
  return <SafeMarkdownView key={key} content={content} depth={depth} {...props} />;
}

export { headingId, safeImageUrl, safeLink };
