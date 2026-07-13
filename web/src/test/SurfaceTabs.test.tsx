import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Surface, SurfaceContent, SurfaceTabs } from "../components/SurfaceTabs";
import "../styles.css";

type TabId = "overview" | "instruction" | "runtime" | "history";

function styleRule(selector: string): CSSStyleDeclaration | undefined {
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules)) {
      if (rule instanceof CSSStyleRule && rule.selectorText === selector) return rule.style;
    }
  }
  return undefined;
}

const tabs = [
  { id: "overview", label: "Обзор", panelId: "agent-panel", tabId: "overview-tab" },
  { id: "instruction", label: "Инструкция", panelId: "agent-panel", tabId: "instruction-tab", icon: <span>i</span> },
  { id: "runtime", label: "Runtime", panelId: "agent-panel", tabId: "runtime-tab", disabled: true },
  { id: "history", label: "История", panelId: "agent-panel", tabId: "history-tab" }
] as const;

function Fixture({ onChange = () => undefined }: { onChange?: (tab: TabId) => void }) {
  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const selectTab = (tab: TabId) => {
    onChange(tab);
    setActiveTab(tab);
  };
  return (
    <Surface data-testid="surface-root">
      <SurfaceTabs
        id="agent-tabs"
        tabs={tabs}
        activeTab={activeTab}
        onTabChange={selectTab}
        ariaLabel="Разделы агента"
      />
      <SurfaceContent id="agent-panel" labelledBy={`${activeTab}-tab`}>
        {activeTab}
      </SurfaceContent>
    </Surface>
  );
}

afterEach(cleanup);

describe("SurfaceTabs", () => {
  it("exposes a labelled horizontal tablist and connected tabpanel", () => {
    render(<Fixture />);

    const tabList = screen.getByRole("tablist", { name: "Разделы агента" });
    expect(tabList).toHaveAttribute("aria-orientation", "horizontal");
    expect(tabList).toHaveClass("surface-tabs");
    expect(screen.getByTestId("surface-tabs-container")).toHaveClass("surface-tabs-container");
    expect(screen.getByTestId("surface-tabs-scroll")).toHaveClass("surface-tabs-scroll");

    const overview = screen.getByRole("tab", { name: "Обзор" });
    expect(overview).toHaveAttribute("aria-selected", "true");
    expect(overview).toHaveAttribute("aria-controls", "agent-panel");
    expect(overview).toHaveAttribute("tabindex", "0");

    const instruction = screen.getByRole("tab", { name: "Инструкция" });
    expect(instruction).toHaveAttribute("aria-selected", "false");
    expect(instruction).toHaveAttribute("tabindex", "-1");
    expect(instruction.querySelector(".surface-tab-icon")).toHaveAttribute("aria-hidden", "true");

    const runtime = screen.getByRole("tab", { name: "Runtime" });
    expect(runtime).toBeDisabled();
    expect(runtime).toHaveAttribute("aria-disabled", "true");

    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveClass("surface-content");
    expect(panel).toHaveAttribute("aria-labelledby", "overview-tab");
    expect(screen.getByTestId("surface-root")).toHaveClass("surface");
  });

  it("activates a tab with a pointer and preserves controlled state", () => {
    const onChange = vi.fn();
    render(<Fixture onChange={onChange} />);

    fireEvent.click(screen.getByRole("tab", { name: "Инструкция" }));

    expect(onChange).toHaveBeenCalledWith("instruction");
    expect(screen.getByRole("tab", { name: "Инструкция" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("instruction");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "instruction-tab");

    fireEvent.click(screen.getByRole("tab", { name: "Runtime" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("tabpanel")).toHaveTextContent("instruction");
  });

  it("supports Arrow, Home and End navigation while skipping disabled tabs", () => {
    render(<Fixture />);
    const overview = screen.getByRole("tab", { name: "Обзор" });
    overview.focus();

    fireEvent.keyDown(overview, { key: "ArrowRight" });
    const instruction = screen.getByRole("tab", { name: "Инструкция" });
    expect(instruction).toHaveFocus();
    expect(instruction).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(instruction, { key: "ArrowRight" });
    const history = screen.getByRole("tab", { name: "История" });
    expect(history).toHaveFocus();
    expect(history).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(history, { key: "Home" });
    expect(overview).toHaveFocus();
    expect(overview).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(overview, { key: "ArrowLeft" });
    expect(history).toHaveFocus();

    fireEvent.keyDown(history, { key: "Home" });
    fireEvent.keyDown(overview, { key: "End" });
    expect(history).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Runtime" })).not.toHaveFocus();
  });

  it("reveals a focused tab inside the horizontal scroller", () => {
    const scrollIntoView = vi.fn();
    const originalDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
      writable: true
    });

    try {
      render(<Fixture />);
      screen.getByRole("tab", { name: "История" }).focus();

      expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
    } finally {
      if (originalDescriptor) {
        Object.defineProperty(HTMLElement.prototype, "scrollIntoView", originalDescriptor);
      } else {
        delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
      }
    }
  });

  it("keeps the responsive layout contract on stable hooks", () => {
    render(<Fixture />);

    const rootTokens = getComputedStyle(document.documentElement);
    expect(rootTokens.getPropertyValue("--surface-tabs-padding").trim()).toBe("6px");
    expect(rootTokens.getPropertyValue("--surface-tabs-gap").trim()).toBe("6px");
    expect(rootTokens.getPropertyValue("--surface-tabs-content-gap").trim()).toBe("16px");
    expect(rootTokens.getPropertyValue("--surface-tabs-height").trim()).toBe("42px");
    expect(rootTokens.getPropertyValue("--focus-ring").trim()).toBe("#63d8d2");
    expect(styleRule(':root[data-theme="light"]')?.getPropertyValue("--focus-ring").trim()).toBe("#0d6f73");
    expect(styleRule(':root[data-theme="contrast"]')?.getPropertyValue("--focus-ring").trim()).toBe("#7ef7f0");

    expect(styleRule(".surface-tabs-container")?.padding).toBe("var(--surface-tabs-padding)");
    expect(styleRule(".surface")?.gap).toBe("var(--surface-tabs-content-gap)");
    expect(styleRule(".surface-tabs-scroll")?.overflowX).toBe("auto");
    expect(styleRule(".surface-tab:focus-visible")?.outline).toBe("2px solid var(--focus-ring)");
    expect(styleRule(".surface-tab:focus-visible")?.outlineOffset).toBe("-3px");

    const activeTabStyle = getComputedStyle(screen.getByRole("tab", { name: "Обзор" }));
    expect(activeTabStyle.minHeight).toBe("var(--surface-tabs-height)");
    expect(activeTabStyle.paddingLeft).toBe("13px");
    expect(activeTabStyle.paddingRight).toBe("13px");
    expect(activeTabStyle.whiteSpace).toBe("nowrap");

    expect(getComputedStyle(screen.getByTestId("surface-tabs-scroll")).overflowX).toBe("auto");
    expect(getComputedStyle(screen.getByTestId("surface-root")).gap).toBe("var(--surface-tabs-content-gap)");
  });
});
