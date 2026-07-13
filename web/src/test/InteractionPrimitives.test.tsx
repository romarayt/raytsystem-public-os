import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog } from "../components/Dialog";
import { ActionMenu } from "../components/Menu";
import { Tooltip } from "../components/Tooltip";
import { DocumentActionDialog } from "../features/documents/DocumentActionDialog";

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

function DialogFixture({ protectedBackdrop = false }: { protectedBackdrop?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Открыть</button>
      {open ? (
        <Dialog className="test-dialog" label="Тестовое окно" closeOnBackdrop={!protectedBackdrop} onClose={() => setOpen(false)}>
          <input aria-label="Автофокус" autoFocus />
          <button type="button" data-dialog-cancel onClick={() => setOpen(false)}>Отмена</button>
          <button type="button">Действие</button>
        </Dialog>
      ) : null}
    </>
  );
}

describe("interaction primitives", () => {
  it("isolates a modal, closes a safe backdrop, and restores focus", async () => {
    const { container } = render(<DialogFixture />);
    const trigger = screen.getByRole("button", { name: "Открыть" });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Тестовое окно" });
    expect(container).toHaveProperty("inert", true);
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.pointerDown(dialog.parentElement!);

    expect(screen.queryByRole("dialog", { name: "Тестовое окно" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(container.inert).not.toBe(true);
  });

  it("does not dismiss a protected backdrop and Escape returns to safety", () => {
    render(<DialogFixture protectedBackdrop />);
    fireEvent.click(screen.getByRole("button", { name: "Открыть" }));
    const dialog = screen.getByRole("dialog", { name: "Тестовое окно" });
    fireEvent.pointerDown(dialog.parentElement!);
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Тестовое окно" })).not.toBeInTheDocument();
  });

  it("implements menu roles, arrow navigation, terminal close, and focus return", () => {
    const first = vi.fn();
    render(<ActionMenu id="test-menu" label="Действия" triggerLabel="Открыть действия" open actions={[
      { id: "one", label: "Первое", onSelect: first },
      { id: "skip", label: "Недоступно", disabled: true, onSelect: vi.fn() },
      { id: "last", label: "Последнее", onSelect: vi.fn() }
    ]} onOpenChange={() => undefined} trigger={<span>•••</span>} />);
    const items = screen.getAllByRole("menuitem");
    items[0].focus();
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(items[2]).toHaveFocus();
    fireEvent.keyDown(items[2], { key: "Home" });
    expect(items[0]).toHaveFocus();
    fireEvent.click(items[0]);
    expect(first).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Открыть действия" })).toHaveFocus();
  });

  it("connects tooltip text without using it as the accessible name and dismisses on Escape", () => {
    render(<Tooltip content="Полезная подсказка"><button type="button" aria-label="Сохранить">S</button></Tooltip>);
    const button = screen.getByRole("button", { name: "Сохранить" });
    const tooltip = screen.getByRole("tooltip");
    expect(button).toHaveAttribute("aria-describedby", tooltip.id);
    button.focus();
    fireEvent.keyDown(button, { key: "Escape" });
    expect(tooltip.parentElement).toHaveClass("tooltip-dismissed");
  });

  it("asks before discarding a dirty document form and keeps values after cancelling", () => {
    render(<DocumentActionDialog kind="create" roots={[{ id: "notes", label: "Заметки", writable: true }]} onCancel={vi.fn()} onSubmit={vi.fn()} />);
    const name = screen.getByRole("textbox", { name: "Название" });
    fireEvent.change(name, { target: { value: "Черновик" } });
    const dialog = screen.getByRole("dialog", { name: "Новый документ" });
    fireEvent.pointerDown(dialog.parentElement!);
    const alert = screen.getByRole("alertdialog", { name: "Закрыть без сохранения?" });
    fireEvent.click(within(alert).getByRole("button", { name: "Продолжить редактирование" }));
    expect(screen.getByRole("textbox", { name: "Название" })).toHaveValue("Черновик");
  });
});
