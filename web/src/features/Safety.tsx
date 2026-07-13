import { Ban, Check, CircleDot, Fingerprint, KeyRound, LockKeyhole, Network, Radar, ShieldCheck, WifiOff } from "lucide-react";
import { useCatalog, useSystem } from "../hooks";
import { usePlatformFeatures } from "../featureHooks";
import { catalogDescription, isolationLabel, localizedCatalogLabel } from "../presentation";
import { ErrorState, LoadingState, StatusPill } from "../components/StatePanel";

export function Safety() {
  const system = useSystem();
  const catalog = useCatalog();
  const platform = usePlatformFeatures();
  if (system.isLoading || catalog.isLoading) return <LoadingState label="Проверяем границу безопасности…" />;
  if (system.isError || catalog.isError || !system.data || !catalog.data) return <ErrorState error={system.error ?? catalog.error} />;
  return (
    <div className="route safety-route">
      <section className="safety-hero panel">
        <div className="safety-seal"><ShieldCheck size={34} /><span /></div>
        <div><span className="eyebrow">Граница доверия · активна</span><h2>Локальность заложена в архитектуру.</h2><p>Браузер может менять отдельный журнал задач и перестраиваемую проекцию графа кода. Он не может запускать агентов, выполнять shell-команды или изменять канонические знания.</p></div>
        <StatusPill status="verified" label="граница проверена" />
      </section>
      <div className="safety-grid">
        <section className="panel safety-card"><Network size={20} /><span className="eyebrow">Сеть</span><h3>Только loopback</h3><ul><li><Check />Привязка к 127.0.0.1</li><li><Check />Точное совпадение Host + Origin</li><li><WifiOff />Без внешних ресурсов и аналитики</li></ul></section>
        <section className="panel safety-card"><KeyRound size={20} /><span className="eyebrow">Сессия браузера</span><h3>Двойная защита записи</h3><ul><li><Check />HttpOnly SameSite cookie</li><li><Check />Отдельный заголовок CSRF</li><li><Check />Идемпотентность каждой записи</li></ul></section>
        <section className="panel safety-card"><Fingerprint size={20} /><span className="eyebrow">Состояние</span><h3>Привязка к поколению</h3><ul><li><Check />SHA-256 знаний</li><li><Check />Указатель поколения задач</li><li><Check />Отпечаток каталога</li></ul></section>
        <section className="panel safety-card"><Ban size={20} /><span className="eyebrow">Недоступно по замыслу</span><h3>Без скрытого выполнения</h3><ul><li><LockKeyhole />Нет endpoint командной оболочки</li><li><LockKeyhole />Нет публикации через веб</li><li><LockKeyhole />Нет внешних изменений</li></ul></section>
      </div>
      <section className="adapter-matrix panel">
        <header className="panel-header"><div><span className="eyebrow">Адаптеры среды выполнения</span><h3>Контракты без подразумеваемых возможностей</h3></div><Radar size={21} /></header>
        {catalog.data.adapters.map((adapter) => (
          <div className="adapter-row" key={adapter.adapter_id}>
            <span className="adapter-mark"><CircleDot size={16} /></span>
            <span><strong>{localizedCatalogLabel(adapter.adapter_id, adapter.name)}</strong><small>{isolationLabel(adapter.isolation_mode)}</small></span>
            <StatusPill status={adapter.state} />
            <p>{catalogDescription(adapter.adapter_id, adapter.reason ?? "Причина не указана.")}</p>
          </div>
        ))}
      </section>
      <section className="policy-boundary panel">
        <div><span className="eyebrow">Граница подтверждения</span><h3>Все необратимые действия остаются за пределами этой версии.</h3></div>
        <div className="policy-flow"><span>намерение в браузере</span><i /> <span>типизированная проверка</span><i /> <span>задачи / derived-граф</span><i className="stopped" /> <span className="denied">каноническое или внешнее действие</span></div>
      </section>
      <section className="safety-monitor panel">
        <header className="panel-header"><div><span className="eyebrow">Self-monitoring</span><h3>Операционный контур сообщает о себе честно</h3></div>{platform.data ? <StatusPill status={platform.data.state} /> : null}</header>
        {platform.isLoading ? <LoadingState label="Читаем операционное состояние…" /> : null}
        {platform.isError ? <ErrorState error={platform.error} onRetry={() => void platform.refetch()} /> : null}
        {platform.data ? (
          <div className="safety-monitor-grid">
            <div><strong>{Object.values(platform.data.active_feature_flags ?? {}).filter(Boolean).length}</strong><small>active feature flags</small></div>
            <div><strong>{platform.data.event_backlog ?? 0}</strong><small>append-only audit events</small></div>
            <div><strong>{platform.data.notification_backlog ?? 0}</strong><small>notification backlog</small></div>
            <div><strong>{platform.data.outbox_backlog ?? 0}</strong><small>external outbox drafts</small></div>
            <div><strong>{platform.data.eval_regression_count ?? 0}</strong><small>eval regressions</small></div>
            <div><strong>{platform.data.circuit_breakers?.length ?? 0}</strong><small>open circuit breakers</small></div>
            <div><strong>{platform.data.mcp_health ?? "unavailable"}</strong><small>MCP health</small></div>
            <div><strong>{platform.data.a2a_state ?? "disabled"}</strong><small>A2A exposure: {platform.data.a2a_network_exposure ? "network" : "none"}</small></div>
            <div><strong>{platform.data.encryption_provider?.state ?? "unavailable"}</strong><small>encryption provider</small></div>
            <div><strong>{platform.data.last_successful_backup ? "recorded" : "none"}</strong><small>last successful backup</small></div>
          </div>
        ) : null}
      </section>
    </div>
  );
}
