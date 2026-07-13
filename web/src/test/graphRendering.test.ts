import { describe, expect, it, vi } from "vitest";

const renderingMocks = vi.hoisted(() => ({ drawDiscNodeHover: vi.fn() }));

vi.mock("sigma/rendering", () => ({ drawDiscNodeHover: renderingMocks.drawDiscNodeHover }));

import { createGraphNodeHoverRenderer, graphHoverLabelColor } from "../features/graphRendering";

describe("graph hover rendering", () => {
  it.each([
    ["dark", "#090a0d"],
    ["light", "#27313b"],
    ["contrast", "#000000"]
  ] as const)("uses a contrasting label over Sigma's white hover surface in %s mode", (theme, color) => {
    expect(graphHoverLabelColor(theme)).toBe(color);
  });

  it("overrides only the hover label color before delegating to Sigma", () => {
    const renderer = createGraphNodeHoverRenderer("dark");
    const context = {} as CanvasRenderingContext2D;
    const data = { x: 10, y: 12, size: 8, label: "Проверка целостности", color: "#ddbb65" } as Parameters<typeof renderer>[1];
    const settings = { labelColor: { color: "#dce2e8" }, labelSize: 11, labelFont: "IBM Plex Sans Variable", labelWeight: "500" } as Parameters<typeof renderer>[2];

    renderer(context, data, settings);

    expect(renderingMocks.drawDiscNodeHover).toHaveBeenCalledOnce();
    expect(renderingMocks.drawDiscNodeHover).toHaveBeenCalledWith(
      context,
      data,
      expect.objectContaining({
        labelSize: 11,
        labelFont: "IBM Plex Sans Variable",
        labelColor: { color: "#090a0d" }
      })
    );
  });
});
