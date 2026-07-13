import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CSSProperties, ReactNode } from "react";

export interface MenuAction {
  id: string;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
  destructive?: boolean;
  onSelect: () => void;
}

interface ActionMenuProps {
  id: string;
  label: string;
  triggerLabel: string;
  open: boolean;
  actions: MenuAction[];
  onOpenChange: (open: boolean) => void;
  trigger: ReactNode;
}

export function ActionMenu({ id, label, triggerLabel, open, actions, onOpenChange, trigger }: ActionMenuProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [position, setPosition] = useState<CSSProperties>({ top: 0, left: 0, visibility: "hidden" });

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const menuRect = menuRef.current?.getBoundingClientRect();
    const width = menuRect?.width || 220;
    const height = menuRect?.height || 220;
    const space = 8;
    const below = triggerRect.bottom + 6;
    const top = below + height <= window.innerHeight - space
      ? below
      : Math.max(space, triggerRect.top - height - 6);
    const left = Math.min(window.innerWidth - width - space, Math.max(space, triggerRect.right - width));
    setPosition({ top, left, maxHeight: `calc(100dvh - ${space * 2}px)`, visibility: "visible" });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const path = event.composedPath();
      if ((rootRef.current && path.includes(rootRef.current)) || (menuRef.current && path.includes(menuRef.current))) return;
      onOpenChange(false);
      triggerRef.current?.focus();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onOpenChange(false);
      triggerRef.current?.focus();
    };
    const onAnchorChange = () => onOpenChange(false);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onAnchorChange);
    window.addEventListener("scroll", onAnchorChange, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onAnchorChange);
      window.removeEventListener("scroll", onAnchorChange, true);
    };
  }, [onOpenChange, open]);

  const enabledIndexes = actions.reduce<number[]>((result, action, index) => {
    if (!action.disabled) result.push(index);
    return result;
  }, []);

  const move = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const position = enabledIndexes.indexOf(index);
    let next: number | undefined;
    if (event.key === "ArrowDown") next = enabledIndexes[(position + 1) % enabledIndexes.length];
    else if (event.key === "ArrowUp") next = enabledIndexes[(position - 1 + enabledIndexes.length) % enabledIndexes.length];
    else if (event.key === "Home") next = enabledIndexes[0];
    else if (event.key === "End") next = enabledIndexes.at(-1);
    else return;
    event.preventDefault();
    if (next !== undefined) itemRefs.current[next]?.focus();
  };

  return (
    <div className="action-menu-root" ref={rootRef}>
      <button
        ref={triggerRef}
        className="icon-button compact"
        type="button"
        aria-label={triggerLabel}
        title={triggerLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => onOpenChange(!open)}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          onOpenChange(true);
          window.requestAnimationFrame(() => itemRefs.current[event.key === "ArrowUp" ? enabledIndexes.at(-1) ?? 0 : enabledIndexes[0] ?? 0]?.focus());
        }}
      >
        {trigger}
      </button>
      {open ? createPortal(
        <div ref={menuRef} className="action-menu" id={id} role="menu" aria-label={label} style={position}>
          {actions.map((action, index) => (
            <button
              key={action.id}
              ref={(node) => { itemRefs.current[index] = node; }}
              type="button"
              role="menuitem"
              disabled={action.disabled}
              className={action.destructive ? "destructive" : undefined}
              tabIndex={index === enabledIndexes[0] ? 0 : -1}
              onKeyDown={(event) => move(event, index)}
              onClick={() => {
                if (action.disabled) return;
                onOpenChange(false);
                triggerRef.current?.focus();
                action.onSelect();
              }}
            >
              {action.icon}<span>{action.label}</span>
            </button>
          ))}
        </div>,
        document.body
      ) : null}
    </div>
  );
}
