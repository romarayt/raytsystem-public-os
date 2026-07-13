---
title: "Функция выключена флагом"
description: "Функция недоступна, потому что соответствующий feature flag выключен по умолчанию."
audience: [operator, developer]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem platform-status --json"
  - "uv run raytsystem status"
related_pages:
  - /reference/feature-flags
  - /security/defaults
  - /troubleshooting/documented-but-disabled
source_of_truth:
  - path: config/platform.yaml
  - path: config/raytsystem.toml
last_verified_against: "schema v1.4.0"
---

# Функция выключена флагом

## Что это

Многие подсистемы raytsystem закрыты за отдельными feature flags. По умолчанию всё
внешнее и потенциально опасное **выключено и fail-closed**. Если UI показывает
«отключено» или CLI отказывается выполнять действие, чаще всего причина в том, что
нужный флаг равен `false`, а не в поломке.

## Когда использовать

Когда раздел интерфейса помечен «отключено», действие возвращает «disabled», либо
функция из документации недоступна в вашей поставке.

## Предварительные условия

Доступ к рабочему каталогу проекта и возможность запускать `uv run raytsystem ...`.

## Пошагово

### 1. Посмотрите фактический статус флагов

```bash
uv run raytsystem platform-status --json
```

Команда показывает текущее состояние платформенных флагов. Значения по умолчанию
заданы в `config/platform.yaml` и `config/raytsystem.toml`.

### 2. Сверьтесь со значениями по умолчанию

Выключены по умолчанию (это ожидаемо, не баг):

- `runtime_execution_enabled`, `codex_local_enabled`, `claude_local_enabled`,
  `scheduled_heartbeats_enabled` (`config/raytsystem.toml`);
- `promptfoo_adapter_enabled`, `otel_export_enabled`, `acp_adapter_enabled`,
  `a2a_gateway_enabled`, `external_notifications_enabled`,
  `restricted_encryption_enabled` и отдельные шлюзы экспозиции
  (`promptfoo_remote_generation_enabled`, `external_mcp_execution_enabled`,
  `a2a_network_exposure_enabled`, `external_kms_enabled`) — все в `config/platform.yaml`.

Полный список — на [/reference/feature-flags](/reference/feature-flags).

### 3. Решение

Если флаг должен быть включён для вашего сценария — включите его сознательно в
конфигурации и понимая последствия. Многие возможности дополнительно требуют
одобрения (approval) на конкретное действие даже после включения флага.

## Ожидаемый результат

`platform-status --json` подтверждает состояние флага; после осознанного включения
функция становится доступной (при выполнении остальных условий и одобрений).

## Ограничения и безопасность

- Отдельные шлюзы экспозиции остаются fail-closed, даже если родительская функция
  включена — например `a2a_network_exposure_enabled` отклоняется независимо от
  `a2a_gateway_enabled`.
- Не включайте внешние флаги «на всякий случай»: значения по умолчанию — это
  граница безопасности, см. [/security/defaults](/security/defaults).

## Частые ошибки

- Ожидать, что функция «просто работает», хотя её флаг выключен.
- Путать включённый флаг с выданным разрешением — это разные проверки.

## Когда открыть issue

Если `platform-status` показывает флаг включённым, все условия выполнены, но функция
всё равно недоступна — откройте issue с выводом `platform-status --json` (без секретов).

## Связанные страницы

- [/reference/feature-flags](/reference/feature-flags)
- [/security/defaults](/security/defaults)
- [/troubleshooting/documented-but-disabled](/troubleshooting/documented-but-disabled)

## Источники истины

- `config/platform.yaml`
- `config/raytsystem.toml`
