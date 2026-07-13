import { useEffect, useRef, type RefObject } from "react";

const focusableSelector = [
  "button:not(:disabled)",
  "input:not(:disabled)",
  "textarea:not(:disabled)",
  "select:not(:disabled)",
  "a[href]",
  "summary",
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])'
].join(",");

interface DialogFocusOptions {
  initialFocus?: "first" | "dialog" | "cancel";
  returnFocus?: HTMLElement | null;
}

const dialogStack: HTMLElement[] = [];
const initialInert = new Map<HTMLElement, boolean>();
let initialBodyOverflow = "";

function syncModalIsolation() {
  const topDialog = dialogStack.at(-1);
  const topLayer = topDialog?.closest<HTMLElement>("[data-modal-layer]") ?? null;
  if (!topLayer) {
    for (const [element, inert] of initialInert) element.inert = inert;
    initialInert.clear();
    document.body.style.overflow = initialBodyOverflow;
    return;
  }
  for (const child of Array.from(document.body.children)) {
    if (!(child instanceof HTMLElement)) continue;
    if (!initialInert.has(child)) initialInert.set(child, child.inert);
    child.inert = child !== topLayer;
  }
  document.body.style.overflow = "hidden";
}

function isVisible(element: HTMLElement): boolean {
  return element.getClientRects().length > 0 && element.getAttribute("aria-hidden") !== "true";
}

export function useDialogFocus<T extends HTMLElement>(
  active: boolean,
  onClose: () => void,
  options: DialogFocusOptions = {}
): RefObject<T | null> {
  const dialogRef = useRef<T>(null);
  const closeRef = useRef(onClose);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!active) return;
    const previous = options.returnFocus ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const dialog = dialogRef.current;
    if (dialog) {
      if (dialogStack.length === 0) initialBodyOverflow = document.body.style.overflow;
      dialogStack.push(dialog);
      syncModalIsolation();
    }
    const frame = window.requestAnimationFrame(() => {
      const preferred = dialogRef.current?.querySelector<HTMLElement>("[autofocus]");
      const cancel = dialogRef.current?.querySelector<HTMLElement>("[data-dialog-cancel]");
      const first = dialogRef.current?.querySelector<HTMLElement>(focusableSelector);
      const target = options.initialFocus === "dialog"
        ? dialogRef.current
        : options.initialFocus === "cancel"
          ? cancel ?? preferred ?? first ?? dialogRef.current
          : preferred ?? first ?? dialogRef.current;
      target?.focus({ preventScroll: true });
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (dialogStack.at(-1) !== dialogRef.current) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(focusableSelector)).filter(isVisible);
      if (!focusable.length) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      const index = dialog ? dialogStack.lastIndexOf(dialog) : -1;
      if (index >= 0) dialogStack.splice(index, 1);
      syncModalIsolation();
      if (previous && previous !== document.body) {
        window.setTimeout(() => {
          if (previous.isConnected) previous.focus();
        }, 0);
      }
    };
  }, [active, options.initialFocus, options.returnFocus]);

  return dialogRef;
}
