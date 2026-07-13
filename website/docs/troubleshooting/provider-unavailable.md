---
title: "Провайдер недоступен"
description: "Адаптер провайдера (codex_local/claude_local) недоступен — это ожидаемое состояние базовой поставки."
audience: [operator]
status: disabled
feature_flags: [runtime_execution_enabled, codex_local_enabled, claude_local_enabled]
related_commands:
  - "uv run raytsystem platform-status --json"
  - "uv run raytsystem status"
related_pages:
  - /security/defaults
  - /workflow/overview
  - /reference/feature-flags
source_of_truth:
  - path: config/raytsystem.toml
  - path: docs/STATUS.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Провайдер недоступен

## Что это

Плоскость исполнения (runtime) для «цифровых сотрудников» реализована, но **по
умолчанию выключена**. Флаги `runtime_execution_enabled`, `codex_local_enabled` и
`claude_local_enabled` равны `false` (`config/raytsystem.toml`). Поэтому обновление
системы не запускает ни одного процесса и не обращается ни к одному провайдеру
(`docs/STATUS.md`). Сообщение «провайдер недоступен» в базовой поставке — это
**нормальное, ожидаемое состояние**, а не ошибка.

## Когда использовать

Когда адаптер `codex_local` или `claude_local` отображается как недоступный, а
запуск не стартует.

## Пошагово: симптом → причина → диагностика → решение

### 1. Подтвердите состояние флагов (только чтение)

```bash
uv run raytsystem platform-status --json
```

Ожидаемо: `runtime_execution_enabled`, `codex_local_enabled`, `claude_local_enabled`
выключены. Определения адаптеров при этом честно отображают состояние «отключён/не
инициализирован».

### 2. Разберите вероятные причины

- **Runtime и адаптеры выключены по умолчанию** — основная причина. Это граница
  безопасности, см. [/security/defaults](/security/defaults).
- **CLI провайдера не установлен** — даже при включённых флагах адаптеру нужен
  реальный локальный CLI; в этом репозитории ни один провайдерский CLI не
  вызывался (`docs/STATUS.md`).
- **Нет разрешения на egress** — запуск провайдера дополнительно требует точного
  истекающего approval на конкретный egress-адрес. Codex/OpenAI и Claude/Anthropic —
  разные egress-назначения с раздельными решениями политики.

### 3. Решение

Если вам действительно нужно исполнение провайдера, это осознанная операция:
включите соответствующие флаги, установите нужный CLI и выдайте точное разрешение
на egress для конкретного провайдера. Браузер/UI при этом никогда не задаёт cwd,
команду, argv или окружение.

## Ожидаемый результат

В базовой поставке отсутствие провайдера — корректное состояние. Определение агента
не подразумевает запущенного агента.

## Ограничения и безопасность

- Флаги исполнения выключены и fail-closed; отдельное решение по egress требуется
  даже после включения флага.
- Разрешение на egress одного провайдера не распространяется на другого.

## Частые ошибки

- Считать, что «цифровой сотрудник» уже работает, потому что он есть в каталоге.
- Ожидать, что включение одного флага само по себе разрешит egress.

## Когда открыть issue

Если флаги включены осознанно, нужный CLI установлен, точное разрешение на egress
выдано, но адаптер всё равно недоступен — приложите вывод `platform-status --json`
(без секретов) и откройте issue.

## Связанные страницы

- [/security/defaults](/security/defaults)
- [/workflow/overview](/workflow/overview)
- [/reference/feature-flags](/reference/feature-flags)

## Источники истины

- `config/raytsystem.toml`
- `docs/STATUS.md`
