import type { FrontmatterField, VisualQualification } from "./documentTypes";

export interface MarkdownIssue {
  code: string;
  message: string;
  severity: "warning" | "error";
  from: number;
  to: number;
}

export interface ProtectedMarkdownToken {
  token: string;
  source: string;
}

export interface VisualMarkdownEnvelope {
  original: string;
  editorMarkdown: string;
  frontmatter: string;
  lineEnding: "lf" | "crlf";
  finalNewline: boolean;
  serialization: VisualMarkdownSerializationProfile;
  protectedTokens: ProtectedMarkdownToken[];
  issues: MarkdownIssue[];
}

export interface VisualMarkdownSerializationProfile {
  stringify: {
    bullet?: "*" | "+" | "-";
    bulletOrdered?: "." | ")";
    listItemIndent?: "one" | "tab";
    rule?: "*" | "-" | "_";
    ruleRepetition?: number;
    ruleSpaces?: boolean;
  };
  gfm: {
    tableCellPadding?: boolean;
    tablePipeAlign?: boolean;
  };
}

const TOKEN_PREFIX = "\uE000RAYTSYSTEM";
const TOKEN_SUFFIX = "\uE001";
const MAX_VISUAL_EDITOR_CHARACTERS = 1_000_000;

function normalizedLineEnding(content: string): "lf" | "crlf" {
  return content.includes("\r\n") ? "crlf" : "lf";
}

function normalizeLf(content: string): string {
  return content.replace(/\r\n?/g, "\n");
}

function markdownLinesOutsideFences(body: string): string[] {
  const lines = body.split("\n");
  const visible: string[] = [];
  let fence: { marker: "`" | "~"; length: number } | null = null;
  for (const line of lines) {
    const candidate = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (fence) {
      if (candidate && candidate[1][0] === fence.marker && candidate[1].length >= fence.length) fence = null;
      continue;
    }
    if (candidate) {
      fence = { marker: candidate[1][0] as "`" | "~", length: candidate[1].length };
      continue;
    }
    visible.push(line);
  }
  return visible;
}

function thematicBreakStyle(line: string): { marker: "*" | "-" | "_"; repetition: number; spaces: boolean } | null {
  if (!/^ {0,3}[-*_][-*_ \t]*$/.test(line)) return null;
  const value = line.trim();
  const compact = value.replace(/[ \t]/g, "");
  if (!/^(?:\*{3,}|-{3,}|_{3,})$/.test(compact)) return null;
  return {
    marker: compact[0] as "*" | "-" | "_",
    repetition: compact.length,
    spaces: /[ \t]/.test(value)
  };
}

function splitSimpleTableRow(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.includes("|") || trimmed.includes("\\|")) return null;
  const withoutStart = trimmed.startsWith("|") ? trimmed.slice(1) : trimmed;
  const withoutEdges = withoutStart.endsWith("|") ? withoutStart.slice(0, -1) : withoutStart;
  const cells = withoutEdges.split("|");
  return cells.length > 1 ? cells : null;
}

function isTableDelimiter(cells: string[] | null): cells is string[] {
  return Boolean(cells?.length && cells.every((cell) => /^\s*:?-+:?\s*$/.test(cell)));
}

function tableSerializationStyle(lines: string[]): VisualMarkdownSerializationProfile["gfm"] {
  const tableRows: string[][] = [];
  const pipeLayouts: number[][][] = [];
  for (let index = 1; index < lines.length; index += 1) {
    if (!isTableDelimiter(splitSimpleTableRow(lines[index]))) continue;
    const rows: string[][] = [];
    const layouts: number[][] = [];
    const start = index - 1;
    let end = index;
    while (end + 1 < lines.length && splitSimpleTableRow(lines[end + 1])) end += 1;
    for (let cursor = start; cursor <= end; cursor += 1) {
      const cells = splitSimpleTableRow(lines[cursor]);
      if (!cells) continue;
      rows.push(cells);
      const positions = [...lines[cursor]].flatMap((character, position) => character === "|" ? [position] : []);
      layouts.push(positions);
    }
    tableRows.push(...rows);
    pipeLayouts.push(layouts);
    index = end;
  }
  if (!tableRows.length) return {};

  const nonemptyCells = tableRows.flat().filter((cell) => cell.length > 0);
  const unpadded = nonemptyCells.length > 0 && nonemptyCells.every((cell) => !/^\s|\s$/.test(cell));
  const padded = nonemptyCells.length > 0 && nonemptyCells.every((cell) => /^\s.*\s$/.test(cell));
  const aligned = pipeLayouts.every((layouts) => {
    if (layouts.length < 2) return false;
    const reference = layouts[0].join(",");
    return layouts.every((layout) => layout.join(",") === reference);
  });
  if (!unpadded && !padded) return {};
  return { tableCellPadding: padded, tablePipeAlign: aligned };
}

export function visualMarkdownSerializationProfile(body: string): VisualMarkdownSerializationProfile {
  const lines = markdownLinesOutsideFences(body);
  const stringify: VisualMarkdownSerializationProfile["stringify"] = {};

  const bullets = new Set<"*" | "+" | "-">();
  const orderedBullets = new Set<"." | ")">();
  const listIndents = new Set<"one" | "tab">();
  const rules: Array<ReturnType<typeof thematicBreakStyle>> = [];
  let inList = false;
  let previousListIndent: number | null = null;
  for (const line of lines) {
    const rule = thematicBreakStyle(line);
    if (rule) {
      rules.push(rule);
      inList = false;
      previousListIndent = null;
      continue;
    }
    const unordered = line.match(/^([ \t]*)([-*+])(?:[ \t]+|$)/);
    const ordered = line.match(/^([ \t]*)\d+([.)])(?:[ \t]+|$)/);
    if (unordered && (unordered[1].length <= 3 || inList)) {
      bullets.add(unordered[2] as "*" | "+" | "-");
      if (inList && unordered[1].length > 0) listIndents.add(unordered[1].includes("\t") || unordered[1].length >= 4 ? "tab" : "one");
      inList = true;
      previousListIndent = unordered[1].length;
      continue;
    }
    if (ordered && (ordered[1].length <= 3 || inList)) {
      orderedBullets.add(ordered[2] as "." | ")");
      if (inList && ordered[1].length > 0) listIndents.add(ordered[1].includes("\t") || ordered[1].length >= 4 ? "tab" : "one");
      inList = true;
      previousListIndent = ordered[1].length;
      continue;
    }
    if (previousListIndent !== null && line.trim()) {
      const leading = line.match(/^[ \t]*/)?.[0] ?? "";
      if (leading.length > previousListIndent) listIndents.add(leading.includes("\t") || leading.length - previousListIndent >= 4 ? "tab" : "one");
      if (!leading.length) {
        inList = false;
        previousListIndent = null;
      }
    }
  }
  if (bullets.size === 1) stringify.bullet = [...bullets][0];
  if (orderedBullets.size === 1) stringify.bulletOrdered = [...orderedBullets][0];
  if (listIndents.size === 1) stringify.listItemIndent = [...listIndents][0];
  if (rules.length) {
    const first = rules[0];
    if (first && rules.every((rule) => rule?.marker === first.marker && rule.repetition === first.repetition && rule.spaces === first.spaces)) {
      stringify.rule = first.marker;
      stringify.ruleRepetition = first.repetition;
      stringify.ruleSpaces = first.spaces;
    }
  }
  return { stringify, gfm: tableSerializationStyle(lines) };
}

function splitFrontmatter(content: string): { frontmatter: string; body: string } {
  if (!content.startsWith("---\n")) return { frontmatter: "", body: content };
  const end = content.indexOf("\n---", 4);
  if (end < 0) return { frontmatter: "", body: content };
  const delimiterEnd = end + 4;
  const hasBreak = content[delimiterEnd] === "\n";
  return {
    frontmatter: content.slice(0, delimiterEnd) + (hasBreak ? "\n" : ""),
    body: content.slice(delimiterEnd + (hasBreak ? 1 : 0))
  };
}

function issueForMatch(
  issues: MarkdownIssue[],
  content: string,
  regex: RegExp,
  code: string,
  message: string,
  severity: MarkdownIssue["severity"] = "error"
): void {
  regex.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(content)) !== null) {
    issues.push({ code, message, severity, from: match.index, to: match.index + Math.max(match[0].length, 1) });
    if (!regex.global) break;
  }
}

export function inspectMarkdownForVisualEditing(content: string): MarkdownIssue[] {
  const normalized = normalizeLf(content);
  const { body } = splitFrontmatter(normalized);
  const issues: MarkdownIssue[] = [];
  issueForMatch(issues, body, /(^|\n)\s*<\/?[A-Za-z][^>]*>/g, "html_fragment", "HTML-фрагмент требует Source mode.");
  issueForMatch(issues, body, /(^|\n)\s*:::[A-Za-z][^\n]*/g, "directive", "Неизвестная Markdown-директива требует Source mode.");
  issueForMatch(issues, body, /(^|\n)\s*\$\$[\s\S]*?\$\$/g, "math_block", "Математический блок не квалифицирован для визуального сохранения.");
  issueForMatch(issues, body, /(^|\n)[^\n]*\s\^[A-Za-z0-9-]+\s*$/g, "block_id", "Obsidian block ID требует Source mode.");
  issueForMatch(issues, body, /```(?:dataview|query|tasks)\b/gi, "executable_fence", "Исполняемое community-расширение доступно только как исходный Markdown.");
  issueForMatch(issues, body, /\{\{[^\n{}]+\}\}/g, "template_expression", "Шаблонное выражение не исполняется и требует Source mode.");
  return issues;
}

function protectAgentExtensions(body: string): { markdown: string; tokens: ProtectedMarkdownToken[] } {
  const tokens: ProtectedMarkdownToken[] = [];
  const protect = (source: string) => {
    const token = `${TOKEN_PREFIX}${tokens.length.toString(36)}${TOKEN_SUFFIX}`;
    tokens.push({ token, source });
    return token;
  };
  let markdown = body.replace(/!?\[\[[^\]\n]+\]\]/g, protect);
  markdown = markdown.replace(/\[![A-Za-z0-9_-]+\](?:[^\n]*)/g, protect);
  return { markdown, tokens };
}

export function registerProtectedVisualToken(envelope: VisualMarkdownEnvelope, source: string): string {
  const token = `${TOKEN_PREFIX}${envelope.protectedTokens.length.toString(36)}${TOKEN_SUFFIX}`;
  envelope.protectedTokens.push({ token, source });
  return token;
}

export function prepareVisualMarkdown(content: string): VisualMarkdownEnvelope {
  const lineEnding = normalizedLineEnding(content);
  const finalNewline = /(?:\r\n|\n)$/.test(content);
  const normalized = normalizeLf(content);
  const { frontmatter, body } = splitFrontmatter(normalized);
  const protectedBody = protectAgentExtensions(body);
  return {
    original: content,
    editorMarkdown: protectedBody.markdown,
    frontmatter,
    lineEnding,
    finalNewline,
    serialization: visualMarkdownSerializationProfile(body),
    protectedTokens: protectedBody.tokens,
    issues: inspectMarkdownForVisualEditing(content)
  };
}

export function restoreVisualMarkdown(
  editorMarkdown: string,
  envelope: VisualMarkdownEnvelope
): { content: string; issues: MarkdownIssue[] } {
  let body = normalizeLf(editorMarkdown);
  const issues = [...envelope.issues];
  for (const item of envelope.protectedTokens) {
    if (!body.includes(item.token)) {
      issues.push({
        code: "protected_token_lost",
        message: `Визуальный редактор изменил защищённую конструкцию: ${item.source}`,
        severity: "error",
        from: 0,
        to: 0
      });
      continue;
    }
    body = body.replaceAll(item.token, item.source);
  }
  body = body.replace(new RegExp(`${TOKEN_PREFIX}[^${TOKEN_SUFFIX}]*${TOKEN_SUFFIX}`, "g"), "");
  let normalized = envelope.frontmatter + body;
  normalized = normalized.replace(/\n+$/, "");
  if (envelope.finalNewline) normalized += "\n";
  const content = envelope.lineEnding === "crlf" ? normalized.replace(/\n/g, "\r\n") : normalized;
  return { content, issues };
}

export function qualificationIssues(
  content: string,
  serverQualification?: VisualQualification
): MarkdownIssue[] {
  const issues = inspectMarkdownForVisualEditing(content);
  for (const warning of serverQualification?.warnings ?? []) {
    issues.push({ code: "server_warning", message: warning, severity: "warning", from: 0, to: 0 });
  }
  for (const syntax of serverQualification?.unsupported_syntax ?? []) {
    issues.push({ code: "server_unsupported", message: `Не поддерживается визуальным редактором: ${syntax}`, severity: "error", from: 0, to: 0 });
  }
  if (serverQualification && (!serverQualification.can_save || !serverQualification.round_trip_safe)) {
    issues.push({ code: "server_round_trip_blocked", message: "Серверная round-trip квалификация запретила визуальное сохранение.", severity: "error", from: 0, to: 0 });
  }
  return issues;
}

export function visualEditorBlockReason(content: string, serverQualification?: VisualQualification): string | null {
  if (content.length > MAX_VISUAL_EDITOR_CHARACTERS) return "Документ превышает безопасный лимит визуального редактора. Используйте Source mode.";
  if (serverQualification?.can_open === false) return "Серверная квалификация запретила открывать этот документ в визуальном редакторе.";
  if (inspectMarkdownForVisualEditing(content).some((issue) => issue.severity === "error")) return "Документ содержит конструкции, которые нужно редактировать в Source mode.";
  return null;
}

function yamlScalar(value: FrontmatterField["value"]): string {
  if (Array.isArray(value)) return `[${value.map((item) => JSON.stringify(item)).join(", ")}]`;
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "string") {
    if (/^[A-Za-zА-Яа-яЁё0-9_. /-]+$/.test(value) && !/^(true|false|null|~|[-+]?\d+(?:\.\d+)?)$/i.test(value)) return value;
    return JSON.stringify(value);
  }
  return String(value);
}

export function updateFrontmatterField(
  content: string,
  field: FrontmatterField,
  value: FrontmatterField["value"]
): { content: string; warning: string | null } {
  if (!field.editable || field.type === "complex") return { content, warning: "Сложное YAML-поле можно менять только в Source mode." };
  const lineEnding = normalizedLineEnding(content) === "crlf" ? "\r\n" : "\n";
  const normalized = normalizeLf(content);
  const { frontmatter, body } = splitFrontmatter(normalized);
  if (!frontmatter) return { content, warning: "Документ не содержит YAML frontmatter." };
  const lines = frontmatter.replace(/\n$/, "").split("\n");
  const keyPattern = new RegExp(`^${field.key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}:`);
  const index = lines.findIndex((line) => keyPattern.test(line));
  if (index < 0) lines.splice(lines.length - 1, 0, `${field.key}: ${yamlScalar(value)}`);
  else {
    const existing = lines[index];
    if (/\s+#/.test(existing)) return { content, warning: "Поле содержит YAML-комментарий; измените его в Source mode, чтобы не потерять комментарий." };
    if (index + 1 < lines.length - 1 && /^\s+/.test(lines[index + 1])) return { content, warning: "Многострочное YAML-поле можно менять только в Source mode." };
    lines[index] = `${field.key}: ${yamlScalar(value)}`;
  }
  const result = `${lines.join("\n")}\n${body}`;
  return { content: lineEnding === "\r\n" ? result.replace(/\n/g, "\r\n") : result, warning: null };
}

function parseFrontmatterScalar(source: string): { value: FrontmatterField["value"]; type: FrontmatterField["type"] } {
  const trimmed = source.trim();
  if (trimmed === "true" || trimmed === "false") return { value: trimmed === "true", type: "boolean" };
  if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) return { value: Number(trimmed), type: "number" };
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return { value: trimmed, type: "date" };
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    const value = trimmed.slice(1, -1).split(",").map((item) => item.trim().replace(/^['"]|['"]$/g, "")).filter(Boolean);
    return { value, type: "list" };
  }
  return { value: trimmed.replace(/^(['"])(.*)\1$/, "$2"), type: "string" };
}

export function deriveFrontmatterFields(content: string): FrontmatterField[] {
  const normalized = normalizeLf(content);
  const { frontmatter } = splitFrontmatter(normalized);
  if (!frontmatter) return [];
  const lines = frontmatter.replace(/\n$/, "").split("\n").slice(1, -1);
  const fields: FrontmatterField[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim() || line.trimStart().startsWith("#") || /^\s/.test(line)) continue;
    const separator = line.indexOf(":");
    if (separator <= 0) continue;
    const key = line.slice(0, separator).trim();
    const raw = line.slice(separator + 1);
    const hasContinuation = index + 1 < lines.length && /^\s+/.test(lines[index + 1]);
    const hasComment = /\s+#/.test(raw);
    if (hasContinuation || hasComment || /[&*!]|[>|]$/.test(raw.trim())) {
      fields.push({ key, value: raw.trim(), type: "complex", editable: false, source: line });
      continue;
    }
    const parsed = parseFrontmatterScalar(raw);
    const type = key === "tags" && Array.isArray(parsed.value) ? "tags" : key === "aliases" && Array.isArray(parsed.value) ? "aliases" : parsed.type;
    fields.push({ key, value: parsed.value, type, editable: true, source: line });
  }
  return fields;
}
