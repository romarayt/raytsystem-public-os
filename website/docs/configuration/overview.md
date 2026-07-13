---
title: "Конфигурация"
description: "Обзор всех конфигурационных файлов raytsystem: raytsystem.toml, platform.yaml, policies.yaml, runtime-adapters.yaml, sources.yaml, а также флаги функций и их зависимости."
audience: [operator, developer]
status: stable
feature_flags:
  - code_graph_enabled
  - graph_first_query_enabled
  - digital_employees_enabled
  - task_workspaces_enabled
  - runtime_execution_enabled
  - heartbeats_enabled
  - scheduled_heartbeats_enabled
  - evals_enabled
  - telemetry_enabled
  - replay_enabled
  - policy_simulator_enabled
  - emergency_controls_enabled
  - mcp_governance_enabled
  - pack_lifecycle_enabled
  - workflow_engine_enabled
  - notifications_enabled
  - backup_enabled
related_commands:
  - "uv run raytsystem doctor"
  - "uv run raytsystem platform-status --json"
related_pages:
  - /reference/feature-flags
  - /security/defaults
  - /security/overview
  - /troubleshooting/feature-flag-off
  - /troubleshooting/documented-but-disabled
  - /reference/cli
  - /documents/editable-and-protected
source_of_truth:
  - path: config/raytsystem.toml
  - path: config/platform.yaml
  - path: config/policies.yaml
  - path: config/runtime-adapters.yaml
  - path: config/sources.yaml
  - path: src/raytsystem/features.py
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Конфигурация

## Что это

raytsystem настраивается пятью файлами в каталоге `config/`. Все они хранятся в репозитории, читаются при старте и служат единым источником правды о том, какие возможности включены и по каким правилам работает система.

- `config/raytsystem.toml` — базовые настройки ядра: версия схемы, пути данных, лимиты, а также флаги функций ядра в секции `[features]`.
- `config/platform.yaml` — платформенные флаги функций, политики по умолчанию и пороги предохранителей (circuit breakers).
- `config/policies.yaml` — правила промоушена знаний, внешних действий, egress к моделям и роли ревьюеров.
- `config/runtime-adapters.yaml` — каталог runtime-адаптеров (исполнителей). В текущем релизе все адаптеры имеют `state: disabled`.
- `config/sources.yaml` — маршруты нормализации источников и настройки сети (по умолчанию сеть выключена).

## Когда использовать

Открывайте эти файлы, когда нужно понять, почему возможность недоступна, проверить пороги безопасности или подготовить изменение платформенных настроек. Для практических вопросов «включено ли X» удобнее посмотреть готовый статус через CLI, а не читать YAML вручную.

## Предварительные условия

- Установленный проект и рабочее окружение `uv` (см. `/getting-started/installation`).
- Доступ к каталогу `config/` в репозитории.

## Пошагово

1. Проверьте базовое состояние системы:

   ```
   uv run raytsystem doctor
   ```

2. Получите платформенные флаги и хеш конфигурации в машинно-читаемом виде:

   ```
   uv run raytsystem platform-status --json
   ```

   Ответ включает поля `flags` и `config_sha256` — хеш нормализованной конфигурации из `config/platform.yaml` (см. `to_public_dict` и `load_feature_config` в `src/raytsystem/features.py`).

3. Полный список флагов и их значения по умолчанию смотрите на странице `/reference/feature-flags`.

4. Для модуля «Документы» секция `[documents]` задаёт disposable
   `index_db`, лимиты и массив `[[documents.roots]]` с `id`, относительным
   `path`, `mode` и `kind`. Такая запись не может ослабить compiled protection floor;
   см. [разрешённые и защищённые области](/documents/editable-and-protected).

## Пример: флаги и их зависимости

Флаги функций живут в двух местах. Флаги ядра (`config/raytsystem.toml`, секция `[features]`) управляют графом кода, графовым поиском, цифровыми сотрудниками, рабочими пространствами задач и heartbeat'ами. Платформенные флаги (`config/platform.yaml`, секция `features`) управляют оценками, телеметрией, replay, симулятором политик, аварийными контролями, MCP-управлением, жизненным циклом паков, движком воркфлоу, уведомлениями и резервным копированием.

Ключевая особенность платформенных флагов — **fail-closed зависимости**. В `src/raytsystem/features.py` (`_DEPENDENCIES`) заданы пары «дочерний → родительский флаг»: дочерний флаг нельзя включить, если родительский выключен, иначе загрузка падает с `FeatureConfigError`. Например:

- `promptfoo_adapter_enabled` требует `evals_enabled`;
- `promptfoo_remote_generation_enabled` требует `promptfoo_adapter_enabled`;
- `otel_export_enabled` требует `telemetry_enabled`;
- `external_mcp_execution_enabled` требует `mcp_governance_enabled`;
- `a2a_network_exposure_enabled` требует `a2a_gateway_enabled`;
- `external_notifications_enabled` требует `notifications_enabled`;
- `external_kms_enabled` требует `restricted_encryption_enabled`.

Отдельные шлюзы экспозиции/провайдеров (удалённая генерация promptfoo, внешнее исполнение MCP, сетевая экспозиция A2A, внешний KMS) остаются выключенными по умолчанию, даже если родительская функция включена.

## Ожидаемый результат

`platform-status --json` возвращает текущий набор флагов и `config_sha256`. По умолчанию (согласно `config/platform.yaml`) включены: `evals_enabled`, `telemetry_enabled`, `replay_enabled`, `policy_simulator_enabled`, `emergency_controls_enabled`, `mcp_governance_enabled`, `pack_lifecycle_enabled`, `workflow_engine_enabled`, `notifications_enabled`, `backup_enabled`.

## Ограничения и безопасность

Часть возможностей **выключена по умолчанию** и требует явного включения и одобрения. Не считайте их работающими:

- Исполнение runtime (`runtime_execution_enabled`, `codex_local_enabled`, `claude_local_enabled` в `config/raytsystem.toml`) — выключено. Все адаптеры в `config/runtime-adapters.yaml` имеют `state: disabled`.
- Плановые heartbeat'ы (`scheduled_heartbeats_enabled`) — выключены.
- Внешние шлюзы: `otel_export_enabled`, `promptfoo_adapter_enabled`, `acp_adapter_enabled`, `a2a_gateway_enabled`, `external_notifications_enabled`, `restricted_encryption_enabled` и связанные шлюзы экспозиции — выключены.

Политики по умолчанию из `config/platform.yaml` (секция `policy`) держат систему в безопасном состоянии: `network_default: none`, `workspace_default: staging_only`, `external_actions_default: approval_required`, `mcp_tool_default: catalog_only`, `a2a_bind: loopback`, пустой `notification_destinations` (любой внешний адресат отклоняется). Сеть в `config/sources.yaml` тоже отключена (`network.enabled: false`) и разрешает только `https`/порт `443` с блокировкой приватных адресов.

`config/policies.yaml` фиксирует: реальный промоушен знаний — `manual_hash_bound` (то есть по умолчанию запрещён без одобрения с привязкой к хешу), а действия `send`, `publish`, `upload`, `delete`, `pay`, `git_push`, `pull_request` всегда требуют одобрения.

Пороговые значения предохранителей (`circuit_breakers` в `config/platform.yaml`) задают лимиты, при превышении которых защёлкивается кэтч-брейкер (например, `repeated_error: 5`, `protected_path: 1`, `forbidden_egress: 1`).

## Частые ошибки

- **Правка YAML без учёта зависимостей.** Если включить дочерний флаг при выключенном родителе, загрузка конфигурации завершится ошибкой `FeatureConfigError`. Сначала включите родительский флаг.
- **Ожидание работы выключенной функции.** Если возможность описана в документации, но отключена, см. `/troubleshooting/documented-but-disabled` и `/troubleshooting/feature-flag-off`.
- **Ручная сверка вместо CLI.** Быстрее и надёжнее выполнить `uv run raytsystem platform-status --json`, чем сверять флаги глазами.

## Связанные страницы

- [Справочник флагов функций](/reference/feature-flags)
- [Безопасные значения по умолчанию](/security/defaults)
- [Обзор безопасности](/security/overview)
- [Функция задокументирована, но отключена](/troubleshooting/documented-but-disabled)
- [Флаг функции выключен](/troubleshooting/feature-flag-off)
- [Справочник CLI](/reference/cli)

## Источники истины

- `config/raytsystem.toml`
- `config/platform.yaml`
- `config/policies.yaml`
- `config/runtime-adapters.yaml`
- `config/sources.yaml`
- `src/raytsystem/features.py`
