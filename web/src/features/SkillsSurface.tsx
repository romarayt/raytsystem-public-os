import { Copy, FileText, Pencil, Search, ShieldCheck, Users, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { shortId } from "../api";
import { EmptyState, ErrorState, LoadingState, StatusPill } from "../components/StatePanel";
import { catalogDescription, localizedCatalogLabel, roleLabel, statusLabel } from "../presentation";
import { useSkills } from "../skillHooks";
import type { SkillWriteResult } from "../types";
import { SkillDetailView } from "./SkillDetailView";

function skillFromLocation(): string | null {
  return new URLSearchParams(window.location.search).get("skill");
}

export function SkillsSurface() {
  const skillsQuery = useSkills();
  const [query, setQuery] = useState("");
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(skillFromLocation);
  const [expectedOverride, setExpectedOverride] = useState<string | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const onPop = () => {
      setSelectedSkillId(skillFromLocation());
      setExpectedOverride(null);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    if (selectedSkillId !== null) return;
    const frame = window.requestAnimationFrame(() => {
      if (returnFocusRef.current?.isConnected) returnFocusRef.current.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [selectedSkillId]);

  const openSkill = (skillId: string, replace = false) => {
    if (!replace && document.activeElement instanceof HTMLElement) {
      returnFocusRef.current = document.activeElement;
    }
    const next = `/skills?skill=${encodeURIComponent(skillId)}`;
    if (replace) window.history.replaceState({}, "", next);
    else window.history.pushState({}, "", next);
    setSelectedSkillId(skillId);
  };

  const closeSkill = () => {
    window.history.pushState({}, "", "/skills");
    setSelectedSkillId(null);
    setExpectedOverride(null);
  };

  const onRevisionChanged = (result: SkillWriteResult) => {
    setExpectedOverride(result.catalog_sha256);
  };

  const onForkCreated = (result: SkillWriteResult) => {
    setExpectedOverride(result.catalog_sha256);
    openSkill(result.skill_id, true);
  };

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("ru-RU");
    if (!needle) return skillsQuery.data?.skills ?? [];
    return (skillsQuery.data?.skills ?? []).filter((skill) => {
      const relatedAgents = skill.related_agents.flatMap((agent) => [agent.agent_id, agent.name, agent.role, roleLabel(agent.role)]);
      return [
        skill.skill_id,
        skill.name,
        skill.description,
        catalogDescription(skill.skill_id, skill.description),
        skill.pack_id,
        localizedCatalogLabel(skill.pack_id, skill.pack_id),
        skill.version,
        skill.trust_class,
        skill.sensitivity,
        ...skill.permissions,
        ...relatedAgents
      ].join(" ").toLocaleLowerCase("ru-RU").includes(needle);
    });
  }, [query, skillsQuery.data?.skills]);

  const expectedCatalogSha256 = expectedOverride ?? skillsQuery.data?.catalog_sha256 ?? null;

  return (
    <section className="route route-list skills-route">
      {selectedSkillId && expectedCatalogSha256 ? (
        <SkillDetailView
          key={selectedSkillId}
          skillId={selectedSkillId}
          expectedCatalogSha256={expectedCatalogSha256}
          onBack={closeSkill}
          onRevisionChanged={onRevisionChanged}
          onForkCreated={onForkCreated}
        />
      ) : (
        <>
          <div className="route-tools skill-list-tools">
            <label className="search-field"><Search size={16} /><input aria-label="Поиск skills" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Имя, ID, описание, pack, permission или агент" /></label>
            <span className="inert-badge"><ShieldCheck size={14} /> Markdown остаётся inert data</span>
          </div>

          {skillsQuery.isLoading ? <LoadingState label="Проверяем каталог и policy skills…" /> : null}
          {skillsQuery.isError ? <ErrorState error={skillsQuery.error} onRetry={() => void skillsQuery.refetch()} /> : null}
          {!skillsQuery.isLoading && !skillsQuery.isError && !filtered.length ? (
            <EmptyState title={query ? "Skills не найдены" : "Нет разрешённых skills"} action={query ? <button className="secondary-button" type="button" onClick={() => setQuery("")}>Сбросить поиск</button> : undefined}>
              {query ? "Измените запрос или проверьте канонический skill_id." : "Skills появляются только из разрешённого workspace-каталога."}
            </EmptyState>
          ) : null}

          {filtered.length ? (
            <div className="skill-card-grid" aria-label="Каталог skills">
              {filtered.map((skill) => (
                <article className="skill-card panel" key={skill.skill_id} aria-labelledby={`skill-card-title-${skill.skill_id}`}>
                  <header>
                    <span className="catalog-icon skill"><Wrench size={19} aria-hidden="true" /></span>
                    <span className="skill-card-title"><span className="eyebrow">{localizedCatalogLabel(skill.pack_id, skill.pack_id)} · {skill.version}</span><strong id={`skill-card-title-${skill.skill_id}`}>{skill.skill_id}</strong></span>
                    <StatusPill status={skill.test_status} />
                  </header>
                  <p>{catalogDescription(skill.skill_id, skill.description)}</p>
                  <dl>
                    <div><dt>Доверие</dt><dd>{statusLabel(skill.trust_class)}</dd></div>
                    <div><dt>Чувствительность</dt><dd>{statusLabel(skill.sensitivity)}</dd></div>
                    <div><dt>Разрешения</dt><dd title={skill.permissions.join(", ")}>{skill.permissions.length}{skill.permissions.length ? ` · ${skill.permissions.join(", ")}` : " · не объявлены"}</dd></div>
                    <div><dt>Источник</dt><dd><code>{shortId(skill.source_sha256, 8, 5)}</code></dd></div>
                  </dl>
                  <div className="skill-card-relations"><Users size={14} /><span>{skill.related_agents.length ? skill.related_agents.map((agent) => agent.name).join(", ") : "Не назначен агентам"}</span></div>
                  <footer>
                    <span className={`skill-editability ${skill.policy.editable ? "editable" : "read-only"}`}>
                      {skill.policy.editable ? <><Pencil size={13} />Редактируемый</> : skill.policy.forkable ? <><Copy size={13} />Только чтение · можно копировать</> : <><FileText size={13} />Только чтение</>}
                    </span>
                    <StatusPill status={skill.enabled ? "enabled" : "restricted"} />
                  </footer>
                  <button className="skill-card-open" type="button" onClick={() => openSkill(skill.skill_id)} aria-label={`Открыть skill ${skill.skill_id}`} />
                </article>
              ))}
            </div>
          ) : null}

          {skillsQuery.data ? <section className="skill-list-summary panel"><span><strong>{skillsQuery.data.skills.length}</strong> skills</span><span><strong>{skillsQuery.data.skills.filter((skill) => skill.policy.editable).length}</strong> редактируемых</span><span><strong>{skillsQuery.data.skills.filter((skill) => !skill.policy.editable).length}</strong> read-only</span><code>{shortId(skillsQuery.data.catalog_sha256, 12, 8)}</code></section> : null}
        </>
      )}
    </section>
  );
}
