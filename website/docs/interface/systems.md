---
title: "Системы (интерфейс)"
description: "Раздел «Системы»: платформенные подсистемы — оценки, трассы, повторы, симулятор политик, аварийный контур, MCP, резервные копии — их состояние и флаги."
audience:
  - operator
status: stable
feature_flags:
  - evals_enabled
  - replay_enabled
  - policy_simulator_enabled
  - emergency_controls_enabled
  - mcp_governance_enabled
  - workflow_engine_enabled
  - notifications_enabled
  - backup_enabled
related_commands:
  - raytsystem ui
  - raytsystem platform-status
  - raytsystem policy simulate
  - raytsystem emergency status
  - raytsystem mcp list
related_pages:
  - /observability/evaluation
  - /observability/tracing
  - /observability/replay
  - /observability/policy-simulator
  - /security/emergency-controls
  - /security/protocols
  - /security/secrets-backup
  - /interface/safety
source_of_truth:
  - path: web/src/features/SystemSections.tsx
  - path: config/platform.yaml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Системы (интерфейс)

## Что это

Маршрут `/systems` — локальный «flight recorder» control plane (`web/src/features/SystemSections.tsx`). Он показывает состояние платформенных подсистем и читает данные из отдельного операционного хранилища. Вверху раздел прямо оговаривает: внешнее выполнение, отправка и сетевой A2A остаются выключенными.

## Когда использовать

- Одним взглядом оценить здоровье платформы: сколько функций включено, объём audit events и trace storage, число регрессий оценок.
- Провалиться в конкретную подсистему через вкладки (адрес меняется на `/systems?section=…`).
- Провести безопасную проверку политики (dry-run) или, при необходимости, применить аварийную блокировку.

## Обзор и вкладки

Верхняя панель показывает сводку: доля включённых функций, `audit events`, `trace storage`, число регрессий, а также состояние store, MCP, ACP, A2A и провайдера шифрования. Ниже — 10 вкладок:

- **Оценки** — детерминированные проверки, результаты и неизменяемые baseline (`evals_enabled=true`; адаптер promptfoo и удалённая генерация по умолчанию выключены).
- **Трассировка** — локальные trace/span без raw prompts и секретов; в waterfall у каждого span показан `redaction` (экспорт OTLP по умолчанию выключен, `otel_export_enabled=false`).
- **Повторы** — replay и fork по зафиксированному execution record (`replay_enabled=true`).
- **Политики** — dry-run тем же policy engine, что защищает выполнение, и аварийный контур (см. ниже).
- **Инструменты** — MCP-каталог, схемы и per-tool разрешения (`mcp_governance_enabled=true`; `external_mcp_execution_enabled=false`).
- **Протоколы** — опциональные ACP и loopback-only A2A; по `config/platform.yaml` `acp_adapter_enabled=false`, `a2a_gateway_enabled=false`, `a2a_network_exposure_enabled=false`.
- **Пакеты** — карантин, проверка, установка и отдельная активация (`pack_lifecycle_enabled=true`).
- **Процессы** — типизированные DAG без raw shell-команд (`workflow_engine_enabled=true`).
- **Уведомления** — локальный inbox и состояния подтверждений (`notifications_enabled=true`; внешние уведомления выключены).
- **Резервные копии** — проверяемые private backup и public export bundles (`backup_enabled=true`).

Пустой раздел прямо оговаривает: отсутствие записей не означает, что операция идёт в фоне.

## Симулятор политик и аварийный контур

На вкладке «Политики» доступны две панели. **Policy simulator** запускает dry-run: он не создаёт workspace, не вызывает модель, не выдаёт секреты и не меняет задачу — только решение allowed / blocked / approval required. **Аварийный контур** позволяет применить блокировку (пауза сотрудников, отмена запусков, отключение выполнения, отзыв сессий и др.); команда записывается идемпотентно, требует причину и подтверждение, попадает в тот же machine-enforced gate и в audit, а восстановление — только вручную со свежим approval.

## Ограничения и безопасность

- Большинство разделов — на чтение; данные читаются из отдельного операционного хранилища.
- Отключённые по умолчанию функции (promptfoo, OTLP-экспорт, ACP, A2A и сетевая экспозиция, внешние уведомления, внешнее MCP-выполнение) показаны как выключенные, а не как рабочие.
- Симулятор политик не имеет побочных эффектов; аварийные действия необратимы и требуют явного подтверждения.

## Связанные страницы

- [Оценки](/observability/evaluation) · [Трассировка](/observability/tracing) · [Повторы](/observability/replay) · [Симулятор политик](/observability/policy-simulator)
- [Аварийные средства управления](/security/emergency-controls) · [Протоколы](/security/protocols) · [Секреты и резервные копии](/security/secrets-backup)
- [Безопасность (интерфейс)](/interface/safety)

## Источники истины

- `web/src/features/SystemSections.tsx`
- `config/platform.yaml`
