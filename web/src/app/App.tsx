import {
  Activity,
  BookOpen,
  Bot,
  ChevronLeft,
  Database,
  Files,
  FileKey2,
  Gauge,
  GitBranch,
  LayoutDashboard,
  ListTodo,
  Menu,
  MoonStar,
  Orbit,
  PanelRightClose,
  PanelRightOpen,
  PlugZap,
  Search,
  ShieldCheck,
  Sun,
  Wrench,
  X
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import brandWordmarkUrl from "../assets/brand-wordmark.svg";
import { shortId } from "../api";
import { CommandPalette } from "../components/CommandPalette";
import { Dialog } from "../components/Dialog";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Inspector } from "../components/Inspector";
import { useExecutionFeatures } from "../executionHooks";
import { usePlatformFeatures } from "../featureHooks";
import { useSystem } from "../hooks";
import type { Selection } from "../types";
import { Context } from "../features/CatalogViews";
import { AgentsSurface } from "../features/AgentsSurface";
import { CommandCenter } from "../features/CommandCenter";
import { Documents } from "../features/documents/Documents";
import { Handbook } from "../features/Handbook";
import { Onboarding } from "../features/Onboarding";
import { Runs } from "../features/Runs";
import { Safety } from "../features/Safety";
import { SkillsSurface } from "../features/SkillsSurface";
import { SystemSections } from "../features/SystemSections";
import { Tasks } from "../features/Tasks";
import { Universe } from "../features/Universe";
import { routeCopy, type RouteKey } from "../presentation";

type Theme = "dark" | "light" | "contrast";

const routeMeta: Record<RouteKey, { label: string; description: string; icon: typeof Gauge; group: string }> = {
  "command-center": { ...routeCopy["command-center"], icon: LayoutDashboard },
  handbook: { ...routeCopy.handbook, icon: BookOpen },
  documents: { ...routeCopy.documents, icon: Files },
  onboarding: { ...routeCopy.onboarding, icon: PlugZap },
  tasks: { ...routeCopy.tasks, icon: ListTodo },
  universe: { ...routeCopy.universe, icon: Orbit },
  runs: { ...routeCopy.runs, icon: GitBranch },
  agents: { ...routeCopy.agents, icon: Bot },
  skills: { ...routeCopy.skills, icon: Wrench },
  context: { ...routeCopy.context, icon: FileKey2 },
  safety: { ...routeCopy.safety, icon: ShieldCheck },
  systems: { ...routeCopy.systems, icon: Activity }
};

const routeKeys = Object.keys(routeMeta) as RouteKey[];

function routeFromPath(): RouteKey {
  const candidate = window.location.pathname.replace(/^\//, "") as RouteKey;
  return routeKeys.includes(candidate) ? candidate : "command-center";
}

const dirtyEditorSelector = '[data-editor-scope][data-unsaved-changes="true"]';

function dirtyEditor(): HTMLElement | null {
  return document.querySelector<HTMLElement>(dirtyEditorSelector);
}

export function App() {
  const [route, setRoute] = useState<RouteKey>(routeFromPath);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [createTaskOpen, setCreateTaskOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMore, setMobileMore] = useState(false);
  const [navigationConfirmOpen, setNavigationConfirmOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem("raytsystem-theme") as Theme | null) ?? "dark");
  const [universeDocumentId, setUniverseDocumentId] = useState<string | null>(() =>
    window.location.pathname === "/universe" ? new URLSearchParams(window.location.search).get("document") : null
  );
  const createTaskReturnFocus = useRef<HTMLElement | null>(null);
  const mainContentRef = useRef<HTMLElement | null>(null);
  const pendingNavigationRef = useRef<(() => void) | null>(null);
  const system = useSystem();
  const platform = usePlatformFeatures();
  const execution = useExecutionFeatures();

  const guardNavigation = useCallback((action: () => void) => {
    if (!dirtyEditor()) {
      action();
      return true;
    }
    pendingNavigationRef.current = action;
    setNavigationConfirmOpen(true);
    return false;
  }, []);

  const navigate = useCallback((target: string) => {
    if (!routeKeys.includes(target as RouteKey)) return false;
    return guardNavigation(() => {
      const next = target as RouteKey;
      window.history.pushState({}, "", `/${next}`);
      setRoute(next);
      setSelection(null);
      setUniverseDocumentId(null);
      setMobileMore(false);
    });
  }, [guardNavigation]);

  useLayoutEffect(() => {
    const main = mainContentRef.current;
    if (!main) return;
    main.scrollTop = 0;
    main.scrollLeft = 0;
    const focusFrame = window.requestAnimationFrame(() => main.focus({ preventScroll: true }));
    return () => window.cancelAnimationFrame(focusFrame);
  }, [route]);

  useEffect(() => {
    if (window.location.pathname === "/") {
      window.history.replaceState({}, "", "/command-center");
    }
    const onPop = () => {
      const editor = dirtyEditor();
      if (editor) {
        const requestedLocation = `${window.location.pathname}${window.location.search}`;
        const editorLocation = editor.dataset.editorLocation;
        if (editorLocation) window.history.pushState({}, "", editorLocation);
        guardNavigation(() => {
          window.history.pushState({}, "", requestedLocation);
          const next = routeFromPath();
          setRoute(next);
          setSelection(null);
          setUniverseDocumentId(next === "universe" ? new URLSearchParams(window.location.search).get("document") : null);
        });
        return;
      }
      const next = routeFromPath();
      setRoute(next);
      setSelection(null);
      setUniverseDocumentId(next === "universe" ? new URLSearchParams(window.location.search).get("document") : null);
    };
    window.addEventListener("popstate", onPop, { capture: true });
    return () => window.removeEventListener("popstate", onPop, { capture: true });
  }, [guardNavigation]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("raytsystem-theme", theme);
  }, [theme]);

  useEffect(() => {
    document.title = `${routeMeta[route].label} · raytsystem`;
  }, [route]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const editorOwnsShortcut = Boolean(target?.closest("[data-editor-scope]"));
      const commandShortcut =
        (event.metaKey || event.ctrlKey) &&
        ((!editorOwnsShortcut && event.key.toLowerCase() === "k") ||
          (event.shiftKey && event.key.toLowerCase() === "p"));
      if (commandShortcut) {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      } else if (event.key === "Escape") {
        setPaletteOpen(false);
        setMobileMore(false);
        if (selection) setSelection(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selection]);

  const setCreateTaskOpenWithFocus = useCallback((open: boolean) => {
    if (open) {
      createTaskReturnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    setCreateTaskOpen(open);
    if (!open) {
      window.setTimeout(() => {
        if (createTaskReturnFocus.current?.isConnected) createTaskReturnFocus.current.focus();
      }, 0);
    }
  }, []);

  const openCreateTask = useCallback(() => {
    if (route === "tasks") {
      setCreateTaskOpenWithFocus(true);
      return;
    }
    guardNavigation(() => {
      window.history.pushState({}, "", "/tasks");
      setRoute("tasks");
      setSelection(null);
      setUniverseDocumentId(null);
      setMobileMore(false);
      setCreateTaskOpenWithFocus(true);
    });
  }, [guardNavigation, route, setCreateTaskOpenWithFocus]);

  const openSkill = useCallback((skillId: string) => {
    guardNavigation(() => {
      window.history.pushState({}, "", `/skills?skill=${encodeURIComponent(skillId)}`);
      setRoute("skills");
      setSelection(null);
      setMobileMore(false);
    });
  }, [guardNavigation]);

  const showDocumentInGraph = useCallback((documentId: string) => {
    guardNavigation(() => {
      window.history.pushState({}, "", `/universe?document=${encodeURIComponent(documentId)}`);
      setUniverseDocumentId(documentId);
      setRoute("universe");
      setSelection(null);
      setMobileMore(false);
    });
  }, [guardNavigation]);

  const groups = useMemo(
    () => ["Пространство", "Оркестрация", "Реестр", "Доверие"].map((group) => ({
      group,
      routes: routeKeys.filter((key) => routeMeta[key].group === group)
    })),
    []
  );

  const page = (() => {
    switch (route) {
      case "command-center": return <CommandCenter onCreateTask={openCreateTask} onNavigate={navigate} onSelect={setSelection} />;
      case "handbook": return <Handbook />;
      case "onboarding": return <Onboarding />;
      case "documents": return <Documents onShowInGraph={showDocumentInGraph} />;
      case "tasks": return <Tasks createOpen={createTaskOpen} onCreateOpenChange={setCreateTaskOpenWithFocus} onSelect={setSelection} />;
      case "universe": return <Universe theme={theme} selectedId={selection?.id ?? null} focusedDocumentId={universeDocumentId} onSelect={setSelection} onClear={() => setSelection(null)} />;
      case "runs": return <Runs onSelect={setSelection} />;
      case "agents": return <AgentsSurface onOpenSkill={openSkill} />;
      case "skills": return <SkillsSurface />;
      case "context": return <Context onSelect={setSelection} />;
      case "safety": return <Safety />;
      case "systems": return <SystemSections />;
    }
  })();

  const current = routeMeta[route];
  const emergencyBlocked = Boolean(platform.data?.emergency_state?.active_actions?.length);
  const runtimeEnabled = Boolean(execution.data?.features?.runtime_execution_enabled) && !emergencyBlocked;
  const runtimeLabel = execution.isError
    ? "состояние выполнения недоступно"
    : execution.isLoading
      ? "проверяем выполнение"
      : runtimeEnabled
        ? "выполнение включено"
        : "выполнение отключено";
  const themeIcon = theme === "dark" ? <MoonStar size={17} /> : theme === "light" ? <Sun size={17} /> : <Gauge size={17} />;
  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${selection ? "has-inspector" : ""}`}>
      <aside className="sidebar" aria-label="Основная навигация">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="-1 51 341 240" xmlns="http://www.w3.org/2000/svg" role="img">
              <path d="M179.948 285.174V260.883H338.652V285.174H179.948Z" fill="currentColor" />
              <path d="M-0.647949 247.767V214.048L110.39 161.318C113.204 159.981 126.018 153.442 128.048 152.513C125.934 151.563 113.219 145.453 110.39 144.1L-0.647949 91.0115V56.2168L157.353 132.981V171.003L-0.647949 247.767Z" fill="currentColor" />
            </svg>
          </span>
          <span className="brand-copy"><img className="brand-wordmark" src={brandWordmarkUrl} alt="raytsystem" /><small>агентная система</small></span>
          <button className="collapse-sidebar" type="button" onClick={() => setSidebarCollapsed((value) => !value)} aria-label={sidebarCollapsed ? "Развернуть навигацию" : "Свернуть навигацию"}><ChevronLeft size={16} /></button>
        </div>
        <nav>
          {groups.map(({ group, routes }) => (
            <div className="nav-group" key={group}>
              <span className="nav-label">{group}</span>
              {routes.map((key) => {
                const Icon = routeMeta[key].icon;
                return (
                  <button type="button" className={route === key ? "active" : ""} key={key} onClick={() => navigate(key)} aria-current={route === key ? "page" : undefined} title={routeMeta[key].label}>
                    <Icon size={18} aria-hidden="true" /><span>{routeMeta[key].label}</span>
                    {key === "tasks" && system.data?.attention.blocked_tasks ? <b>{system.data.attention.blocked_tasks}</b> : null}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="local-card"><span className="local-orb"><Database size={16} /></span><span><strong>Локальное пространство</strong><small><i /> проверенный срез</small></span></div>
          <button type="button" onClick={() => navigate("safety")}><ShieldCheck size={16} /><span>{runtimeLabel}</span></button>
        </div>
      </aside>

      <section className="workspace-shell" data-testid="workspace-shell">
        <header className="topbar" data-testid="topbar">
          <div className="topbar-title"><span className="eyebrow">{current.group}</span><h1>{current.label}</h1><p>{current.description}</p></div>
          <div className="topbar-actions">
            <button className="command-trigger" type="button" onClick={() => setPaletteOpen(true)}><Search size={16} /><span>Палитра команд</span><kbd>⌘ K</kbd></button>
            <span className="top-local"><i /><span>ЛОКАЛЬНО</span></span>
            <button className="icon-button" type="button" onClick={() => setTheme(theme === "dark" ? "light" : theme === "light" ? "contrast" : "dark")} aria-label="Сменить цветовую тему" title={`Тема: ${theme === "dark" ? "тёмная" : theme === "light" ? "светлая" : "контрастная"}`}>{themeIcon}</button>
            {selection ? <button className="icon-button inspector-toggle" type="button" onClick={() => setSelection(null)} aria-label="Закрыть инспектор"><PanelRightClose size={18} /></button> : <button className="icon-button inspector-toggle" type="button" disabled aria-label="Объект не выбран"><PanelRightOpen size={18} /></button>}
          </div>
        </header>
        <main ref={mainContentRef} id="main-content" className="main-content" tabIndex={0}>
          {platform.data?.emergency_state?.active_actions?.length ? (
            <aside className="global-emergency" role="alert">
              <ShieldCheck size={17} aria-hidden="true" />
              <strong>Аварийный контур активен</strong>
              <span>{platform.data.emergency_state.active_actions.join(" · ")}</span>
              <span className="status-pill status-blocked"><i className="status-shape" />выполнение заблокировано</span>
            </aside>
          ) : null}
          <ErrorBoundary key={route} label={current.label}>{page}</ErrorBoundary>
        </main>
        <footer className="activity-strip">
          <span><Activity size={13} /><i /> проверено</span>
          <span>знания <code>{shortId(system.data?.fingerprint.knowledge_generation_id)}</code></span>
          <span>задачи <code>{shortId(system.data?.fingerprint.task_generation_id)}</code></span>
          <span>каталог <code>{shortId(system.data?.fingerprint.catalog_sha256)}</code></span>
          <span className="activity-boundary"><ShieldCheck size={13} /> {runtimeLabel}</span>
        </footer>
      </section>

      <ErrorBoundary key={`${selection?.kind ?? "none"}:${selection?.id ?? "none"}:${selection?.snapshotId ?? "none"}`} label="Инспектор">
        <Inspector selection={selection} onClose={() => setSelection(null)} onSelect={setSelection} onCreateTask={openCreateTask} />
      </ErrorBoundary>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} onNavigate={navigate} onSelect={setSelection} onCreateTask={openCreateTask} />
      {navigationConfirmOpen ? (
        <Dialog className="small-modal panel" role="alertdialog" labelledBy="leave-editor-title" describedBy="leave-editor-description" closeOnBackdrop={false} initialFocus="cancel" onClose={() => { pendingNavigationRef.current = null; setNavigationConfirmOpen(false); }}>
          <header><div><span className="eyebrow">Несохранённые изменения</span><h2 id="leave-editor-title">Покинуть редактор?</h2></div></header>
          <p id="leave-editor-description">Несохранённые изменения останутся только в текущем браузерном состоянии и могут быть потеряны после ухода со страницы.</p>
          <footer><button type="button" data-dialog-cancel onClick={() => { pendingNavigationRef.current = null; setNavigationConfirmOpen(false); }}>Продолжить редактирование</button><button className="danger-button" type="button" onClick={() => { const action = pendingNavigationRef.current; pendingNavigationRef.current = null; setNavigationConfirmOpen(false); action?.(); }}>Покинуть без сохранения</button></footer>
        </Dialog>
      ) : null}

      <nav className="mobile-nav" aria-label="Мобильная навигация">
        {(["command-center", "documents", "tasks", "universe"] as RouteKey[]).map((key) => {
          const Icon = routeMeta[key].icon;
          const mobileLabel = key === "command-center" ? "Главная" : key === "universe" ? "Граф" : routeMeta[key].label;
          return <button type="button" className={route === key ? "active" : ""} key={key} onClick={() => navigate(key)}><Icon size={19} /><span>{mobileLabel}</span></button>;
        })}
        <button type="button" className={mobileMore ? "active" : ""} onClick={() => setMobileMore(true)}><Menu size={19} /><span>Ещё</span></button>
      </nav>
      {mobileMore ? (
        <Dialog className="mobile-more-sheet" backdropClassName="mobile-more modal-backdrop" label="Дополнительная навигация" onClose={() => setMobileMore(false)}>
            <header><strong>Ещё</strong><button className="icon-button" type="button" onClick={() => setMobileMore(false)} aria-label="Закрыть"><X size={18} /></button></header>
            {(["handbook", "onboarding", "runs", "agents", "skills", "context", "safety", "systems"] as RouteKey[]).map((key) => {
              const Icon = routeMeta[key].icon;
              return <button type="button" key={key} onClick={() => navigate(key)}><Icon size={19} /><span><strong>{routeMeta[key].label}</strong><small>{routeMeta[key].description}</small></span></button>;
            })}
        </Dialog>
      ) : null}
    </div>
  );
}
