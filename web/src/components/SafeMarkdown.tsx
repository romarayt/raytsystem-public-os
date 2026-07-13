import { Fragment, type ReactNode } from "react";

interface SafeMarkdownProps {
  content: string;
}

const MAX_BLOCKQUOTE_DEPTH = 16;
const MAX_RENDER_NODES = 4_096;
const MAX_TABLE_ROWS = 128;
const MAX_TABLE_CELLS = 1_024;
const MAX_TABLE_COLUMNS = 64;

interface RenderBudget {
  nodes: number;
  tableRows: number;
  tableCells: number;
  truncated: boolean;
}

function consumeNode(budget: RenderBudget, count = 1): boolean {
  if (budget.nodes + count > MAX_RENDER_NODES) {
    budget.truncated = true;
    return false;
  }
  budget.nodes += count;
  return true;
}

function safeHref(value: string): string | null {
  const href = value.trim();
  if (href.startsWith("//")) return null;
  if (href.startsWith("#") || href.startsWith("/") || href.startsWith("./") || href.startsWith("../")) {
    return href;
  }
  try {
    const parsed = new URL(href);
    return parsed.protocol === "https:" || parsed.protocol === "http:" || parsed.protocol === "mailto:"
      ? href
      : null;
  } catch {
    return null;
  }
}

function renderInline(text: string, prefix: string, budget: RenderBudget): ReactNode[] {
  const nodes: ReactNode[] = [];
  const tokenPattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^)\n]+\))/g;
  let cursor = 0;
  let index = 0;
  let match: RegExpExecArray | null;
  while ((match = tokenPattern.exec(text)) !== null) {
    if (match.index > cursor) {
      if (!consumeNode(budget)) return nodes;
      nodes.push(<Fragment key={`${prefix}-text-${index++}`}>{text.slice(cursor, match.index)}</Fragment>);
    }
    const token = match[0];
    if (!consumeNode(budget)) return nodes;
    if (token.startsWith("**")) {
      nodes.push(<strong key={`${prefix}-strong-${index++}`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={`${prefix}-code-${index++}`}>{token.slice(1, -1)}</code>);
    } else {
      const closingBracket = token.indexOf("]");
      const label = token.slice(1, closingBracket);
      const rawHref = token.slice(closingBracket + 2, -1);
      const href = safeHref(rawHref);
      if (href === null) {
        nodes.push(<span className="unsafe-markdown-link" key={`${prefix}-unsafe-${index++}`}>{label}</span>);
      } else {
        const external = /^https?:/i.test(href) || /^mailto:/i.test(href);
        nodes.push(
          <a
            href={href}
            key={`${prefix}-link-${index++}`}
            rel={external ? "noreferrer noopener" : undefined}
            target={external ? "_blank" : undefined}
          >
            {label}
          </a>
        );
      }
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) {
    if (!consumeNode(budget)) return nodes;
    nodes.push(<Fragment key={`${prefix}-tail-${index}`}>{text.slice(cursor)}</Fragment>);
  }
  return nodes;
}

function isTableDivider(line: string): boolean {
  let valid = true;
  const count = visitTableCells(line, (cell) => {
    if (!/^:?-{3,}:?$/.test(cell)) valid = false;
    return valid;
  });
  return count > 0 && valid;
}

function visitTableCells(line: string, visitor: (cell: string, index: number) => boolean): number {
  const trimmed = line.trim();
  let start = trimmed.startsWith("|") ? 1 : 0;
  const end = trimmed.endsWith("|") ? trimmed.length - 1 : trimmed.length;
  let count = 0;

  while (start < end) {
    const separator = trimmed.indexOf("|", start);
    const cellEnd = separator < 0 || separator >= end ? end : separator;
    const shouldContinue = visitor(trimmed.slice(start, cellEnd).trim(), count);
    count += 1;
    if (!shouldContinue || cellEnd === end) break;
    start = cellEnd + 1;
  }
  return count;
}

function boundedTableCells(line: string, limit: number): { cells: string[]; truncated: boolean } {
  const cells: string[] = [];
  let truncated = false;
  visitTableCells(line, (cell, index) => {
    if (index >= limit) {
      truncated = true;
      return false;
    }
    cells.push(cell);
    return true;
  });
  return { cells, truncated };
}

function startsBlock(lines: string[], index: number): boolean {
  const line = lines[index] ?? "";
  return (
    /^\s*```/.test(line) ||
    /^#{1,6}\s+/.test(line) ||
    /^\s*>/.test(line) ||
    /^\s*[-*+]\s+/.test(line) ||
    /^\s*\d+[.)]\s+/.test(line) ||
    (/\|/.test(line) && isTableDivider(lines[index + 1] ?? ""))
  );
}

function renderBlocks(
  content: string,
  budget: RenderBudget,
  keyPrefix = "md",
  blockquoteDepth = 0,
): ReactNode[] {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  let block = 0;

  if (lines[0]?.trim() === "---") {
    const closing = lines.slice(1).findIndex((line) => line.trim() === "---");
    if (closing >= 0) {
      const end = closing + 1;
      if (consumeNode(budget, 2)) {
        blocks.push(
          <pre className="skill-markdown-frontmatter" key={`${keyPrefix}-frontmatter`}>
            <code>{lines.slice(0, end + 1).join("\n")}</code>
          </pre>
        );
      }
      index = end + 1;
    }
  }

  while (index < lines.length) {
    if (budget.nodes >= MAX_RENDER_NODES) {
      budget.truncated = true;
      break;
    }
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = /^\s*```([^\s`]*)\s*$/.exec(line);
    if (fence) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      if (consumeNode(budget, 2)) {
        blocks.push(
          <pre className="skill-markdown-code" key={`${keyPrefix}-code-${block++}`}>
            <code data-language={fence[1] || undefined}>{code.join("\n")}</code>
          </pre>
        );
      }
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      if (consumeNode(budget)) {
        blocks.push(<Tag key={`${keyPrefix}-heading-${block}`}>{renderInline(heading[2], `${keyPrefix}-heading-${block++}`, budget)}</Tag>);
      }
      index += 1;
      continue;
    }

    if (/^\s*>/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      const quoteKey = `${keyPrefix}-quote-${block++}`;
      const quoteContent = quote.join("\n");
      if (consumeNode(budget)) {
        blocks.push(
          <blockquote key={quoteKey}>
            {blockquoteDepth + 1 >= MAX_BLOCKQUOTE_DEPTH
              ? consumeNode(budget)
                ? <p>{renderInline(quoteContent, `${quoteKey}-bounded`, budget)}</p>
                : null
              : renderBlocks(quoteContent, budget, quoteKey, blockquoteDepth + 1)}
          </blockquote>
        );
      }
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*+]\s+/, ""));
        index += 1;
      }
      if (consumeNode(budget)) {
        const renderedItems: ReactNode[] = [];
        for (const [itemIndex, item] of items.entries()) {
          if (!consumeNode(budget)) break;
          renderedItems.push(<li key={itemIndex}>{renderInline(item, `${keyPrefix}-ul-${block}-${itemIndex}`, budget)}</li>);
        }
        blocks.push(<ul key={`${keyPrefix}-ul-${block}`}>{renderedItems}</ul>);
      }
      block += 1;
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ""));
        index += 1;
      }
      if (consumeNode(budget)) {
        const renderedItems: ReactNode[] = [];
        for (const [itemIndex, item] of items.entries()) {
          if (!consumeNode(budget)) break;
          renderedItems.push(<li key={itemIndex}>{renderInline(item, `${keyPrefix}-ol-${block}-${itemIndex}`, budget)}</li>);
        }
        blocks.push(<ol key={`${keyPrefix}-ol-${block}`}>{renderedItems}</ol>);
      }
      block += 1;
      continue;
    }

    if (/\|/.test(line) && isTableDivider(lines[index + 1] ?? "")) {
      const header = boundedTableCells(line, MAX_TABLE_COLUMNS);
      if (header.truncated) budget.truncated = true;
      index += 2;
      const headerNodes: ReactNode[] = [];
      const rowNodes: ReactNode[] = [];
      if (consumeNode(budget, 5)) {
        for (const [cellIndex, cell] of header.cells.entries()) {
          if (budget.tableCells >= MAX_TABLE_CELLS || !consumeNode(budget)) {
            budget.truncated = true;
            break;
          }
          budget.tableCells += 1;
          headerNodes.push(<th key={cellIndex}>{renderInline(cell, `${keyPrefix}-th-${block}-${cellIndex}`, budget)}</th>);
        }

        while (index < lines.length && lines[index].trim() && /\|/.test(lines[index])) {
          if (
            budget.tableRows >= MAX_TABLE_ROWS ||
            budget.tableCells >= MAX_TABLE_CELLS ||
            budget.nodes >= MAX_RENDER_NODES
          ) {
            budget.truncated = true;
            index += 1;
            continue;
          }
          const row = boundedTableCells(lines[index], Math.min(MAX_TABLE_COLUMNS, header.cells.length));
          if (row.truncated) budget.truncated = true;
          index += 1;
          if (!consumeNode(budget)) break;
          budget.tableRows += 1;
          const cells: ReactNode[] = [];
          for (const [cellIndex, cell] of row.cells.entries()) {
            if (budget.tableCells >= MAX_TABLE_CELLS || !consumeNode(budget)) {
              budget.truncated = true;
              break;
            }
            budget.tableCells += 1;
            cells.push(<td key={cellIndex}>{renderInline(cell, `${keyPrefix}-td-${block}-${budget.tableRows}-${cellIndex}`, budget)}</td>);
          }
          rowNodes.push(<tr key={budget.tableRows}>{cells}</tr>);
        }
        blocks.push(
          <div className="skill-markdown-table-wrap" key={`${keyPrefix}-table-${block}`}>
            <table>
              <thead><tr>{headerNodes}</tr></thead>
              <tbody>{rowNodes}</tbody>
            </table>
          </div>
        );
      }
      block += 1;
      continue;
    }

    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !startsBlock(lines, index)) {
      paragraph.push(lines[index]);
      index += 1;
    }
    if (paragraph.length === 0) {
      paragraph.push(line);
      index += 1;
    }
    if (consumeNode(budget)) {
      blocks.push(<p key={`${keyPrefix}-p-${block}`}>{renderInline(paragraph.join("\n"), `${keyPrefix}-p-${block++}`, budget)}</p>);
    }
  }
  return blocks;
}

/** Render the inert, allowlisted Markdown subset used by SKILL.md documents. */
export function SafeMarkdown({ content }: SafeMarkdownProps) {
  const budget: RenderBudget = { nodes: 0, tableRows: 0, tableCells: 0, truncated: false };
  const blocks = renderBlocks(content, budget);
  return (
    <article className="safe-markdown">
      {blocks}
      {budget.truncated ? (
        <p className="safe-markdown-truncated" role="status">
          Предпросмотр сокращён: документ превышает безопасный лимит отображения.
        </p>
      ) : null}
    </article>
  );
}
