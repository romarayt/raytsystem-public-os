import { useId, useRef } from "react";
import type { ComponentPropsWithoutRef, KeyboardEvent, ReactNode } from "react";

function classNames(...values: Array<string | undefined>): string {
  return values.filter(Boolean).join(" ");
}

function domIdPart(value: string): string {
  return encodeURIComponent(value).replaceAll("%", "-");
}

export interface SurfaceTab<T extends string = string> {
  id: T;
  label: ReactNode;
  icon?: ReactNode;
  panelId?: string;
  tabId?: string;
  disabled?: boolean;
}

export interface SurfaceTabsProps<T extends string = string> {
  tabs: readonly SurfaceTab<T>[];
  activeTab: T;
  onTabChange: (tab: T) => void;
  ariaLabel: string;
  id?: string;
  className?: string;
}

export type SurfaceProps = ComponentPropsWithoutRef<"section">;

export function Surface({ className, ...props }: SurfaceProps) {
  return <section {...props} className={classNames("surface", className)} />;
}

export interface SurfaceContentProps extends ComponentPropsWithoutRef<"div"> {
  labelledBy?: string;
  "data-testid"?: string;
}

export function SurfaceContent({
  className,
  labelledBy,
  role = "tabpanel",
  "aria-labelledby": ariaLabelledBy,
  "data-testid": dataTestId = "surface-content",
  ...props
}: SurfaceContentProps) {
  return (
    <div
      {...props}
      className={classNames("surface-content", className)}
      role={role}
      aria-labelledby={ariaLabelledBy ?? labelledBy}
      data-testid={dataTestId}
    />
  );
}

export function SurfaceTabs<T extends string>({
  tabs,
  activeTab,
  onTabChange,
  ariaLabel,
  id,
  className
}: SurfaceTabsProps<T>) {
  const generatedId = useId().replaceAll(":", "");
  const tabListId = id ?? `surface-tabs-${generatedId}`;
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const enabledIndexes = tabs.reduce<number[]>((indexes, tab, index) => {
    if (!tab.disabled) indexes.push(index);
    return indexes;
  }, []);

  const revealTab = (index: number) => {
    const node = tabRefs.current[index];
    if (node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  };

  const activateAndFocus = (index: number) => {
    const tab = tabs[index];
    if (!tab || tab.disabled) return;
    tabRefs.current[index]?.focus();
    onTabChange(tab.id);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (enabledIndexes.length === 0) return;

    const enabledPosition = enabledIndexes.indexOf(index);
    let nextIndex: number | undefined;

    switch (event.key) {
      case "ArrowRight":
        nextIndex = enabledIndexes[(enabledPosition + 1) % enabledIndexes.length];
        break;
      case "ArrowLeft":
        nextIndex = enabledIndexes[(enabledPosition - 1 + enabledIndexes.length) % enabledIndexes.length];
        break;
      case "Home":
        nextIndex = enabledIndexes[0];
        break;
      case "End":
        nextIndex = enabledIndexes.at(-1);
        break;
      default:
        return;
    }

    if (nextIndex === undefined) return;
    event.preventDefault();
    activateAndFocus(nextIndex);
  };

  const activeIndex = tabs.findIndex((tab) => tab.id === activeTab && !tab.disabled);
  const rovingIndex = activeIndex >= 0 ? activeIndex : enabledIndexes[0];

  return (
    <div
      className={classNames("surface-tabs-container", className)}
      data-testid="surface-tabs-container"
      data-surface-tabs-container=""
    >
      <div className="surface-tabs-scroll" data-testid="surface-tabs-scroll">
        <div
          id={tabListId}
          className="surface-tabs"
          role="tablist"
          aria-label={ariaLabel}
          aria-orientation="horizontal"
          data-testid="surface-tabs"
        >
          {tabs.map((tab, index) => {
            const selected = tab.id === activeTab && !tab.disabled;
            const tabId = tab.tabId ?? `${tabListId}-tab-${domIdPart(tab.id)}`;
            return (
              <button
                key={tab.id}
                ref={(node) => {
                  tabRefs.current[index] = node;
                }}
                id={tabId}
                className="surface-tab"
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={tab.panelId}
                aria-disabled={tab.disabled || undefined}
                disabled={tab.disabled}
                tabIndex={index === rovingIndex ? 0 : -1}
                data-surface-tab={tab.id}
                onClick={() => onTabChange(tab.id)}
                onFocus={() => revealTab(index)}
                onKeyDown={(event) => handleKeyDown(event, index)}
              >
                {tab.icon ? <span className="surface-tab-icon" aria-hidden="true">{tab.icon}</span> : null}
                <span className="surface-tab-label">{tab.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
