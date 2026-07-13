import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axe from "axe-core";
import { cleanup, render, waitFor } from "@testing-library/react";
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
    getMouseCaptor() { return { on() {}, off() {} }; }
    getCustomBBox() { return null; }
    setCustomBBox() { return undefined; }
    getBBox() { return { x: [0, 0], y: [0, 0] }; }
    viewportToGraph() { return { x: 0, y: 0 }; }
    getNodeDisplayData() { return undefined; }
  }
}));
vi.mock("graphology-layout-forceatlas2", () => ({ default: { assign: () => undefined } }));

const routes = ["command-center", "handbook", "documents", "tasks", "universe", "runs", "agents", "skills", "context", "safety", "systems"] as const;

beforeEach(async () => {
  await page.viewport(1440, 900);
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

describe("WCAG 2.2 AA automation", () => {
  it("reports no axe violations on every route and theme", async () => {
    const failures: string[] = [];
    for (const theme of ["dark", "light", "contrast"] as const) {
      localStorage.setItem("raytsystem-theme", theme);
      for (const route of routes) {
        window.history.replaceState({}, "", `/${route}`);
        const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
        const rendered = render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
        await waitFor(() => expect(document.querySelector(".main-content > .route")).not.toBeNull());
        await document.fonts.ready;
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
        const result = await axe.run(document, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
          resultTypes: ["violations"]
        });
        for (const violation of result.violations) {
          const targets = violation.nodes.map((node) => `${node.target.join(" ")} :: ${node.html}`).join(" | ");
          failures.push(`${theme}/${route}: ${violation.id} — ${violation.help} (${violation.nodes.length}) :: ${targets}`);
        }
        const enabledCursorFailures = Array.from(document.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], [role="button"]:not([aria-disabled="true"]), [role="tab"]:not([aria-disabled="true"]), [role="menuitem"]:not([aria-disabled="true"]), [data-clickable="true"]:not([aria-disabled="true"])'))
          .filter((element) => element.getClientRects().length > 0 && getComputedStyle(element).cursor !== "pointer")
          .map((element) => `${element.tagName.toLowerCase()}.${element.className}`);
        const disabledCursorFailures = Array.from(document.querySelectorAll<HTMLElement>('button:disabled, [aria-disabled="true"]'))
          .filter((element) => element.getClientRects().length > 0 && getComputedStyle(element).cursor !== "not-allowed")
          .map((element) => `${element.tagName.toLowerCase()}.${element.className}`);
        if (enabledCursorFailures.length) failures.push(`${theme}/${route}: enabled cursor != pointer :: ${enabledCursorFailures.join(", ")}`);
        if (disabledCursorFailures.length) failures.push(`${theme}/${route}: disabled cursor != not-allowed :: ${disabledCursorFailures.join(", ")}`);
        rendered.unmount();
        client.clear();
      }
    }
    expect(failures, failures.join("\n")).toEqual([]);
  });
});
