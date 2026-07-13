---
title: Покрытие документации
description: Матрица покрытия функций raytsystem документацией и способ автоматической проверки полноты.
slug: /coverage
sidebar_position: 98
audience:
  - developer
  - contributor
status: stable
feature_flags: []
related_commands:
  - raytsystem platform-status
related_pages:
  - /development/documentation
  - /reference/cli
  - /reference/feature-flags
  - /reference/routes
  - /reference/api
source_of_truth:
  - path: scripts/docs/coverage_check.py
  - path: scripts/docs/gen_reference.py
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Покрытие документации

Эта страница — доказательство полноты. Она показывает, какие поверхности продукта покрыты
документацией и как полнота проверяется автоматически. Матрица ниже поддерживается людьми как
обзор; фактическую полноту гарантируют скрипты, которые сверяют документацию с живыми контрактами
(а не с этой страницей).

## Как полнота проверяется автоматически

| Проверка | Скрипт | Что гарантирует |
|---|---|---|
| Reference актуален | `scripts/docs/gen_reference.py --check` | CLI, флаги, UI-маршруты, Agents/Skills HTTP API, узлы workflow и версии собраны из кода и не устарели |
| Покрытие поверхностей | `scripts/docs/coverage_check.py` | у каждого маршрута есть страница, каждая CLI-команда есть в reference, все отключённые флаги раскрыты, ни одна отключённая функция не помечена `stable` |
| Frontmatter и ссылки | `scripts/docs/frontmatter_lint.py` | обязательные поля, уникальные slug, валидные флаги/команды, разрешимые ссылки, отсутствие секретов и абсолютных путей |
| Влияние на документацию | `scripts/docs/docs_impact_check.py` | изменение публичной поверхности сопровождается изменением документации |
| Сборка сайта | `npm --prefix website run build` | битые внутренние ссылки, отсутствующие изображения, MDX и соответствие навигации |

Проверки находят: недокументированную новую функцию; удалённую функцию с оставшейся статьёй;
команду с устаревшим примером (через регенерацию reference); функцию, ошибочно помеченную как
`stable`; функцию, выключенную флагом, но описанную как доступную по умолчанию.

## Матрица покрытия

Легенда: T — Tutorial (обучение), H — How-to (инструкция), R — Reference (справочник),
Tr — Troubleshooting (решение проблем).

| Поверхность продукта | T | H | R | Tr | Статус |
|---|:--:|:--:|:--:|:--:|---|
| Установка и первый запуск | ✓ | ✓ | ✓ | ✓ | stable |
| Интерфейс (11 маршрутов) | ✓ | ✓ | ✓ | ✓ | stable |
| Граф проекта (code graph) | ✓ | ✓ | ✓ | ✓ | stable |
| Задачи и task ledger | ✓ | ✓ | ✓ | ✓ | stable |
| Workflow и execution plane | ✓ | ✓ | ✓ | ✓ | experimental |
| База знаний (INGEST/QUERY/LINT/SAVE) | ✓ | ✓ | ✓ | ✓ | stable |
| Агенты, навыки, контекст, packs | ✓ | ✓ | ✓ | ✓ | stable |
| Исследования и демо-вертикали | ✓ | ✓ | — | ✓ | experimental |
| Наблюдаемость (eval/trace/replay/policy) | ✓ | ✓ | ✓ | ✓ | stable |
| Безопасность и approvals | ✓ | ✓ | ✓ | ✓ | stable |
| Emergency controls и circuit breakers | ✓ | ✓ | ✓ | ✓ | stable |
| MCP / ACP / A2A | ✓ | ✓ | ✓ | ✓ | experimental |
| Секреты, backup, export, restore | ✓ | ✓ | ✓ | ✓ | stable |
| Конфигурация и feature flags | ✓ | ✓ | ✓ | ✓ | stable |
| CLI (77 команд) | ✓ | ✓ | ✓ | ✓ | stable |
| Разработка и участие | ✓ | ✓ | — | — | stable |

## Что документация сознательно НЕ описывает как рабочее

Ряд возможностей отключён по умолчанию и описан как таковой, а не как доступный функционал:
runtime execution (`codex_local`, `claude_local`), scheduled heartbeats, promptfoo adapter и
удалённая генерация, OTLP export, ACP adapter, A2A gateway, внешние уведомления, прикладное
шифрование (restricted encryption), внешнее выполнение MCP, внешний KMS, промоушен реального
корпуса и удалённая загрузка источников. Полный список и умолчания — на странице
[Feature flags](/reference/feature-flags) и [«Что отключено по умолчанию»](/security/defaults).

## Оставшиеся пробелы

- Английская локализация подготовлена архитектурно (структура i18n), но ещё не переведена.
- Версионирование документации намеренно не включено на первом этапе (но не заблокировано).
- Публикация на GitHub Pages использует GitHub owner `romarayt`; репозиторий
  `raytsystem-public-os` задан по умолчанию в
  `website/docusaurus.config.ts` и включения Pages владельцем репозитория.
