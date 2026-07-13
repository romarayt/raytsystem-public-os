---
title: "Что отключено по умолчанию"
description: "Полный список функций и границ raytsystem, которые отключены по умолчанию и требуют явного включения или разрешения: исполнение рантайма, внешние адаптеры, egress, promotion реального корпуса и другое."
audience: [operator, developer]
status: stable
feature_flags:
  - runtime_execution_enabled
  - codex_local_enabled
  - claude_local_enabled
  - scheduled_heartbeats_enabled
  - promptfoo_adapter_enabled
  - promptfoo_remote_generation_enabled
  - otel_export_enabled
  - acp_adapter_enabled
  - a2a_gateway_enabled
  - a2a_network_exposure_enabled
  - external_notifications_enabled
  - restricted_encryption_enabled
  - external_mcp_execution_enabled
  - external_kms_enabled
related_commands:
  - "uv run raytsystem platform-status"
  - "uv run raytsystem status"
  - "uv run raytsystem doctor"
related_pages:
  - /reference/feature-flags
  - /security/overview
  - /security/approvals
  - /agents/tool-hub
  - /security/emergency-controls
  - /troubleshooting/feature-flag-off
  - /troubleshooting/documented-but-disabled
source_of_truth:
  - path: config/raytsystem.toml
  - path: config/platform.yaml
  - path: src/raytsystem/toolhub/dispatch.py
  - path: docs/04-security-scope.md
  - path: docs/10-execution-security.md
last_verified_against: "schema v1.4.0"
---

# Что отключено по умолчанию

## Что это

raytsystem придерживается принципа fail-closed: потенциально опасные возможности выключены,
пока их явно не включат флагом и не подтвердят разрешением (approval). Ниже — полный список
того, что по умолчанию находится в состоянии «off» и как оно шлюзуется. Значения флагов —
из `config/raytsystem.toml` и
`config/platform.yaml`.

## Отключено в `config/raytsystem.toml` (секция `[features]`)

- `runtime_execution_enabled = false` — реальное исполнение рантайма выключено.
- `codex_local_enabled = false` — локальный адаптер Codex выключен.
- `claude_local_enabled = false` — локальный адаптер Claude выключен.
- `scheduled_heartbeats_enabled = false` — запланированные heartbeats выключены (обычные
  `heartbeats_enabled = true`).

Реальное исполнение требует одновременно: глобального флага рантайма, флага провайдера и
решения политики `ALLOW`; egress к внутреннему/приватному провайдеру — ещё и неистёкшего
approval. Источник: `docs/10-execution-security.md`.

## Отключено в `config/platform.yaml`

- `promptfoo_adapter_enabled = false` и `promptfoo_remote_generation_enabled = false` —
  адаптер Promptfoo и удалённая генерация выключены.
- `otel_export_enabled = false` — экспорт OTLP выключен.
- `acp_adapter_enabled = false` — адаптер ACP выключен.
- `a2a_gateway_enabled = false` и `a2a_network_exposure_enabled = false` — шлюз A2A и его
  сетевая экспозиция выключены; шлюз A2A отказывает в удалённой экспозиции даже при
  установленном флаге. Источник: `docs/04-security-scope.md`.
- `external_notifications_enabled = false` — внешние уведомления выключены (список внешних
  назначений пуст, что отклоняет любое внешнее направление).
- `restricted_encryption_enabled = false` — прикладное шифрование restricted-данных выключено:
  состояние честно сообщается как `unavailable`, потому что не установлен ни один провайдер
  ключей. Источник: `docs/04-security-scope.md`.
- `external_mcp_execution_enabled = false` — внешнее исполнение MCP выключено.
- `external_kms_enabled = false` — внешний KMS выключен.

Политики по умолчанию тоже консервативны: `network_default: none`,
`workspace_default: staging_only`, `external_actions_default: approval_required`,
`mcp_tool_default: catalog_only`, `a2a_bind: loopback`. Источник:
`config/platform.yaml`.

## Другие закрытые поверхности

- Real-corpus promotion — по умолчанию default-deny (`default_promotion_mode = "manual"`).
- Удалённая загрузка через HTTP / `yt-dlp` fail-closed недоступна в штатных Tool Hub
  CLI/MCP: даже точного approval недостаточно без внешней destination-enforcing capability.
  Готовые транскрипты обрабатываются без внешнего CLI; локальное media требует host-injected
  root-confined/network-denied executor. Полный контракт — в [Tool Hub](/agents/tool-hub).
- Извлечение архивов и webhooks недоступны (default-deny).
- ASR, веса моделей и QMD-ассеты недоступны до отдельного одобрения артефакта/лицензии.
  Локальный OCR кадров в экспериментальном Tool Hub требует не только pinned `tesseract`,
  но и host-injected root-confined/network-denied executor; штатные CLI/MCP без него fail closed.
  Это не включает ASR или visual inference.

Источники: `docs/04-security-scope.md`, `src/raytsystem/toolhub/dispatch.py`,
`src/raytsystem/toolhub/video.py`.

## Что raytsystem НИКОГДА не делает без разрешения

Ни один путь исполнения не выполняет push, publish, deploy, отправку, удаление, оплату,
доступ к внешнему корню, egress приватного корпуса или promotion реального корпуса без
отдельного, привязанного к действию approval. Источник:
`docs/10-execution-security.md`.

## Как проверить статус

```bash
uv run raytsystem platform-status
uv run raytsystem status
uv run raytsystem doctor
```

## Ограничения и безопасность

Отключённое состояние — это не баг, а граница приёмки. Не описывайте эти функции как
рабочие: пока флаг стоит в `false` (а для чувствительных операций — пока нет approval),
функция считается выключенной. Полная таблица — в
`reference/feature-flags.md`.

## Частые ошибки

- Считать, что документированная функция уже работает: сверьтесь с флагом и статусом
  (см. `troubleshooting/documented-but-disabled.md`).
- Пытаться включить возможность только флагом, забыв про approval для внешних действий.

## Связанные страницы

- `reference/feature-flags.md`
- `overview.md`
- `approvals.md`
- [/agents/tool-hub](/agents/tool-hub)
- `troubleshooting/feature-flag-off.md`

## Источники истины

- `config/raytsystem.toml`
- `config/platform.yaml`
- `src/raytsystem/toolhub/dispatch.py`
- `src/raytsystem/toolhub/video.py`
- `docs/04-security-scope.md`
- `docs/10-execution-security.md`
