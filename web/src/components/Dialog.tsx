import { createPortal } from "react-dom";
import { useState } from "react";
import type { KeyboardEventHandler, PointerEvent, ReactNode } from "react";
import { useDialogFocus } from "./useDialogFocus";

interface DialogProps {
  children: ReactNode;
  className: string;
  labelledBy?: string;
  label?: string;
  describedBy?: string;
  backdropClassName?: string;
  role?: "dialog" | "alertdialog";
  busy?: boolean;
  closeOnBackdrop?: boolean;
  closeOnEscape?: boolean;
  initialFocus?: "first" | "dialog" | "cancel";
  onClose: () => void;
  onKeyDown?: KeyboardEventHandler<HTMLElement>;
}

export function Dialog({
  children,
  className,
  labelledBy,
  label,
  describedBy,
  backdropClassName = "modal-backdrop",
  role = "dialog",
  busy = false,
  closeOnBackdrop = true,
  closeOnEscape = true,
  initialFocus = "first",
  onClose,
  onKeyDown
}: DialogProps) {
  const [returnFocus] = useState(() => document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const dialogRef = useDialogFocus<HTMLElement>(true, () => {
    if (closeOnEscape && !busy) onClose();
  }, { initialFocus, returnFocus });

  const onBackdropPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (closeOnBackdrop && !busy) onClose();
    else dialogRef.current?.focus({ preventScroll: true });
  };

  return createPortal(
    <div
      className={backdropClassName}
      data-modal-layer=""
      role="presentation"
      onPointerDown={onBackdropPointerDown}
    >
      <section
        ref={dialogRef}
        className={className}
        role={role}
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : label}
        aria-describedby={describedBy}
        aria-busy={busy || undefined}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        {children}
      </section>
    </div>,
    document.body
  );
}
