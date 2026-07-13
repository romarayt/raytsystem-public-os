import {
  AlertOctagon,
  AlertTriangle,
  CircleCheck,
  CircleDashed,
  Clock3,
  LockKeyhole,
  ShieldQuestion,
  WifiOff
} from "lucide-react";
import type { ReactNode } from "react";
import type { OperationalState } from "../featureTypes";
import { StatusPill } from "./StatePanel";

const stateCopy: Record<string, { title: string; detail: string; icon: typeof AlertTriangle }> = {
  empty: {
    title: "Пока нет записей",
    detail: "Система готова и покажет здесь первую подтверждённую запись.",
    icon: CircleDashed
  },
  disabled: {
    title: "Функция выключена",
    detail: "Она останется недоступной, пока не будет явно включена в локальной конфигурации.",
    icon: LockKeyhole
  },
  unavailable: {
    title: "Локальный источник недоступен",
    detail: "Инициализируйте операционное хранилище или выполните проверку состояния.",
    icon: WifiOff
  },
  stale: {
    title: "Срез устарел",
    detail: "Обновите данные перед любым действием — запись по старому срезу будет отклонена.",
    icon: Clock3
  },
  degraded: {
    title: "Работа ограничена",
    detail: "Часть локальных возможностей недоступна; безопасные данные ниже остаются доступными.",
    icon: AlertTriangle
  },
  blocked: {
    title: "Операции заблокированы",
    detail: "Политика или аварийный контур запретили выполнение. Причина сохранена в аудите.",
    icon: AlertOctagon
  },
  approval_required: {
    title: "Требуется подтверждение",
    detail: "Действие не начнётся, пока не появится новое подтверждение с подходящей областью.",
    icon: ShieldQuestion
  },
  error: {
    title: "Система сообщила об ошибке",
    detail: "Состояние сохранено без попытки скрыть сбой. Проверьте журнал и повторите безопасную операцию.",
    icon: AlertTriangle
  },
  success: {
    title: "Операция подтверждена",
    detail: "Результат зафиксирован в локальном журнале.",
    icon: CircleCheck
  }
};

export function OperationalNotice({ state }: { state: OperationalState }) {
  const normalized = state === "catalog_only" ? "degraded" : state;
  if (normalized === "ready") return null;
  const copy = stateCopy[normalized] ?? stateCopy.degraded;
  const Icon = copy.icon;
  return (
    <aside className={`systems-notice systems-notice-${normalized}`} role={normalized === "error" || normalized === "blocked" ? "alert" : "status"}>
      <Icon size={19} aria-hidden="true" />
      <span><strong>{copy.title}</strong><small>{copy.detail}</small></span>
      <StatusPill status={state} />
    </aside>
  );
}

export function ActionBoundary({
  scope,
  effect,
  approval,
  recovery,
  children
}: {
  scope: string;
  effect: string;
  approval: string;
  recovery: string;
  children?: ReactNode;
}) {
  return (
    <div className="action-boundary">
      <dl>
        <div><dt>Область</dt><dd>{scope}</dd></div>
        <div><dt>Ожидаемый эффект</dt><dd>{effect}</dd></div>
        <div><dt>Подтверждение</dt><dd>{approval}</dd></div>
        <div><dt>Восстановление</dt><dd>{recovery}</dd></div>
      </dl>
      {children}
    </div>
  );
}
