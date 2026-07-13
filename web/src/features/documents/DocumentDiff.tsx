import { Check, Copy, FileDiff } from "lucide-react";
import { useState } from "react";

const MAX_RENDERED_DIFF_LINES = 4_000;
const MAX_SOURCE_CHARACTERS = 200_000;
const MAX_DIFF_INPUT_CHARACTERS = 1_000_000;
const MAX_DIFF_INPUT_LINES = 20_000;
const SAMPLE_LINES = 120;

export interface DiffLine {
  kind: "context" | "added" | "removed";
  text: string;
  oldLine: number | null;
  newLine: number | null;
}

export interface DiffResult {
  lines: DiffLine[];
  added: number;
  removed: number;
  totalLines: number;
  truncated: boolean;
  coarse: boolean;
}

function splitLines(content: string): string[] {
  return content.replace(/\r\n?/g, "\n").split("\n");
}

function coarseDiff(before: string[], after: string[]): DiffLine[] {
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (suffix < before.length - prefix && suffix < after.length - prefix && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix += 1;
  const result: DiffLine[] = [];
  for (let index = 0; index < prefix; index += 1) result.push({ kind: "context", text: before[index], oldLine: index + 1, newLine: index + 1 });
  for (let index = prefix; index < before.length - suffix; index += 1) result.push({ kind: "removed", text: before[index], oldLine: index + 1, newLine: null });
  for (let index = prefix; index < after.length - suffix; index += 1) result.push({ kind: "added", text: after[index], oldLine: null, newLine: index + 1 });
  for (let index = suffix - 1; index >= 0; index -= 1) {
    const oldIndex = before.length - 1 - index;
    const newIndex = after.length - 1 - index;
    result.push({ kind: "context", text: before[oldIndex], oldLine: oldIndex + 1, newLine: newIndex + 1 });
  }
  return result;
}

function countLines(content: string): number {
  let lines = 1;
  for (let index = 0; index < content.length; index += 1) if (content.charCodeAt(index) === 10) lines += 1;
  return lines;
}

function sampledLines(content: string, fromEnd = false): string[] {
  const sampleCharacters = 48_000;
  const fragment = fromEnd ? content.slice(-sampleCharacters) : content.slice(0, sampleCharacters);
  const lines = splitLines(fragment);
  return fromEnd ? lines.slice(-SAMPLE_LINES) : lines.slice(0, SAMPLE_LINES);
}

export function computeDiff(beforeContent: string, afterContent: string): DiffResult {
  const beforeLineCount = countLines(beforeContent);
  const afterLineCount = beforeContent === afterContent ? beforeLineCount : countLines(afterContent);
  if (beforeContent === afterContent) {
    const bounded = beforeContent.length > MAX_DIFF_INPUT_CHARACTERS || beforeLineCount > MAX_DIFF_INPUT_LINES;
    const all = bounded ? [...sampledLines(beforeContent), ...sampledLines(beforeContent, true)] : splitLines(beforeContent);
    const lines = all.slice(0, MAX_RENDERED_DIFF_LINES).map((text, index) => ({ kind: "context" as const, text, oldLine: index + 1, newLine: index + 1 }));
    return { lines, added: 0, removed: 0, totalLines: beforeLineCount, truncated: bounded, coarse: bounded };
  }
  if (beforeContent.length + afterContent.length > MAX_DIFF_INPUT_CHARACTERS || beforeLineCount + afterLineCount > MAX_DIFF_INPUT_LINES) {
    const beforeHead = sampledLines(beforeContent);
    const afterHead = sampledLines(afterContent);
    const beforeTail = sampledLines(beforeContent, true);
    const afterTail = sampledLines(afterContent, true);
    const lines: DiffLine[] = [
      ...beforeHead.map((text, index) => ({ kind: "removed" as const, text, oldLine: index + 1, newLine: null })),
      ...afterHead.map((text, index) => ({ kind: "added" as const, text, oldLine: null, newLine: index + 1 })),
      ...beforeTail.map((text, index) => ({ kind: "removed" as const, text, oldLine: Math.max(1, beforeLineCount - beforeTail.length + index + 1), newLine: null })),
      ...afterTail.map((text, index) => ({ kind: "added" as const, text, oldLine: null, newLine: Math.max(1, afterLineCount - afterTail.length + index + 1) }))
    ];
    return { lines, added: afterLineCount, removed: beforeLineCount, totalLines: beforeLineCount + afterLineCount, truncated: true, coarse: true };
  }
  const before = splitLines(beforeContent);
  const after = splitLines(afterContent);
  if (before.length * after.length > 200_000) {
    const lines = coarseDiff(before, after);
    return { lines, added: lines.filter((line) => line.kind === "added").length, removed: lines.filter((line) => line.kind === "removed").length, totalLines: lines.length, truncated: false, coarse: true };
  }
  const widths = after.length + 1;
  const table = new Uint32Array((before.length + 1) * widths);
  for (let left = before.length - 1; left >= 0; left -= 1) {
    for (let right = after.length - 1; right >= 0; right -= 1) {
      table[left * widths + right] = before[left] === after[right]
        ? table[(left + 1) * widths + right + 1] + 1
        : Math.max(table[(left + 1) * widths + right], table[left * widths + right + 1]);
    }
  }
  const result: DiffLine[] = [];
  let left = 0;
  let right = 0;
  while (left < before.length || right < after.length) {
    if (left < before.length && right < after.length && before[left] === after[right]) {
      result.push({ kind: "context", text: before[left], oldLine: ++left, newLine: ++right });
    } else if (right < after.length && (left >= before.length || table[left * widths + right + 1] >= table[(left + 1) * widths + right])) {
      result.push({ kind: "added", text: after[right], oldLine: null, newLine: ++right });
    } else {
      result.push({ kind: "removed", text: before[left], oldLine: ++left, newLine: null });
    }
  }
  let added = 0;
  let removed = 0;
  for (const line of result) {
    if (line.kind === "added") added += 1;
    else if (line.kind === "removed") removed += 1;
  }
  return { lines: result, added, removed, totalLines: result.length, truncated: false, coarse: false };
}

export function diffLines(beforeContent: string, afterContent: string): DiffLine[] {
  return computeDiff(beforeContent, afterContent).lines;
}

interface DocumentDiffProps {
  original: string;
  current: string;
  disk?: string | null;
}

function SourcePane({ title, content }: { title: string; content: string }) {
  const truncated = content.length > MAX_SOURCE_CHARACTERS;
  const rendered = truncated ? content.slice(0, MAX_SOURCE_CHARACTERS) : content;
  return <section className="doc-diff-source"><header>{title}{truncated ? <span>показаны первые {MAX_SOURCE_CHARACTERS.toLocaleString("ru-RU")} символов</span> : null}</header><pre><code>{rendered}</code></pre></section>;
}

export function DocumentDiff({ original, current, disk }: DocumentDiffProps) {
  const [copied, setCopied] = useState(false);
  const result = computeDiff(original, current);
  const diff = result.lines;
  const { added, removed } = result;
  const truncated = result.truncated || diff.length > MAX_RENDERED_DIFF_LINES;
  const half = MAX_RENDERED_DIFF_LINES / 2;
  const renderedDiff = truncated ? [...diff.slice(0, half), ...diff.slice(-half)] : diff;
  const sourceBudgetExceeded = original.length + current.length + (disk?.length ?? 0) > MAX_SOURCE_CHARACTERS * 3;
  return (
    <div className="doc-diff" aria-label="Изменения Markdown">
      <header className="doc-diff-summary"><FileDiff size={17} aria-hidden="true" /><strong>Локальные изменения</strong><span className="added">+{added}</span><span className="removed">−{removed}</span><button type="button" onClick={() => void navigator.clipboard.writeText(original).then(() => setCopied(true))}>{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Скопировано" : "Копировать исходную версию"}</button></header>
      {disk !== undefined && disk !== null && disk !== original && !sourceBudgetExceeded ? <div className="doc-diff-three"><SourcePane title="При открытии" content={original} /><SourcePane title="Сейчас на диске" content={disk} /><SourcePane title="Версия пользователя" content={current} /></div> : sourceBudgetExceeded ? <p className="doc-diff-truncated" role="status">Три полных версии не отрисованы одновременно: документ превышает безопасный UI-бюджет. Построчный diff ниже ограничен, исходники доступны по отдельности.</p> : null}
      {truncated ? <p className="doc-diff-truncated" role="status">Показан bounded sample вместо полного diff: вход содержит {result.totalLines.toLocaleString("ru-RU")} строк. Счётчики для large-file режима являются консервативной оценкой.</p> : null}
      <div className="doc-diff-lines" role="table" aria-label="Построчный diff">
        {renderedDiff.map((line, index) => (
          <div className={`doc-diff-line ${line.kind}`} role="row" key={`${index}:${line.kind}`}>
            <span role="cell">{line.oldLine ?? ""}</span><span role="cell">{line.newLine ?? ""}</span><b role="cell" aria-label={line.kind === "added" ? "Добавлено" : line.kind === "removed" ? "Удалено" : "Без изменений"}>{line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " "}</b><code role="cell">{line.text || " "}</code>
          </div>
        ))}
      </div>
      <SourcePane title="Итоговый Markdown" content={current} />
    </div>
  );
}
