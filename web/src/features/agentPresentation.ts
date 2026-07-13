import type { AgentReadiness } from "../executionTypes";

const reasonCopy: Record<string, string> = {
  digital_employees_disabled: "Цифровые сотрудники отключены",
  runtime_execution_disabled: "Выполнение отключено",
  runtime_adapter_disabled: "Адаптер отключён",
  catalog_definition_disabled: "Определение не активировано",
  execution_store_uninitialized: "Runtime не настроен",
  operational_record_missing: "Только каталог",
  configuration_revision_changed: "Конфигурация изменилась",
  definition_missing: "Определение агента отсутствует",
  duplicate_execution_records: "Найдены конфликтующие execution records",
  employee_identity_mismatch: "Execution identity не совпадает с определением",
  persisted_operational_state: "Операционное состояние доступно"
};

const readinessCopy: Record<AgentReadiness, string> = {
  ready: "Готов",
  disabled: "Отключён",
  catalog_only: "Только каталог",
  running: "Выполняет задачу",
  requires_configuration: "Требует настройки",
  degraded: "Нарушена целостность"
};

const boundaryCopy: Record<string, string> = {
  canonical_knowledge_write: "Запись в канонические знания",
  external_side_effects: "Внешние побочные эффекты",
  runtime_output_is_untrusted: "Runtime output считается недоверенным"
};

const limitationCopy: Record<string, string> = {
  catalog_definition_is_inert: "Определение каталога инертно и само по себе не выполняется",
  sensitive_runtime_fields_are_omitted: "Чувствительные runtime-поля скрыты из проекции"
};

const valueCopy: Record<string, string> = {
  approval_required: "Требует подтверждения",
  allow: "Разрешено",
  allowed: "Разрешено",
  deny: "Запрещено",
  denied: "Запрещено",
  read: "Чтение",
  write: "Запись",
  read_write: "Чтение и запись",
  none: "Нет",
  block_new: "Блокировать новые запуски",
  cancel_active: "Отменять активные запуски",
  external_send: "Внешняя отправка",
  filesystem_write: "Запись в файловую систему",
  git_write: "Запись в Git",
  network_egress: "Внешний сетевой доступ",
  tool_use: "Использование инструмента",
  workspace_read: "Чтение workspace",
  staged_write: "Запись в staged-область",
  agent_configuration_changed: "Конфигурация агента изменена",
  assignment_created: "Назначение создано",
  run_started: "Запуск начат",
  run_completed: "Запуск завершён"
};

function humanize(value: string): string {
  const normalized = value.replaceAll("_", " ").trim();
  return normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : "Не указано";
}

export function agentReasonLabel(reason: string): string {
  return reasonCopy[reason] ?? humanize(reason);
}

export function agentReadinessLabel(readiness: AgentReadiness): string {
  return readinessCopy[readiness];
}

export function filesystemModeLabel(mode: string): string {
  if (mode === "task_worktree") return "изолированный worktree";
  if (mode === "workspace_root_readonly") return "корень только для чтения";
  if (mode === "approved_external_root") return "одобренный внешний корень";
  if (mode === "read_only") return "только чтение";
  if (mode === "staging_only") return "только staged-изменения";
  if (mode === "none") return "без доступа";
  return humanize(mode);
}

export function booleanLabel(value: boolean): string {
  return value ? "Да" : "Нет";
}

export function boundaryLabel(boundary: string): string {
  return boundaryCopy[boundary] ?? humanize(boundary);
}

export function safeValueLabel(value: boolean | string): string {
  if (typeof value === "boolean") return value ? "Разрешено" : "Запрещено";
  return valueCopy[value.toLowerCase()] ?? humanize(value);
}

export function limitationLabel(limitation: string): string {
  return limitationCopy[limitation] ?? humanize(limitation);
}

export function accessValueLabel(value: string): string {
  return valueCopy[value.toLowerCase()] ?? humanize(value);
}

export function activityLabel(value: string): string {
  return valueCopy[value.toLowerCase()] ?? humanize(value);
}
