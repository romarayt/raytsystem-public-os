import {
  Bold,
  Brackets,
  Code2,
  FileCode2,
  Heading2,
  Image,
  Italic,
  Link2,
  List,
  ListChecks,
  ListOrdered,
  MessageSquareText,
  Minus,
  Quote,
  Redo2,
  Strikethrough,
  Table2,
  Undo2
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Tooltip } from "../../components/Tooltip";
import {
  commandsCtx,
  defaultValueCtx,
  Editor,
  editorViewCtx,
  editorViewOptionsCtx,
  remarkStringifyOptionsCtx,
  rootAttrsCtx,
  rootCtx,
  serializerCtx
} from "@milkdown/kit/core";
import {
  commonmark,
  createCodeBlockCommand,
  insertHrCommand,
  toggleEmphasisCommand,
  toggleInlineCodeCommand,
  toggleLinkCommand,
  toggleStrongCommand,
  wrapInBlockquoteCommand,
  wrapInBulletListCommand,
  wrapInHeadingCommand,
  wrapInOrderedListCommand
} from "@milkdown/kit/preset/commonmark";
import { gfm, insertTableCommand, remarkGFMPlugin, toggleStrikethroughCommand } from "@milkdown/kit/preset/gfm";
import { history, redoCommand, undoCommand } from "@milkdown/kit/plugin/history";
import { listener, listenerCtx } from "@milkdown/kit/plugin/listener";
import { clipboard } from "@milkdown/kit/plugin/clipboard";
import type { VisualQualification } from "./documentTypes";
import {
  prepareVisualMarkdown,
  qualificationIssues,
  registerProtectedVisualToken,
  restoreVisualMarkdown,
  type MarkdownIssue,
  type VisualMarkdownEnvelope
} from "./markdownCodec";
import { safeLink } from "./SafeMarkdownView";

interface VisualEditorProps {
  value: string;
  readOnly: boolean;
  qualification?: VisualQualification;
  onChange: (value: string, issues: MarkdownIssue[]) => void;
  onSave: () => void;
  onToggleSource: () => void;
}

interface ToolbarButtonProps {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}

function ToolbarButton({ label, disabled, onClick, children }: ToolbarButtonProps) {
  return <Tooltip content={label}><button type="button" aria-label={label} disabled={disabled} onMouseDown={(event) => event.preventDefault()} onClick={onClick}>{children}</button></Tooltip>;
}

function issueKey(issue: MarkdownIssue, index: number): string {
  return `${issue.code}:${issue.from}:${index}`;
}

export default function VisualEditor({
  value,
  readOnly,
  qualification,
  onChange,
  onSave,
  onToggleSource
}: VisualEditorProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [editorInstance, setEditorInstance] = useState<Editor | null>(null);
  const envelopeRef = useRef<VisualMarkdownEnvelope>(prepareVisualMarkdown(value));
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  const [runtimeIssues, setRuntimeIssues] = useState<MarkdownIssue[]>(() => qualificationIssues(value, qualification));
  const clientIssues = useMemo(() => qualificationIssues(value, qualification), [qualification, value]);
  const blocked = readOnly || runtimeIssues.some((issue) => issue.severity === "error") || clientIssues.some((issue) => issue.severity === "error");
  const blockedRef = useRef(blocked);

  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);
  useEffect(() => { onSaveRef.current = onSave; }, [onSave]);
  useEffect(() => { blockedRef.current = blocked; }, [blocked]);

  const insertText = (text: string) => {
    editorInstance?.action((ctx) => {
      const view = ctx.get(editorViewCtx);
      view.dispatch(view.state.tr.insertText(text));
      view.focus();
    });
  };

  const insertAgentExtension = (source: string) => {
    insertText(registerProtectedVisualToken(envelopeRef.current, source));
  };

  const requestLink = () => {
    const href = window.prompt("URL ссылки");
    if (!href || !safeLink(href)) return;
    editorInstance?.action((ctx) => ctx.get(commandsCtx).call(toggleLinkCommand.key, { href }));
  };

  useEffect(() => {
    if (!rootRef.current) return;
    let disposed = false;
    const envelope = envelopeRef.current;
    const initialIssues = qualificationIssues(envelope.original, qualification);
    const editor = Editor.make()
      .config((ctx) => {
        ctx.set(rootCtx, rootRef.current);
        ctx.set(rootAttrsCtx, { class: "doc-milkdown-root", "aria-label": "Визуальный Markdown-редактор" });
        ctx.set(defaultValueCtx, envelope.editorMarkdown);
        ctx.update(remarkStringifyOptionsCtx, (previous) => ({ ...previous, ...envelope.serialization.stringify }));
        ctx.set(remarkGFMPlugin.options.key, envelope.serialization.gfm);
        ctx.update(editorViewOptionsCtx, (previous) => ({
          ...previous,
          editable: () => !blockedRef.current,
          attributes: { ...previous.attributes, "aria-label": "Визуальный Markdown-редактор", role: "textbox", "aria-multiline": "true" }
        }));
        ctx.get(listenerCtx).markdownUpdated((_listenerCtx, markdown) => {
          if (disposed) return;
          const restored = restoreVisualMarkdown(markdown, envelope);
          const issues = [...initialIssues, ...restored.issues.filter((issue) => !initialIssues.some((known) => known.code === issue.code && known.from === issue.from))];
          setRuntimeIssues(issues);
          onChangeRef.current(restored.content, issues);
        });
      })
      .use(commonmark)
      .use(gfm)
      .use(history)
      .use(clipboard)
      .use(listener);
    void editor.create().then(() => {
      if (disposed) {
        void editor.destroy();
        return;
      }
      setEditorInstance(editor);
      const serialized = editor.action((ctx) => ctx.get(serializerCtx)(ctx.get(editorViewCtx).state.doc));
      const restored = restoreVisualMarkdown(serialized, envelope);
      if (restored.content !== envelope.original) {
        const issue: MarkdownIssue = {
          code: "client_round_trip_changed",
          message: "Milkdown изменяет этот документ даже без правок. Визуальное сохранение заблокировано; используйте Source mode.",
          severity: "error",
          from: 0,
          to: 0
        };
        const issues = [...initialIssues, issue];
        setRuntimeIssues(issues);
        onChangeRef.current(envelope.original, issues);
        blockedRef.current = true;
      }
    }).catch(() => {
      if (disposed) return;
      const issue: MarkdownIssue = {
        code: "visual_editor_failed",
        message: "Визуальный редактор не смог открыть документ. Исходный Markdown не изменён.",
        severity: "error",
        from: 0,
        to: 0
      };
      blockedRef.current = true;
      setEditorInstance(null);
      const issues = [...initialIssues, issue];
      setRuntimeIssues(issues);
      onChangeRef.current(envelope.original, issues);
    });
    return () => {
      disposed = true;
      void editor.destroy();
    };
  // Recreated by the parent's document key. Keeping this lifecycle stable avoids replacing a live draft.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const commandDisabled = blocked || !editorInstance;
  const command = (run: (editor: Editor) => void) => () => {
    if (editorInstance && !blockedRef.current) run(editorInstance);
  };

  const onKeyDownCapture = (event: React.KeyboardEvent) => {
    if (!(event.metaKey || event.ctrlKey)) return;
    const key = event.key.toLowerCase();
    if (key === "s") {
      event.preventDefault();
      event.stopPropagation();
      if (!blockedRef.current) onSaveRef.current();
    } else if (key === "k") {
      event.preventDefault();
      event.stopPropagation();
      if (!blockedRef.current) requestLink();
    } else if (event.shiftKey && key === "m") {
      event.preventDefault();
      event.stopPropagation();
      onToggleSource();
    }
  };

  return (
    <div className="doc-visual-editor" data-editor-scope="visual" onKeyDownCapture={onKeyDownCapture}>
      <div className="doc-editor-toolbar" role="toolbar" aria-label="Форматирование Markdown">
        <ToolbarButton label="Отменить" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(undoCommand.key)))}><Undo2 size={15} /></ToolbarButton>
        <ToolbarButton label="Повторить" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(redoCommand.key)))}><Redo2 size={15} /></ToolbarButton>
        <span aria-hidden="true" />
        <ToolbarButton label="Заголовок второго уровня" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(wrapInHeadingCommand.key, 2)))}><Heading2 size={15} /></ToolbarButton>
        <ToolbarButton label="Жирный" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(toggleStrongCommand.key)))}><Bold size={15} /></ToolbarButton>
        <ToolbarButton label="Курсив" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(toggleEmphasisCommand.key)))}><Italic size={15} /></ToolbarButton>
        <ToolbarButton label="Зачёркнутый" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(toggleStrikethroughCommand.key)))}><Strikethrough size={15} /></ToolbarButton>
        <ToolbarButton label="Ссылка" disabled={commandDisabled} onClick={requestLink}><Link2 size={15} /></ToolbarButton>
        <ToolbarButton label="Wikilink" disabled={commandDisabled} onClick={() => insertAgentExtension("[[Документ]]")}><Brackets size={15} /></ToolbarButton>
        <ToolbarButton label="Маркированный список" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(wrapInBulletListCommand.key)))}><List size={15} /></ToolbarButton>
        <ToolbarButton label="Нумерованный список" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(wrapInOrderedListCommand.key)))}><ListOrdered size={15} /></ToolbarButton>
        <ToolbarButton label="Список задач" disabled={commandDisabled} onClick={() => insertAgentExtension("\n- [ ] Задача\n")}><ListChecks size={15} /></ToolbarButton>
        <ToolbarButton label="Цитата" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(wrapInBlockquoteCommand.key)))}><Quote size={15} /></ToolbarButton>
        <ToolbarButton label="Callout" disabled={commandDisabled} onClick={() => insertAgentExtension("\n> [!NOTE] Примечание\n> \n")}><MessageSquareText size={15} /></ToolbarButton>
        <ToolbarButton label="Встроенный код" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(toggleInlineCodeCommand.key)))}><Code2 size={15} /></ToolbarButton>
        <ToolbarButton label="Блок кода" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(createCodeBlockCommand.key, "")))}><FileCode2 size={15} /></ToolbarButton>
        <ToolbarButton label="Таблица" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(insertTableCommand.key, { row: 3, col: 3 } as never)))}><Table2 size={15} /></ToolbarButton>
        <ToolbarButton label="Изображение — picker вложений ещё недоступен" disabled onClick={() => undefined}><Image size={15} /></ToolbarButton>
        <ToolbarButton label="Горизонтальная линия" disabled={commandDisabled} onClick={command((editor) => editor.action((ctx) => ctx.get(commandsCtx).call(insertHrCommand.key)))}><Minus size={15} /></ToolbarButton>
      </div>
      {runtimeIssues.length ? (
        <div className="doc-visual-warning" role="alert">
          <strong>Визуальное сохранение ограничено</strong>
          <ul>{runtimeIssues.map((issue, index) => <li key={issueKey(issue, index)}>{issue.message}</li>)}</ul>
          <button type="button" onClick={onToggleSource}>Открыть Source mode</button>
        </div>
      ) : null}
      <div ref={rootRef} className="doc-visual-root" />
    </div>
  );
}
