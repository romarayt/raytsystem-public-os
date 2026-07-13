import { useEffect, useRef, useState } from "react";
import { EditorState, Transaction } from "@codemirror/state";
import { basicSetup } from "codemirror";
import { EditorView, keymap } from "@codemirror/view";
import { markdown } from "@codemirror/lang-markdown";
import { lintGutter, linter, type Diagnostic } from "@codemirror/lint";
import type { MarkdownIssue } from "./markdownCodec";

interface SourceEditorProps {
  value: string;
  readOnly: boolean;
  issues: MarkdownIssue[];
  lineNumbers?: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
  onToggleVisual: () => void;
}

function cspNonce(): string | null {
  return document.querySelector<HTMLMetaElement>('meta[name="raytsystem-csp-nonce"]')?.content || null;
}

export function sourceLineSeparator(value: string): "\n" | "\r\n" | null {
  const withoutCrlf = value.replace(/\r\n/g, "");
  const hasCrlf = withoutCrlf.length !== value.length;
  const hasLf = withoutCrlf.includes("\n");
  const hasLoneCr = withoutCrlf.includes("\r");
  if (hasLoneCr || (hasCrlf && hasLf)) return null;
  return hasCrlf ? "\r\n" : "\n";
}

function toDiagnostics(issues: MarkdownIssue[], length: number): Diagnostic[] {
  return issues.map((issue) => ({
    from: Math.max(0, Math.min(length, issue.from)),
    to: Math.max(0, Math.min(length, Math.max(issue.to, issue.from))),
    severity: issue.severity,
    source: "raytsystem Markdown",
    message: issue.message
  }));
}

export default function SourceEditor({
  value,
  readOnly,
  issues,
  lineNumbers = true,
  onChange,
  onSave,
  onToggleVisual
}: SourceEditorProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  const onToggleVisualRef = useRef(onToggleVisual);
  const issuesRef = useRef(issues);
  const nonce = cspNonce();
  const [lineSeparator] = useState(() => sourceLineSeparator(value));

  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);
  useEffect(() => { onSaveRef.current = onSave; }, [onSave]);
  useEffect(() => { onToggleVisualRef.current = onToggleVisual; }, [onToggleVisual]);
  useEffect(() => { issuesRef.current = issues; }, [issues]);

  useEffect(() => {
    if (!hostRef.current || !nonce || !lineSeparator) return;
    const extensions = [
      basicSetup,
      markdown(),
      lintGutter(),
      linter((view) => toDiagnostics(issuesRef.current, view.state.doc.length)),
      EditorState.lineSeparator.of(lineSeparator),
      EditorState.readOnly.of(readOnly),
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({
        "aria-label": "Исходный Markdown",
        "aria-multiline": "true",
        spellcheck: "true"
      }),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) onChangeRef.current(update.state.sliceDoc());
      }),
      keymap.of([
        { key: "Mod-s", preventDefault: true, run: () => { onSaveRef.current(); return true; } },
        { key: "Mod-Shift-m", preventDefault: true, run: () => { onToggleVisualRef.current(); return true; } }
      ])
    ];
    extensions.push(EditorView.cspNonce.of(nonce));
    const editor = new EditorView({ doc: value, extensions, parent: hostRef.current });
    editorRef.current = editor;
    return () => {
      editor.destroy();
      editorRef.current = null;
    };
  // The editor owns its live document. External replacements are synchronized below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineSeparator, nonce, readOnly]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const current = editor.state.sliceDoc();
    if (current === value) return;
    editor.dispatch({
      changes: { from: 0, to: editor.state.doc.length, insert: value },
      annotations: Transaction.addToHistory.of(false)
    });
  }, [value]);

  return nonce && lineSeparator
    ? <div className="doc-source-editor" data-editor-scope="source" data-line-numbers={lineNumbers ? "visible" : "hidden"} ref={hostRef} />
    : <div className="doc-visual-unavailable" role="alert"><strong>Source editor не открыт</strong><p>{!nonce ? "Страница не содержит CSP nonce локальной сессии. raytsystem не создаёт style sheet без разрешённого nonce." : "Документ содержит смешанные или одиночные CR line endings. Редактирование заблокировано, чтобы CodeMirror не нормализовал исходные bytes."}</p></div>;
}
