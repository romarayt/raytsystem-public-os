import type { GraphLens, TaskPriority, TaskStatus } from "./types";

export const routeCopy = {
  "command-center": {
    label: "Центр управления",
    description: "Состояние пространства и активная работа",
    group: "Пространство"
  },
  handbook: {
    label: "База знаний",
    description: "Документация raytsystem: установка, интерфейс, граф, безопасность",
    group: "Пространство"
  },
  documents: {
    label: "Документы",
    description: "Управляемые файлы и заметки текущего workspace",
    group: "Пространство"
  },
  onboarding: {
    label: "Подключить",
    description: "Установить raytsystem в репозиторий или папку",
    group: "Пространство"
  },
  tasks: {
    label: "Задачи",
    description: "Операционный журнал без перезаписи истории",
    group: "Оркестрация"
  },
  universe: {
    label: "Вселенная",
    description: "Граф знаний, работы и доказательств",
    group: "Оркестрация"
  },
  runs: {
    label: "Запуски",
    description: "История зафиксированных операций",
    group: "Оркестрация"
  },
  agents: {
    label: "Агенты",
    description: "Независимые от провайдера профили",
    group: "Реестр"
  },
  skills: {
    label: "Навыки",
    description: "Процедуры с зафиксированным хешем",
    group: "Реестр"
  },
  context: {
    label: "Контекст",
    description: "Разрешённые документы с инструкциями",
    group: "Реестр"
  },
  safety: {
    label: "Безопасность",
    description: "Локальная граница и адаптеры",
    group: "Доверие"
  },
  systems: {
    label: "Системы",
    description: "Качество, политика и восстановление",
    group: "Доверие"
  }
} as const;

export type RouteKey = keyof typeof routeCopy;

const statusCopy: Record<string, string> = {
  inbox: "Входящие",
  planned: "Запланировано",
  ready: "Готово",
  idle: "Готов",
  assigned: "Назначено",
  running: "В работе",
  paused: "Приостановлено",
  terminated: "Остановлено",
  queued: "В очереди",
  preparing: "Подготовка",
  cancelling: "Отмена",
  completed: "Завершено",
  incompatible: "Несовместимо",
  review: "На проверке",
  blocked: "Заблокировано",
  done: "Завершено",
  cancelled: "Отменено",
  succeeded: "Успешно",
  terminal_failed: "Ошибка",
  failed: "Ошибка",
  quarantined: "Карантин",
  pass: "Проверено",
  verified: "Проверено",
  enabled: "Включено",
  active: "Активно",
  supported: "Подтверждено",
  confirmed: "Подтверждено",
  configured: "Настроено",
  available: "Доступно",
  degraded: "Ограниченно",
  restricted: "Ограничено",
  retracted: "Отозвано",
  pending: "Ожидает",
  optional: "Опционально",
  awaiting_review: "Ждёт проверки",
  awaiting_approval: "Ждёт подтверждения",
  disabled: "Отключено",
  declared: "Объявлено",
  superseded: "Заменено",
  disputed: "Оспорено",
  internal: "Внутреннее",
  public: "Публичное",
  official: "Официальное",
  user: "Пользовательское",
  trusted: "Доверенное",
  community: "Сообщество",
  personal: "Личное",
  local_only: "Только локально",
  unavailable: "Недоступно",
  draft: "Черновик",
  current: "Актуально",
  unchecked: "Нужна проверка",
  stale: "Устарело",
  missing: "Не построено",
  building: "Обновляется",
  error: "Ошибка",
  extracted: "Извлечено",
  inferred: "Предположено",
  ambiguous: "Неоднозначно"
};

const kindCopy: Record<string, string> = {
  workspace: "Рабочее пространство",
  generation: "Срез знаний",
  task_generation: "Срез задач",
  instruction: "Инструкция",
  pack: "Пакет",
  agent: "Агент",
  skill: "Навык",
  adapter: "Адаптер",
  task: "Задача",
  run: "Запуск",
  claim: "Утверждение",
  entity: "Сущность",
  evidence: "Доказательство",
  source: "Источник",
  manual_document: "Ручной документ",
  documentation_document: "Документация",
  generated_document: "Защищённый документ",
  document: "Документ",
  repository: "Репозиторий",
  directory: "Каталог",
  file: "Файл",
  module: "Модуль",
  package: "Пакет кода",
  class: "Класс",
  function: "Функция",
  method: "Метод",
  api_endpoint: "API-метод",
  database_table: "Таблица БД",
  database_schema: "Схема БД",
  configuration: "Конфигурация",
  test: "Тест",
  adr: "ADR",
  rationale: "Обоснование",
  dependency: "Зависимость"
};

const fieldCopy: Record<string, string> = {
  status: "Статус",
  state: "Состояние",
  role: "Роль",
  adapter: "Адаптер",
  adapter_state: "Состояние адаптера",
  skills: "Навыки",
  filesystem_request: "Доступ к файлам",
  requested_filesystem_mode: "Доступ к файлам",
  egress: "Передача данных",
  egress_destination: "Канал передачи",
  trust: "Уровень доверия",
  trust_class: "Уровень доверия",
  sensitivity: "Чувствительность",
  test_status: "Проверка",
  permissions: "Разрешения",
  sha256: "SHA-256",
  content_sha256: "SHA-256 содержимого",
  excerpt_sha256: "SHA-256 фрагмента",
  manifest_sha256: "SHA-256 манифеста",
  size_bytes: "Размер, байт",
  editable: "Можно редактировать",
  priority: "Приоритет",
  project: "Проект",
  project_id: "Проект",
  revision: "Ревизия",
  dependencies: "Зависимости",
  dependency_ids: "Зависимости",
  run_id: "ID запуска",
  generation_id: "ID поколения",
  semantic_noop: "Без смысловых изменений",
  claim_id: "ID утверждения",
  entity_id: "ID сущности",
  evidence_id: "ID доказательства",
  source_id: "ID источника",
  source_label: "Источник",
  statement: "Утверждение",
  language: "Язык",
  evidence_ids: "Доказательства",
  relation_ids: "Связи",
  supersedes: "Заменяет",
  contradicts: "Противоречит",
  recorded_at: "Зафиксировано",
  created_at: "Создано",
  updated_at: "Обновлено",
  source_type: "Тип источника",
  entity_type: "Тип сущности",
  locator_kind: "Тип указателя",
  source_ref: "Ссылка на источник",
  capabilities: "Возможности",
  isolation_mode: "Режим изоляции",
  qualified_name: "Полное имя",
  path: "Путь",
  start_line: "Начальная строка",
  end_line: "Конечная строка",
  community_id: "Сообщество",
  is_god: "Узловой центр",
  is_bridge: "Мост",
  incoming_edges: "Входящие связи",
  outgoing_edges: "Исходящие связи",
  extractor: "Экстрактор",
  extractor_version: "Версия экстрактора",
  content_fingerprint: "Отпечаток содержимого"
};

const priorityCopy: Record<TaskPriority, string> = {
  low: "Низкий",
  normal: "Обычный",
  high: "Высокий",
  urgent: "Срочный"
};

const lensCopy: Record<GraphLens, string> = {
  universe: "Орбита",
  knowledge: "Знания",
  work: "Работа",
  agent: "Агенты",
  evidence: "Доказательства",
  code: "Код"
};

const relationCopy: Record<string, string> = {
  contains: "содержит",
  defines: "определяет",
  imports: "импортирует",
  calls: "вызывает",
  inherits: "наследует",
  implements: "реализует",
  references: "ссылается",
  links_to: "ссылается на",
  embeds: "встраивает",
  depends_on: "зависит от",
  configured_by: "настроено через",
  tests: "тестирует",
  explained_by: "объясняется",
  verifies: "проверяет"
};

const operationCopy: Record<string, string> = {
  ingest: "Импорт",
  query: "Запрос",
  lint: "Проверка",
  save: "Сохранение",
  promote: "Публикация поколения",
  rebuild: "Пересборка"
};

const capabilityCopy: Record<string, string> = {
  research: "исследование",
  planning: "планирование",
  implementation: "реализация",
  review: "проверка",
  knowledge_curation: "курация знаний",
  task_decomposition: "декомпозиция задач",
  evidence_collection: "сбор доказательств",
  source_verification: "проверка источников",
  draft_generation: "подготовка черновиков"
};

const localizedCatalogLabelCopy: Record<string, string> = {
  pack_core: "Основные процедуры raytsystem",
  pack_starter: "Универсальные стартовые агенты",
  pack_local: "Локальные skills",
  adapter_disabled: "Только каталог",
  adapter_codex_local: "Локальный коннектор Codex",
  adapter_claude_code: "Коннектор Claude Code",
  adapter_hermes: "Коннектор Hermes",
  adapter_openhands: "Коннектор OpenHands",
  instruction_agents: "Маршрутизация Codex",
  instruction_work: "Запуск в ChatGPT Work",
  instruction_claude: "Контекст Claude Code"
};

const catalogDescriptionCopy: Record<string, string> = {
  agent_builder: "Создаёт проектные реализации в явных границах staging, не выходя за пределы рабочего пространства.",
  agent_librarian: "Курирует находящиеся поиском предложения знаний, сохраняя доказательства, противоречия и историю.",
  agent_orchestrator: "Декомпозирует миссию на ограниченные задачи, назначает проверки и оставляет полномочия у пользователя.",
  agent_researcher: "Собирает первичные доказательства и возвращает структурированные предложения с привязкой к источникам.",
  agent_reviewer: "Независимо проверяет архитектуру, доказательства, безопасность и тесты без права публикации.",
  pack_core: "Процедуры с приоритетом происхождения данных и явные точки входа для инструкций рабочего пространства.",
  pack_starter: "Пять пассивных, независимых от провайдера агентов для планирования, исследования, реализации, проверки и курации знаний.",
  adapter_disabled: "В этой версии веб-интерфейса выполнение намеренно отключено.",
  adapter_codex_local: "Доступен только контракт; проверенный мост запуска не включён.",
  adapter_claude_code: "Доступен только контракт; проверенный мост запуска не включён.",
  adapter_hermes: "Доступен только контракт; установка и выполнение требуют отдельного решения.",
  adapter_openhands: "Доступен только контракт; сервер OpenHands не настроен.",
  "raytsystem-ingest": "Захватывает, нормализует, проверяет и безопасно подготавливает источники к публикации в raytsystem.",
  "raytsystem-query": "Отвечает по активному поколению raytsystem, используя локальный поиск и проверенные фрагменты источников.",
  "raytsystem-lint": "Детерминированно проверяет целостность, происхождение данных, проекции, ссылки и секреты.",
  "raytsystem-save": "Сохраняет синтез с цитатами как типизированный черновик без канонической публикации.",
  "raytsystem-research": "Проводит ограниченное исследование и возвращает предложения доказательств без канонической записи.",
  "raytsystem-run-review": "Независимо проверяет запуск, diff, контракт или контрольную точку, не изменяя состояние.",
  "raytsystem-security-review": "Проверяет границы политики, происхождение данных, утечки, изоляцию и восстановление.",
  "raytsystem-watch": "Безопасно просматривает видео и транскрипты как инертные доказательства, не выполняя импортированные инструкции."
};

const roleCopy: Record<string, string> = {
  builder: "реализация",
  librarian: "курация знаний",
  orchestrator: "оркестрация",
  researcher: "исследование",
  reviewer: "независимая проверка"
};

const isolationCopy: Record<string, string> = {
  none: "без изоляции",
  external_cli: "внешний CLI",
  workspace_sandbox: "песочница рабочего пространства",
  external_runtime: "внешняя среда выполнения",
  container_or_remote_sandbox: "контейнер или удалённая песочница"
};

const errorCopy: Record<string, string> = {
  request_failed: "Локальная система не ответила на запрос.",
  not_found: "Объект не найден.",
  task_not_found: "Задача не найдена.",
  skill_not_found: "Навык не найден.",
  context_not_found: "Документ контекста не найден.",
  knowledge_not_found: "Объект знаний не найден.",
  snapshot_stale: "Выбранный срез уже изменился. Обновите страницу и повторите действие.",
  session_required: "Заново откройте локальный интерфейс.",
  content_restricted: "Содержимое skill скрыто sensitivity policy.",
  skill_read_only: "Этот skill доступен только для чтения. Создайте локальную копию, если policy это разрешает.",
  skill_validation_failed: "Проверьте обязательные поля frontmatter и исправьте отмеченные ошибки.",
  skill_edit_conflict: "Skill изменился после открытия редактора. Ваши изменения не записаны.",
  skill_idempotency_conflict: "Этот ключ операции уже связан с другим изменением.",
  unsafe_skill_path: "Источник skill не входит в разрешённый локальный путь.",
  skill_persistence_failed: "Не удалось атомарно записать skill; исходная версия сохранена.",
  body_too_large: "Содержимое превышает безопасный размер запроса.",
  csrf_rejected: "Локальная сессия изменилась. Обновите страницу перед записью.",
  idempotency_required: "Для записи нужен корректный ключ идемпотентности."
};

export function statusLabel(status: string): string {
  return statusCopy[status.toLowerCase()] ?? humanize(status);
}

export function kindLabel(kind: string): string {
  return kindCopy[kind.toLowerCase()] ?? humanize(kind);
}

export function fieldLabel(field: string): string {
  return fieldCopy[field.toLowerCase()] ?? humanize(field);
}

export function priorityLabel(priority: string): string {
  return priorityCopy[priority as TaskPriority] ?? humanize(priority);
}

export function taskStatusLabel(status: TaskStatus): string {
  return statusCopy[status] ?? humanize(status);
}

export function lensLabel(lens: GraphLens): string {
  return lensCopy[lens];
}

export function relationLabel(relation: string): string {
  return relationCopy[relation.toLowerCase()] ?? humanize(relation);
}

export function operationLabel(operation: string): string {
  return operationCopy[operation.toLowerCase()] ?? humanize(operation);
}

export function capabilityLabel(capability: string): string {
  return capabilityCopy[capability.toLowerCase()] ?? humanize(capability);
}

export function localizedCatalogLabel(id: string, fallback: string): string {
  return localizedCatalogLabelCopy[id] ?? fallback;
}

export function canonicalAgentName(agent: { agent_id?: string; name: string }): string {
  return agent.name;
}

export function canonicalSkillName(skill: { skill_id: string }): string {
  return skill.skill_id;
}

export function catalogDescription(id: string, fallback: string): string {
  return catalogDescriptionCopy[id] ?? fallback;
}

export function roleLabel(role: string): string {
  return roleCopy[role.toLowerCase()] ?? humanize(role);
}

export function isolationLabel(mode: string): string {
  return isolationCopy[mode.toLowerCase()] ?? humanize(mode);
}

export function displayValue(field: string, value: string): string {
  const normalizedField = field.toLowerCase();
  const normalizedValue = value.toLowerCase();
  if (["status", "state", "adapter_state", "test_status", "sensitivity", "trust", "trust_class"].includes(normalizedField)) {
    return statusLabel(value);
  }
  if (normalizedField === "priority") return priorityLabel(value);
  if (normalizedField === "role") return roleLabel(value);
  if (["kind", "source_type", "entity_type", "locator_kind"].includes(normalizedField)) return kindLabel(value);
  if (normalizedValue === "true") return "Да";
  if (normalizedValue === "false") return "Нет";
  if (["none", "null", "not declared"].includes(normalizedValue)) return "Нет";
  if (normalizedValue === "read_only") return "Только чтение";
  if (normalizedValue === "workspace_write") return "Запись в рабочем пространстве";
  if (normalizedField === "isolation_mode") return isolationLabel(value);
  if (normalizedValue === "unavailable") return "Недоступно";
  return value;
}

export function localizeError(code: string, fallback: string): string {
  return errorCopy[code] ?? fallback;
}

export function pluralRu(count: number, one: string, few: string, many: string): string {
  const category = new Intl.PluralRules("ru-RU").select(count);
  return category === "one" ? one : category === "few" ? few : many;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}
