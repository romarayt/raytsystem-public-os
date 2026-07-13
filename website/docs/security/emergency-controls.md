---
title: "Аварийные средства и circuit breakers"
description: "Аварийный стоп-механизм raytsystem: как остановить платформу одной локальной командой, что делают latching circuit breakers и почему возобновление всегда требует свежего approval."
audience: [operator]
status: stable
feature_flags: [emergency_controls_enabled, runtime_execution_enabled]
related_commands:
  - "uv run raytsystem emergency status --json"
  - "uv run raytsystem emergency activate --action <ДЕЙСТВИЕ> --reason \"...\" --idempotency-key <ключ>"
  - "uv run raytsystem emergency recover --action <ДЕЙСТВИЕ> --reason \"...\" --approval-id <id> --idempotency-key <ключ>"
  - "uv run raytsystem emergency close-breaker <breaker_id> --approval-id <id> --reason \"...\""
related_pages:
  - /security/overview
  - /security/approvals
  - /security/defaults
  - /reference/feature-flags
  - /reference/cli
source_of_truth:
  - path: src/raytsystem/emergency/service.py
  - path: ops/decisions/ADR-024-emergency-controls-and-circuit-breakers.md
  - path: config/platform.yaml
last_verified_against: "schema v1.4.0"
---

# Аварийные средства и circuit breakers

## Что это

Аварийные средства — это стоп-механизм, который остановить проще, чем возобновить.
Остановка не требует approval; возобновление требует. Управление включено по умолчанию:
`emergency_controls_enabled: true` в
`config/platform.yaml`. Всё состояние хранится одной
глобальной записью в изолированном хранилище `ops/platform.sqlite` с хэш-цепочкой событий и
идемпотентными квитанциями. Источник:
`src/raytsystem/emergency/service.py`.

## Когда использовать

Когда нужно немедленно затормозить рантайм, сеть, egress к провайдерам, выдачу задач или
сессии — по подозрению на нештатное или враждебное поведение. Проверить текущее состояние
можно в любой момент:

```bash
uv run raytsystem emergency status --json
```

Ответ показывает `state` (`ready` или `blocked`), список активных действий и состояние
breaker-ов. Если хранилище недоступно, снимок деградирует в `unavailable`, и рантайм-гейт
трактует это как блокировку (fail closed).

## Предварительные условия

- `emergency_controls_enabled: true`. При отключённом флаге любая операция подсистемы
  завершается ошибкой.
- Для возобновления — свежий точечный approval (см. `approvals.md`).

## Пошагово

1. Активируйте нужные типовые действия. Активация требует только локальной аварийной
   полномочности и причины — approval не нужен. Доступные действия (`EmergencyAction`):
   `pause_all_employees`, `cancel_active_runs`, `disable_runtime_execution`,
   `disable_network_adapters`, `disable_external_providers`, `freeze_task_checkout`,
   `revoke_runtime_sessions`, `revoke_pending_approvals`, `emergency_budget_stop`.
   Активация объединяет запрошенные действия с уже активными и сохраняет отметку времени по
   каждому.
2. Гейты на местах сверяются с состоянием: старт рантайма, сетевые адаптеры, egress к
   провайдерам, выдача задач и гранты runtime-сессий. `revoke_pending_approvals`
   инвалидирует approvals, выданные до момента активации.
3. Для возврата вызовите `recover` с точечным approval (`recover_emergency`, scope
   `emergency_recovery`), привязанным по хэшу к снимаемым действиям и причине. Можно снять
   подмножество — остальное остаётся активным.

## Circuit breakers

Breaker-ы работают по порогам из
`config/platform.yaml` (секция `circuit_breakers`).
Ключевое свойство — защёлкивание (latching): открытый breaker закрывается только новым
точечным approval.

- Открытие любого breaker автоматически активирует `disable_runtime_execution`.
- Security-триггеры (`protected_path`, `forbidden_egress`, `policy_violations`,
  `failed_approvals`) не имеют автоматического восстановления вообще. Остальные получают
  ровно одну ограниченную HALF_OPEN-попытку, после чего остаются открытыми.
- Закрыть breaker вручную (единственный путь для security-триггеров) можно только со свежим
  approval `close_circuit_breaker` (scope `emergency_recovery`):

```bash
uv run raytsystem emergency close-breaker <breaker_id> --approval-id <id> --reason "..."
```

Восстановление рантайма отклоняется, пока хоть один security-breaker открыт.

## Ожидаемый результат

Остановка — одна локальная команда. Возобновление требует записанной человеческой
полномочности. Автоматическое восстановление не может «мигать»: одна попытка на открытие, а
security-срабатывания не самовосстанавливаются.

## Ограничения и безопасность

- Реальное исполнение рантайма всё равно выключено по умолчанию
  (`runtime_execution_enabled: false`) — аварийные гейты добавляют защиту поверх этого.
- Состояние глобальное по замыслу: точечные (по сотруднику/адаптеру) состояния и
  авто-истечение по времени пока отложены. Источник:
  `ops/decisions/ADR-024-emergency-controls-and-circuit-breakers.md`.
- При нечитаемом хранилище каждый гейт закрывается наглухо.

## Частые ошибки

- Ожидать, что breaker закроется сам: security-breaker закрывается только через
  `close-breaker` со свежим approval.
- Пытаться восстановить рантайм при открытом security-breaker — операция будет отклонена.

## Связанные страницы

- `overview.md`
- `approvals.md`
- `defaults.md`

## Источники истины

- `src/raytsystem/emergency/service.py`
- `ops/decisions/ADR-024-emergency-controls-and-circuit-breakers.md`
- `config/platform.yaml`
