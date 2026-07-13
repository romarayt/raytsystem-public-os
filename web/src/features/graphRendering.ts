import { drawDiscNodeHover, type NodeHoverDrawingFunction } from "sigma/rendering";

type GraphTheme = "dark" | "light" | "contrast";

export function graphHoverLabelColor(theme: GraphTheme): string {
  if (theme === "light") return "#27313b";
  if (theme === "contrast") return "#000000";
  return "#090a0d";
}

export function createGraphNodeHoverRenderer(theme: GraphTheme): NodeHoverDrawingFunction {
  const color = graphHoverLabelColor(theme);
  return (context, data, settings) => {
    drawDiscNodeHover(context, data, { ...settings, labelColor: { color } });
  };
}
