export type LayoutIssue = {
  rule: "horizontal-overflow" | "viewport-x" | "overlap" | "target-size" | "route-origin" | "nested-route" | "containment" | "control-height";
  selector: string;
  detail: string;
};

const tolerance = 1.5;

function isVisible(element: Element): element is HTMLElement {
  if (!(element instanceof HTMLElement)) return false;
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
}

function label(element: Element): string {
  if (!(element instanceof HTMLElement)) return element.tagName.toLowerCase();
  const id = element.id ? `#${element.id}` : "";
  const classes = [...element.classList].slice(0, 2).map((name) => `.${name}`).join("");
  return `${element.tagName.toLowerCase()}${id}${classes}`;
}

function overlaps(a: DOMRect, b: DOMRect): boolean {
  return Math.min(a.right, b.right) - Math.max(a.left, b.left) > tolerance
    && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > tolerance;
}

export function auditLayout(root: Document = document): LayoutIssue[] {
  const issues: LayoutIssue[] = [];
  const viewportWidth = root.defaultView?.innerWidth ?? 0;

  const overflowRegions = [
    "html", "body", "#root", ".app-shell", ".workspace-shell", ".topbar", ".topbar-actions",
    ".main-content", ".route", ".route-tools", ".tasks-route", ".task-tools",
    ".kanban-column", ".task-card", ".universe-toolbar", ".catalog-list", ".context-intro",
    ".context-card", ".context-card footer", ".safety-card", ".agent-card", ".adapter-row",
    ".policy-boundary", ".inspector", ".inspector-scroll"
  ];
  for (const selector of overflowRegions) {
    const element = root.querySelector(selector);
    if (!(element instanceof HTMLElement) || !isVisible(element)) continue;
    const overflowX = getComputedStyle(element).overflowX;
    const intentionallyScrollable = overflowX === "auto" || overflowX === "scroll";
    if (element.scrollWidth - element.clientWidth > tolerance && !intentionallyScrollable) {
      issues.push({ rule: "horizontal-overflow", selector, detail: `${element.scrollWidth}px > ${element.clientWidth}px` });
    }
  }

  const interactiveSelector = "button, a[href], input, select, textarea, [role='button'], [tabindex]:not([tabindex='-1'])";
  const horizontalScrollException = ".kanban, .lens-switch, .systems-tabs, .data-table, .graph-table, .surface-tabs-scroll";
  for (const element of root.querySelectorAll(interactiveSelector)) {
    if (!isVisible(element)) continue;
    const rect = element.getBoundingClientRect();
    if (!element.closest(horizontalScrollException) && (rect.left < -tolerance || rect.right > viewportWidth + tolerance)) {
      issues.push({ rule: "viewport-x", selector: label(element), detail: `${rect.left.toFixed(1)}…${rect.right.toFixed(1)} outside 0…${viewportWidth}` });
    }
    const minimum = element.closest(".mobile-nav") ? 44 : 24;
    if (rect.width + tolerance < minimum || rect.height + tolerance < minimum) {
      issues.push({ rule: "target-size", selector: label(element), detail: `${rect.width.toFixed(1)}×${rect.height.toFixed(1)} < ${minimum}×${minimum}` });
    }
  }

  const title = root.querySelector(".topbar-title");
  const actions = root.querySelector(".topbar-actions");
  if (title && actions && isVisible(title) && isVisible(actions) && overlaps(title.getBoundingClientRect(), actions.getBoundingClientRect())) {
    issues.push({ rule: "overlap", selector: ".topbar-title + .topbar-actions", detail: "header title intersects actions" });
  }

  const visibleHeaderControls = [...root.querySelectorAll(".topbar-actions > button, .topbar-actions > .top-local")].filter(isVisible);
  if (visibleHeaderControls.length > 1) {
    const heights = visibleHeaderControls.map((element) => element.getBoundingClientRect().height);
    if (Math.max(...heights) - Math.min(...heights) > tolerance) {
      issues.push({ rule: "control-height", selector: ".topbar-actions", detail: `heights ${heights.map((height) => height.toFixed(1)).join(", ")}` });
    }
  }

  const main = root.querySelector(".main-content");
  const route = root.querySelector(".main-content > .route");
  const nestedRoute = root.querySelector(".main-content > .route .route");
  if (nestedRoute) {
    issues.push({ rule: "nested-route", selector: label(nestedRoute), detail: ".route is reserved for the page root" });
  }
  if (main && route && isVisible(main) && isVisible(route)) {
    const mainRect = main.getBoundingClientRect();
    const routeRect = route.getBoundingClientRect();
    if (main.scrollTop !== 0 || routeRect.top < mainRect.top - tolerance) {
      issues.push({ rule: "route-origin", selector: ".main-content > .route", detail: `scrollTop=${main.scrollTop}, routeTop=${routeRect.top.toFixed(1)}, mainTop=${mainRect.top.toFixed(1)}` });
    }
  }

  for (const container of root.querySelectorAll(".context-card, .safety-card, .agent-card, .context-card footer, .adapter-row, .policy-boundary, .topbar-actions")) {
    if (!isVisible(container)) continue;
    const containerRect = container.getBoundingClientRect();
    for (const descendant of container.querySelectorAll("*")) {
      if (!isVisible(descendant)) continue;
      const descendantRect = descendant.getBoundingClientRect();
      if (descendantRect.left < containerRect.left - tolerance || descendantRect.right > containerRect.right + tolerance || descendantRect.top < containerRect.top - tolerance || descendantRect.bottom > containerRect.bottom + tolerance) {
        issues.push({ rule: "containment", selector: `${label(container)} > ${label(descendant)}`, detail: "visible descendant escapes container bounds" });
      }
    }
  }

  for (const gridSelector of [".context-grid", ".safety-grid"]) {
    const children = [...root.querySelectorAll(`${gridSelector} > *`)].filter(isVisible);
    for (let index = 0; index < children.length; index += 1) {
      for (let other = index + 1; other < children.length; other += 1) {
        if (overlaps(children[index].getBoundingClientRect(), children[other].getBoundingClientRect())) {
          issues.push({ rule: "overlap", selector: gridSelector, detail: `${label(children[index])} intersects ${label(children[other])}` });
        }
      }
    }
  }

  return issues;
}

export function formatLayoutIssues(issues: LayoutIssue[]): string {
  return issues.map((issue) => `[${issue.rule}] ${issue.selector}: ${issue.detail}`).join("\n");
}
