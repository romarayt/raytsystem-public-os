import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { App } from "../app/App";
import "../uiStyles";
import { mockFetch } from "./mockApi";

type Route = "command-center" | "documents" | "universe" | "agents" | "skills" | "tasks" | "safety";

const output = (name: string) => `../../../assets/github/${name}.png`;

async function settle() {
  await document.fonts.ready;
  await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

async function renderRoute(route: Route, width: number, height: number) {
  await page.viewport(width, height);
  window.history.replaceState({}, "", `/${route}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  await waitFor(() => expect(document.querySelector(".main-content > .route")).not.toBeNull());
  await settle();
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
    await settle();
  }
  const row = [...document.querySelectorAll<HTMLElement>('[role="treeitem"]')]
    .find((entry) => entry.textContent?.includes("Layout note"));
  if (!row) throw new Error("Synthetic document fixture was not found");
  row.click();
  await waitFor(() => expect(document.querySelector(".document-stage")).not.toBeNull());
  await settle();
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(mockFetch));
  localStorage.clear();
  localStorage.setItem("raytsystem-theme", "dark");
  document.documentElement.dataset.layoutTest = "true";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.layoutTest;
  window.history.replaceState({}, "", "/command-center");
});

describe("GitHub product screenshots", () => {
  it.each([
    ["command-center", "command-center"],
    ["universe", "universe"],
    ["agents", "agents"],
    ["skills", "skills"],
    ["tasks", "tasks"],
    ["safety", "safety"]
  ] as const)("captures %s", async (route, name) => {
    await renderRoute(route, 1440, 900);
    // Chromium can return an unpainted first full-page frame for the initial route.
    // Warm the compositor without writing an artifact, then capture reviewed outputs.
    await page.screenshot();
    await settle();
    await page.screenshot({ path: output(name) });
    if (route === "command-center") await page.screenshot({ path: output("hero") });
  });

  it("captures an open synthetic document", async () => {
    await renderRoute("documents", 1440, 900);
    await openFixtureDocument();
    await page.screenshot({ path: output("documents") });
  });

  it("captures the social preview", async () => {
    await renderRoute("command-center", 1280, 640);
    await page.screenshot({ path: output("social-preview") });
  });
});
