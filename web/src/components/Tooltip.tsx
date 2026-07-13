import { cloneElement, useId, useState } from "react";
import type { KeyboardEvent, ReactElement } from "react";

interface TooltipProps {
  children: ReactElement<{ "aria-describedby"?: string }>;
  content: string;
}

export function Tooltip({ children, content }: TooltipProps) {
  const id = `tooltip-${useId().replaceAll(":", "")}`;
  const [dismissed, setDismissed] = useState(false);
  const onKeyDown = (event: KeyboardEvent<HTMLSpanElement>) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    setDismissed(true);
  };
  return (
    <span
      className={`tooltip-anchor ${dismissed ? "tooltip-dismissed" : ""}`}
      onPointerEnter={() => setDismissed(false)}
      onFocusCapture={() => setDismissed(false)}
      onKeyDown={onKeyDown}
    >
      {cloneElement(children, { "aria-describedby": id })}
      <span className="tooltip" id={id} role="tooltip">{content}</span>
    </span>
  );
}
