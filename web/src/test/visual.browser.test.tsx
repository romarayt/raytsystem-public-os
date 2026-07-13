import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { App } from "../app/App";
import "../uiStyles";
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

async function renderStableRoute(route: "command-center" | "context" | "documents" | "safety") {
  window.history.replaceState({}, "", `/${route}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  await waitFor(() => expect(document.querySelector(".main-content > .route")).not.toBeNull());
  await document.fonts.ready;
  await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

async function openFixtureDocument() {
  const commandbar = document.querySelector<HTMLElement>(".documents-commandbar");
  if (window.innerWidth < 800 && commandbar) {
    within(commandbar).getByRole("button", { name: "Открыть файлы" }).click();
  }
  for (let depth = 0; depth < 3; depth += 1) {
    const collapsed = document.querySelector<HTMLElement>('[role="treeitem"][aria-expanded="false"]');
    if (!collapsed) break;
    collapsed.click();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  }
  const documentRow = [...document.querySelectorAll<HTMLElement>('[role="treeitem"]')]
    .find((entry) => entry.textContent?.includes("Layout note"));
  if (!documentRow) throw new Error("Documents visual fixture was not found");
  documentRow.click();
  await waitFor(() => expect(document.querySelector(".document-stage")).not.toBeNull());
  await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(mockFetch));
  localStorage.clear();
  document.documentElement.dataset.layoutTest = "true";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.layoutTest;
  window.history.replaceState({}, "", "/command-center");
});

describe("reviewed visual baselines", () => {
  it("keeps the Safety composition balanced on desktop", async () => {
    await page.viewport(1440, 900);
    await renderStableRoute("safety");
    await expect(page.getByTestId("workspace-shell")).toMatchScreenshot("safety-desktop");
  });

  it("keeps Context compact at a 150% zoom equivalent", async () => {
    await page.viewport(960, 600);
    await renderStableRoute("context");
    await expect(page.getByTestId("workspace-shell")).toMatchScreenshot("context-zoom-150");
  });

  it("keeps the compact header aligned at a 200% zoom equivalent", async () => {
    await page.viewport(720, 450);
    await renderStableRoute("safety");
    await expect(page.getByTestId("topbar")).toMatchScreenshot("topbar-zoom-200");
  });

  it("shows the complete Documents workspace on desktop", async () => {
    await page.viewport(1440, 900);
    await renderStableRoute("documents");
    await openFixtureDocument();
    await expect(page.getByTestId("workspace-shell")).toMatchScreenshot("documents-desktop");
  });

  it("keeps the open Documents workspace usable on mobile", async () => {
    await page.viewport(390, 844);
    await renderStableRoute("documents");
    await openFixtureDocument();
    await expect(page.getByTestId("workspace-shell")).toMatchScreenshot("documents-mobile");
  });

  it("shows keyboard focus and pointer hover without moving layout", async () => {
    await page.viewport(1440, 900);
    await renderStableRoute("command-center");
    const create = page.getByRole("button", { name: "Создать задачу" });
    document.querySelector<HTMLButtonElement>(".command-trigger")?.focus();
    await create.hover();
    await expect(page.getByTestId("workspace-shell")).toMatchScreenshot("interaction-hover-focus");
  });

  it("shows a focus-contained modal over an inert workspace", async () => {
    await page.viewport(1440, 900);
    await renderStableRoute("command-center");
    await page.getByRole("button", { name: "Палитра команд" }).click();
    await waitFor(() => expect(document.querySelector('[role="dialog"][aria-label="Палитра команд"]')).not.toBeNull());
    await expect(page.getByRole("dialog", { name: "Палитра команд" })).toMatchScreenshot("command-palette-modal");
  });

  it("keeps the high-contrast theme operational", async () => {
    await page.viewport(1440, 900);
    localStorage.setItem("raytsystem-theme", "contrast");
    await renderStableRoute("safety");
    await expect(page.getByTestId("workspace-shell")).toMatchScreenshot("safety-high-contrast");
  });

  it("keeps the light theme readable", async () => {
    await page.viewport(1440, 900);
    localStorage.setItem("raytsystem-theme", "light");
    await renderStableRoute("command-center");
    await expect(page.getByTestId("workspace-shell")).toMatchScreenshot("command-center-light");
  });
});
