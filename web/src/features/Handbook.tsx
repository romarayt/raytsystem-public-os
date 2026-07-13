import { BookOpen, FileText, Hash, Search } from "lucide-react";
import { Fragment, type ReactNode, useMemo, useState } from "react";
import { useHandbook, useHandbookArticle } from "../hooks";
import { EmptyState, ErrorState, LoadingState } from "../components/StatePanel";
import type { HandbookArticleRef } from "../types";

const STATUS_LABEL: Record<string, string> = {
  stable: "стабильно",
  experimental: "эксперимент",
  disabled: "отключено",
  draft: "черновик"
};

function StatusTag({ status, generated }: { status: string; generated: boolean }) {
  if (generated) return <span className="hb-tag hb-tag-generated">генерируется</span>;
  if (!status) return null;
  return <span className={`hb-tag hb-tag-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

// A small, safe inline formatter: bold, inline code and links only. It builds
// React nodes (never raw HTML), so nothing from the document can inject markup.
function renderInline(text: string, onNavigate: (slug: string) => void): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(<Fragment key={key++}>{text.slice(last, match.index)}</Fragment>);
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key++}>{token.slice(1, -1)}</code>);
    } else {
      const label = token.slice(1, token.indexOf("]"));
      const url = token.slice(token.indexOf("(") + 1, -1);
      if (url.startsWith("/")) {
        nodes.push(
          <a key={key++} href={url} onClick={(event) => { event.preventDefault(); onNavigate(url); }}>{label}</a>
        );
      } else {
        nodes.push(
          <a key={key++} href={url} target="_blank" rel="noreferrer noopener">{label}</a>
        );
      }
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(<Fragment key={key}>{text.slice(last)}</Fragment>);
  return nodes;
}

// Block renderer for the safe Markdown subset the backend emits: headings,
// fenced code, blockquotes, ordered/unordered lists, GFM tables and paragraphs.
function renderMarkdown(markdown: string, onNavigate: (slug: string) => void): ReactNode[] {
  const lines = markdown.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) { code.push(lines[i]); i++; }
      i++;
      blocks.push(<pre key={key++} className="hb-code"><code>{code.join("\n")}</code></pre>);
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const text = heading[2].replace(/\s*\{#[^}]+\}\s*$/, "");
      const Tag = (`h${Math.min(level + 1, 5)}`) as "h2" | "h3" | "h4" | "h5";
      blocks.push(<Tag key={key++} className="hb-heading">{renderInline(text, onNavigate)}</Tag>);
      i++;
      continue;
    }

    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) { quote.push(lines[i].replace(/^>\s?/, "")); i++; }
      blocks.push(
        <blockquote key={key++} className="hb-quote">{renderMarkdown(quote.join("\n"), onNavigate)}</blockquote>
      );
      continue;
    }

    if (line.trimStart().startsWith("| ") && i + 1 < lines.length && /^[\s|:-]+$/.test(lines[i + 1])) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].trimStart().startsWith("|")) { rows.push(lines[i]); i++; }
      const cells = (row: string) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const header = cells(rows[0]);
      const body = rows.slice(2).map(cells);
      blocks.push(
        <div key={key++} className="hb-table-wrap">
          <table className="hb-table">
            <thead><tr>{header.map((c, ci) => <th key={ci}>{renderInline(c, onNavigate)}</th>)}</tr></thead>
            <tbody>{body.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{renderInline(c, onNavigate)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      );
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i++; }
      blocks.push(<ul key={key++} className="hb-list">{items.map((it, ii) => <li key={ii}>{renderInline(it, onNavigate)}</li>)}</ul>);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+\.\s+/, "")); i++; }
      blocks.push(<ol key={key++} className="hb-list">{items.map((it, ii) => <li key={ii}>{renderInline(it, onNavigate)}</li>)}</ol>);
      continue;
    }

    const para: string[] = [];
    while (
      i < lines.length && lines[i].trim() && !lines[i].startsWith("#") && !lines[i].startsWith("```") &&
      !lines[i].startsWith(">") && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i]) &&
      !lines[i].trimStart().startsWith("|")
    ) { para.push(lines[i]); i++; }
    blocks.push(<p key={key++} className="hb-p">{renderInline(para.join(" "), onNavigate)}</p>);
  }
  return blocks;
}

export function Handbook() {
  const tree = useHandbook();
  const [slug, setSlug] = useState<string>("/");
  const [query, setQuery] = useState("");
  const article = useHandbookArticle(slug);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("ru-RU");
    if (!needle) return tree.data?.sections ?? [];
    return (tree.data?.sections ?? [])
      .map((section) => ({
        ...section,
        articles: section.articles.filter((a) => a.title.toLocaleLowerCase("ru-RU").includes(needle))
      }))
      .filter((section) => section.articles.length > 0 || section.label.toLocaleLowerCase("ru-RU").includes(needle));
  }, [query, tree.data?.sections]);

  if (tree.isLoading) return <LoadingState label="Читаем базу знаний…" />;
  if (tree.isError || !tree.data) return <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />;
  if (!tree.data.available) {
    return (
      <div className="route route-list">
        <EmptyState title="База знаний недоступна">
          Каталог документации <code>website/docs</code> не найден рядом с установкой raytsystem.
        </EmptyState>
      </div>
    );
  }

  const rootArticle = tree.data.root_articles.find((a) => a.slug === "/");
  const navigate = (target: string) => setSlug(target);

  const renderNavItem = (a: HandbookArticleRef) => (
    <button
      key={a.slug}
      type="button"
      className={`hb-nav-item ${slug === a.slug ? "active" : ""}`}
      onClick={() => setSlug(a.slug)}
      aria-current={slug === a.slug ? "page" : undefined}
    >
      <FileText size={14} aria-hidden="true" />
      <span>{a.title}</span>
      <StatusTag status={a.status} generated={a.generated} />
    </button>
  );

  return (
    <div className="route handbook-route">
      <aside className="hb-sidebar" aria-label="Разделы базы знаний">
        <div className="hb-search">
          <Search size={15} aria-hidden="true" />
          <input
            aria-label="Поиск по базе знаний"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Поиск статьи"
          />
        </div>
        <nav className="hb-nav">
          {rootArticle ? renderNavItem(rootArticle) : null}
          {filtered.map((section) => (
            <div className="hb-nav-group" key={section.id}>
              <span className="hb-nav-label"><Hash size={12} aria-hidden="true" /> {section.label}</span>
              {section.articles.map(renderNavItem)}
            </div>
          ))}
        </nav>
      </aside>
      <article className="hb-article panel" aria-live="polite">
        {article.isLoading ? (
          <LoadingState label="Открываем статью…" />
        ) : article.isError || !article.data ? (
          <ErrorState error={article.error} onRetry={() => void article.refetch()} />
        ) : (
          <>
            <header className="hb-article-head">
              <div className="hb-eyebrow"><BookOpen size={14} aria-hidden="true" /> База знаний raytsystem</div>
              <div className="hb-article-title">
                <StatusTag status={article.data.status} generated={article.data.generated} />
              </div>
            </header>
            <div className="hb-content">{renderMarkdown(article.data.markdown, navigate)}</div>
          </>
        )}
      </article>
    </div>
  );
}
