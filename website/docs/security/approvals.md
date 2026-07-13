---
title: "Approvals (разрешения)"
description: "Approval в raytsystem — это точная, истекающая запись, привязанная к типу действия, хэшу полезной нагрузки и конкретным целям. Что требует разрешения и почему старое approval нельзя переиспользовать."
audience: [operator]
status: stable
feature_flags: [runtime_execution_enabled, external_mcp_execution_enabled, external_notifications_enabled]
related_commands:
  - "uv run raytsystem proposal import"
  - "uv run raytsystem mcp approve"
  - "uv run raytsystem package approve"
  - "uv run raytsystem workflow approve"
related_pages:
  - /security/overview
  - /security/emergency-controls
  - /security/defaults
  - /observability/policy-simulator
  - /reference/feature-flags
source_of_truth:
  - path: src/raytsystem/authority.py
  - path: docs/10-execution-security.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Approvals (разрешения)

## Что это

Approval — это точная, истекающая запись, которая разрешает ровно одно действие. Она
привязана к конкретным полям и проверяется fail-closed резолвером `AuthorityResolver`,
который берёт записи только из доверенных локальных хранилищ. Источник:
`src/raytsystem/authority.py`.

## Когда использовать

Approval нужен всякий раз, когда действие выходит за границу «безопасного по умолчанию»:
включение адаптера, любой сетевой egress, promotion в канонический корпус, публикация,
push, удаление, оплата или egress приватного (private-corpus) содержимого. Реальное
исполнение рантайма с egress к внутреннему/приватному провайдеру дополнительно требует
неистёкшего approval. Источник:
`docs/10-execution-security.md`.

## К чему привязано approval

Резолвер считает approval валидным, только если совпадает всё сразу:

- тип действия (`action`);
- хэш полезной нагрузки (`payload_sha256` / `artifact_sha256`) — то есть привязка к
  конкретному содержимому;
- назначение (`destination`), например конкретный провайдер или получатель;
- цель — один из идентификаторов employee, task, run или workspace;
- требуемый scope (`required_scope` должен быть подмножеством scope разрешения);
- срок действия: `approved_at <= now < expires_at`;
- при необходимости — версия/хэш политики (`policy_sha256`).

Дополнительно резолвер пересобирает `approval_id` из полей записи и сверяет его — это
защищает от подделки. Источник:
`src/raytsystem/authority.py`.

## Почему старое approval нельзя переиспользовать

Approval привязано к хэшу полезной нагрузки. Если payload изменился, хэш не совпадёт, и
резолвер выбросит ошибку `Approval does not match the exact action scope`. Точно так же
отклоняется approval с истёкшим сроком, с другим destination, с целью вне привязки или с
недостаточным scope. Это исключает повторное использование «почти подходящего» разрешения.

## Пример

Проверка разрешений и решений политики выполняется автоматически в тех операциях, которые
их требуют. Для отдельных потоков есть явные команды подтверждения, например:

```bash
uv run raytsystem mcp approve
uv run raytsystem package approve
uv run raytsystem workflow approve
```

Оценить, какое решение вынесет политика для гипотетического действия, можно в симуляторе
политики (`uv run raytsystem policy simulate`).

## Ожидаемый результат

При совпадении всех полей действие разрешается ровно один раз в рамках привязки. При любом
расхождении оно закрывается наглухо (fail closed).

## Ограничения и безопасность

- Экстренное действие `revoke_pending_approvals` инвалидирует все approvals, выданные до
  времени его активации: резолвер сверяет `approved_at` с временем отзыва. Источник:
  `src/raytsystem/authority.py`.
- Защитные circuit breakers защёлкиваются (latch) в открытом состоянии и закрываются только
  свежим точечным approval — см. `emergency-controls.md`.
- Approval не заменяет решение политики: для реального исполнения нужны и `ALLOW`, и
  подходящее approval одновременно.

## Частые ошибки

- Пытаться применить approval после правки payload — хэш не совпадёт.
- Ожидать, что approval «широкого» scope закроет действие вне его привязки к цели/destination.

## Связанные страницы

- `overview.md`
- `emergency-controls.md`
- `observability/policy-simulator.md`
- `defaults.md`

## Источники истины

- `src/raytsystem/authority.py`
- `docs/10-execution-security.md`
