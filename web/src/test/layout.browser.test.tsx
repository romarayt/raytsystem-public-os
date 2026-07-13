import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { App } from "../app/App";
import { DocumentRestoreDialog } from "../features/documents/DocumentRestoreDialog";
import "../uiStyles";
import { auditLayout, formatLayoutIssues } from "./layoutAudit";
import { mockFetch } from "./mockApi";

vi.mock("sigma", () => ({
  default: class MockSigma {
    on() { return this; }
    setSetting() { return this; }
    refresh() { return undefined; }
    kill() { return undefined; }
    setGraph() { return undefined; }
    getDimensions() { return { width: 800, height: 600 }; }
    getCamera() { return { animatedReset: () => Promise.resolve(), animate: () => Promise.resolve(), on() {}, off() {}, disable() {}, enable() {}, getState() { return { x: 0, y: 0, ratio: 1, angle: 0 }; } }; }
    getMouseCaptor() { return { on() { return undefined; }, off() { return undefined; } }; }
    getCustomBBox() { return null; }
    setCustomBBox() { return undefined; }
    getBBox() { return { x: [0, 0], y: [0, 0] }; }
    viewportToGraph() { return { x: 0, y: 0 }; }
    getNodeDisplayData() { return undefined; }
  }
}));

vi.mock("graphology-layout-forceatlas2", () => ({ default: { assign: () => undefined } }));

const routes = ["command-center", "handbook", "documents", "tasks", "universe", "runs", "agents", "skills", "context", "safety", "systems"] as const;
const viewports = [
  [1920, 1080, "wide-desktop"],
  [1440, 900, "desktop"],
  [1280, 720, "compact-desktop"],
  [1152, 720, "zoom-125"],
  [960, 600, "zoom-150"],
  [823, 514, "zoom-175"],
  [720, 450, "zoom-200"],
  [834, 1112, "tablet"],
  [390, 844, "mobile"],
  [320, 720, "reflow"]
] as const;
const inspectorViewports = [
  [1600, 900, "docked"],
  [1280, 800, "overlay"]
] as const;

function renderRoute(route: (typeof routes)[number]) {
  window.history.replaceState({}, "", `/${route}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const result = render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  return { ...result, client };
}

async function waitForRoute() {
  await waitFor(() => expect(document.querySelector(".main-content > .route")).not.toBeNull());
  await document.fonts.ready;
  await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(mockFetch));
  localStorage.clear();
  sessionStorage.clear();
  document.documentElement.dataset.layoutTest = "true";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.layoutTest;
  window.history.replaceState({}, "", "/command-center");
});

describe("layout quality gate", () => {
  it.each([
    [706, 790, "reported compact viewport"],
    [390, 844, "mobile"]
  ] as const)("keeps the restore diff dialog viewport-bound at %ix%i (%s)", async (width, height, name) => {
    await page.viewport(width, height);
    render(
      <div style={{ transform: "translateX(180px)", width: "320px" }}>
        <DocumentRestoreDialog
          revision={{ history_id: "revision-0123456789", source: "raytsystem_revision", recorded_at: "2026-07-13T00:00:00Z", content_sha256: "before", author: null, summary: null }}
          preview={{ preview_token: "preview-0123456789", snapshot_id: "snapshot", document_id: "document", history_id: "revision-0123456789", current_sha256: "current-0123456789", restored_sha256: "restored-0123456789", current_content: "# Правила безопасности расширений:\n\nТекущая версия", restored_content: "# Правила безопасности расширений:\n\nВосстановленная версия" }}
          fallbackCurrentContent=""
          pending={false}
          onCancel={() => undefined}
          onConfirm={() => undefined}
        />
      </div>
    );
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

    const backdrop = document.querySelector<HTMLElement>(".doc-modal-backdrop");
    const dialog = document.querySelector<HTMLElement>(".doc-restore-dialog");
    const summary = document.querySelector<HTMLElement>(".doc-diff-summary");
    const copy = summary?.querySelector<HTMLButtonElement>("button");
    expect(backdrop?.parentElement).toBe(document.body);
    expect(dialog, `restore dialog missing at ${name}`).not.toBeNull();
    expect(summary).not.toBeNull();
    expect(copy).not.toBeNull();
    if (!dialog || !summary || !copy) return;
    const dialogRect = dialog.getBoundingClientRect();
    const summaryRect = summary.getBoundingClientRect();
    const copyRect = copy.getBoundingClientRect();
    expect(dialogRect.left).toBeGreaterThanOrEqual(0);
    expect(dialogRect.right).toBeLessThanOrEqual(width);
    expect(summary.scrollWidth).toBeLessThanOrEqual(summary.clientWidth);
    expect(copyRect.left).toBeGreaterThanOrEqual(summaryRect.left);
    expect(copyRect.right).toBeLessThanOrEqual(summaryRect.right);
    expect(document.documentElement.scrollWidth).toBe(document.documentElement.clientWidth);
  });

  it.each(viewports)("keeps all routes within geometry contracts at %ix%i (%s)", async (width, height, name) => {
    await page.viewport(width, height);
    const failures: string[] = [];

    for (const route of routes) {
      const rendered = renderRoute(route);
      await waitForRoute();
      const issues = auditLayout();
      if (issues.length) failures.push(`${route}:\n${formatLayoutIssues(issues)}`);
      rendered.unmount();
      rendered.client.clear();
    }

    expect(failures, `${name} (${width}×${height})\n${failures.join("\n\n")}`).toEqual([]);
  });

  it.each([
    [1440, 900, "desktop"],
    [720, 450, "zoom-200"],
    [390, 844, "mobile"]
  ] as const)("keeps an open document and its drawers inside the viewport at %ix%i (%s)", async (width, height, name) => {
    await page.viewport(width, height);
    renderRoute("documents");
    await waitForRoute();
    if (width < 800) {
      within(document.querySelector<HTMLElement>(".documents-commandbar") as HTMLElement)
        .getByRole("button", { name: "Открыть файлы" })
        .click();
    }
    for (let depth = 0; depth < 3; depth += 1) {
      const collapsed = document.querySelector<HTMLElement>('[role="treeitem"][aria-expanded="false"]');
      if (!collapsed) break;
      collapsed.click();
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    }
    const documentRow = [...document.querySelectorAll<HTMLElement>('[role="treeitem"]')]
      .find((entry) => entry.textContent?.includes("Layout note"));
    expect(documentRow, `document row missing at ${name}`).toBeDefined();
    documentRow?.click();
    await waitFor(() => expect(document.querySelector(".document-stage")).not.toBeNull());
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    const openIssues = auditLayout();
    expect(openIssues, formatLayoutIssues(openIssues)).toEqual([]);

    if (width < 800) {
      document.querySelector<HTMLButtonElement>(".documents-drawer-close")?.click();
      within(document.querySelector<HTMLElement>(".documents-commandbar") as HTMLElement)
        .getByRole("button", { name: "Открыть свойства" })
        .click();
      await waitFor(() => expect(document.querySelector(".documents-inspector-shell.drawer-open")).not.toBeNull());
      const drawerIssues = auditLayout();
      expect(drawerIssues, formatLayoutIssues(drawerIssues)).toEqual([]);
    }
  });

  it("treats mobile document drawers as modal focus scopes and restores their triggers", async () => {
    await page.viewport(390, 844);
    renderRoute("documents");
    await waitForRoute();
    const commandbar = document.querySelector<HTMLElement>(".documents-commandbar") as HTMLElement;
    const filesTrigger = within(commandbar).getByRole("button", { name: "Открыть файлы" });
    const inspectorTrigger = within(commandbar).getByRole("button", { name: "Открыть свойства" });
    expect(inspectorTrigger).toBeDisabled();

    filesTrigger.click();
    const navigation = await waitFor(() => {
      const value = document.querySelector<HTMLElement>("#documents-navigation-drawer.drawer-open");
      expect(value).not.toBeNull();
      return value as HTMLElement;
    });
    expect(navigation).toHaveAttribute("role", "dialog");
    expect(navigation).toHaveAttribute("aria-modal", "true");
    await waitFor(() => expect(within(navigation).getByRole("button", { name: "Закрыть файлы" })).toHaveFocus());

    for (let depth = 0; depth < 3; depth += 1) {
      const collapsed = navigation.querySelector<HTMLElement>('[role="treeitem"][aria-expanded="false"]');
      if (!collapsed) break;
      collapsed.click();
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    }
    const documentRow = [...navigation.querySelectorAll<HTMLElement>('[role="treeitem"]')]
      .find((entry) => entry.textContent?.includes("Layout note"));
    expect(documentRow).toBeDefined();
    documentRow?.click();
    await waitFor(() => expect(filesTrigger).toHaveFocus());
    await waitFor(() => expect(inspectorTrigger).not.toBeDisabled());

    inspectorTrigger.click();
    const inspector = await waitFor(() => {
      const value = document.querySelector<HTMLElement>("#documents-inspector-drawer.drawer-open");
      expect(value).not.toBeNull();
      return value as HTMLElement;
    });
    expect(inspector).toHaveAttribute("role", "dialog");
    expect(inspector).toHaveAttribute("aria-modal", "true");
    const close = within(inspector).getByRole("button", { name: "Закрыть сведения" });
    await waitFor(() => expect(close).toHaveFocus());
    const focusable = [...inspector.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])")];
    focusable.at(-1)?.focus();
    fireEvent.keyDown(inspector, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(inspector, { key: "Escape" });
    await waitFor(() => expect(inspectorTrigger).toHaveFocus());
  });

  it("resets nested scrolling before presenting the next route", async () => {
    await page.viewport(960, 600);
    renderRoute("context");
    await waitForRoute();
    const main = document.querySelector<HTMLElement>(".main-content");
    expect(main).not.toBeNull();
    if (!main) return;
    main.scrollTop = 360;

    const navigation = document.querySelector<HTMLElement>(".sidebar");
    expect(navigation).not.toBeNull();
    if (!navigation) return;
    within(navigation).getByRole("button", { name: "Безопасность" }).click();

    await waitFor(() => expect(document.querySelector(".topbar h1")?.textContent).toBe("Безопасность"));
    await waitForRoute();
    const routeRect = document.querySelector<HTMLElement>(".main-content > .route")?.getBoundingClientRect();
    const mainRect = main.getBoundingClientRect();
    expect(main.scrollTop).toBe(0);
    expect(routeRect?.top ?? -1).toBeGreaterThanOrEqual(mainRect.top);
  });

  it("keeps the sidebar toggle fully visible and centered on the divider", async () => {
    await page.viewport(1600, 900);
    renderRoute("command-center");
    await waitForRoute();

    const assertToggleGeometry = (label: string) => {
      const sidebar = document.querySelector<HTMLElement>(".sidebar");
      const brand = document.querySelector<HTMLElement>(".brand");
      const toggle = document.querySelector<HTMLButtonElement>(".collapse-sidebar");
      expect(sidebar).not.toBeNull();
      expect(brand).not.toBeNull();
      expect(toggle).not.toBeNull();
      if (!sidebar || !brand || !toggle) return;
      const sidebarRect = sidebar.getBoundingClientRect();
      const brandRect = brand.getBoundingClientRect();
      const toggleRect = toggle.getBoundingClientRect();
      expect(toggle.getAttribute("aria-label")).toBe(label);
      expect(toggleRect.width).toBeCloseTo(24, 0);
      expect(toggleRect.height).toBeCloseTo(32, 0);
      expect(Math.abs((toggleRect.left + toggleRect.right) / 2 - sidebarRect.right)).toBeLessThanOrEqual(1.5);
      expect(Math.abs((toggleRect.top + toggleRect.bottom) / 2 - (brandRect.top + brandRect.bottom) / 2)).toBeLessThanOrEqual(1);
      expect(document.elementFromPoint(toggleRect.right - 1, (toggleRect.top + toggleRect.bottom) / 2)?.closest("button")).toBe(toggle);
      expect(document.documentElement.scrollWidth).toBe(document.documentElement.clientWidth);
    };

    assertToggleGeometry("Свернуть навигацию");
    document.querySelector<HTMLButtonElement>(".collapse-sidebar")?.click();
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    assertToggleGeometry("Развернуть навигацию");
  });

  it("keeps agent detail tabs inset, separated, focus-visible and horizontally scrollable on mobile", async () => {
    await page.viewport(320, 720);
    renderRoute("agents");
    await waitForRoute();

    expect(document.querySelector(".main-content > .route .route")).toBeNull();
    within(document.querySelector<HTMLElement>(".main-content") as HTMLElement)
      .getByRole("button", { name: "Открыть агента Researcher" })
      .click();
    await waitFor(() => expect(document.querySelector(".detail-tab-surface [role='tablist']")).not.toBeNull());
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

    const surface = document.querySelector<HTMLElement>(".detail-tab-surface");
    const container = surface?.querySelector<HTMLElement>(".surface-tabs-container");
    const scroller = surface?.querySelector<HTMLElement>(".surface-tabs-scroll");
    const activeTab = surface?.querySelector<HTMLButtonElement>(".surface-tab[aria-selected='true']");
    const content = surface?.querySelector<HTMLElement>(".surface-content");
    expect(surface).not.toBeNull();
    expect(container).not.toBeNull();
    expect(scroller).not.toBeNull();
    expect(activeTab).not.toBeNull();
    expect(content).not.toBeNull();
    if (!surface || !container || !scroller || !activeTab || !content) return;

    const rootStyle = getComputedStyle(document.documentElement);
    const surfaceStyle = getComputedStyle(surface);
    const containerStyle = getComputedStyle(container);
    const activeStyle = getComputedStyle(activeTab);
    const contentGap = Number.parseFloat(rootStyle.getPropertyValue("--surface-tabs-content-gap"));
    const inset = Number.parseFloat(rootStyle.getPropertyValue("--surface-tabs-padding"));
    const containerRect = container.getBoundingClientRect();
    const activeRect = activeTab.getBoundingClientRect();
    const contentRect = content.getBoundingClientRect();

    expect(Number.parseFloat(surfaceStyle.rowGap)).toBeCloseTo(contentGap, 1);
    expect(contentRect.top - containerRect.bottom).toBeCloseTo(contentGap, 0);
    expect(activeRect.left - containerRect.left).toBeGreaterThanOrEqual(inset);
    expect(containerRect.bottom - activeRect.bottom).toBeGreaterThanOrEqual(inset);
    expect(Number.parseFloat(containerStyle.paddingLeft)).toBeCloseTo(inset, 1);
    expect(getComputedStyle(scroller).overflowX).toBe("auto");
    expect(scroller.scrollWidth).toBeGreaterThan(scroller.clientWidth);

    activeTab.focus();
    expect(activeTab).toHaveFocus();
    expect(activeTab.matches(":focus-visible")).toBe(true);
    expect(Number.parseFloat(activeStyle.outlineWidth)).toBeGreaterThanOrEqual(2);
    expect(Number.parseFloat(activeStyle.outlineOffset)).toBeLessThanOrEqual(0);

    const enabledTabs = [...surface.querySelectorAll<HTMLButtonElement>(".surface-tab:not(:disabled)")];
    const lastTab = enabledTabs.at(-1);
    expect(lastTab).toBeDefined();
    if (!lastTab) return;

    fireEvent.keyDown(activeTab, { key: "End" });
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    const finalRect = lastTab.getBoundingClientRect();
    const scrollerRect = scroller.getBoundingClientRect();
    expect(lastTab).toHaveFocus();
    expect(scroller.scrollLeft).toBeGreaterThan(0);
    expect(finalRect.left).toBeGreaterThanOrEqual(scrollerRect.left - 0.5);
    expect(finalRect.right).toBeLessThanOrEqual(scrollerRect.right + 0.5);
    expect(document.documentElement.scrollWidth).toBe(document.documentElement.clientWidth);
  });

  it.each(inspectorViewports)("keeps the inspector and reduced workspace inside geometry contracts at %ix%i (%s)", async (width, height, mode) => {
    await page.viewport(width, height);
    renderRoute("context");
    await waitForRoute();
    document.querySelector<HTMLButtonElement>(".context-card")?.click();
    await waitFor(() => expect(document.querySelector(".inspector")).not.toBeNull());
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

    const inspector = document.querySelector<HTMLElement>(".inspector");
    const workspace = document.querySelector<HTMLElement>(".workspace-shell");
    expect(inspector).not.toBeNull();
    expect(workspace).not.toBeNull();
    const rect = inspector?.getBoundingClientRect();
    const workspaceRect = workspace?.getBoundingClientRect();
    expect(rect?.left ?? -1).toBeGreaterThanOrEqual(0);
    expect(rect?.right ?? width + 1).toBeLessThanOrEqual(width);
    expect(rect?.height ?? 0).toBeLessThanOrEqual(height);
    if (mode === "docked") {
      expect(workspaceRect?.right ?? width).toBeLessThanOrEqual((rect?.left ?? 0) + 1.5);
    } else {
      expect(workspaceRect?.right ?? 0).toBeGreaterThan((rect?.left ?? width) + 1.5);
    }
    const issues = auditLayout();
    expect(issues, formatLayoutIssues(issues)).toEqual([]);
  });
});
