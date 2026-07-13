---
title: "Работа через Skills без постоянной работы в терминале"
description: "Как открыть raytsystem в Codex или Claude Code, писать агенту естественные команды и контролировать approvals."
audience: [user, operator]
status: stable
feature_flags: []
related_commands:
  - uv run raytsystem start
  - uv run raytsystem agent preflight
related_pages:
  - /getting-started/installation
  - /getting-started/first-run
  - /security/approvals
  - /interface/skills
source_of_truth:
  - path: AGENTS.md
  - path: CLAUDE.md
  - path: WORK.md
  - path: skills/start/SKILL.md
  - path: skills/graph/SKILL.md
  - path: skills/raytsystem-ingest/SKILL.md
  - path: skills/raytsystem-query/SKILL.md
  - path: skills/raytsystem-lint/SKILL.md
  - path: skills/raytsystem-save/SKILL.md
  - path: skills/raytsystem-research/SKILL.md
  - path: skills/raytsystem-run-review/SKILL.md
  - path: skills/raytsystem-security-review/SKILL.md
  - path: skills/raytsystem-watch/SKILL.md
last_verified_against: "working tree 2026-07-13 / ten canonical Skills"
---

# Работа через Skills без постоянной работы в терминале

После одноразовой установки Python и `uv` большинство операций можно запускать
обычными запросами агенту. Например: «Запусти raytsystem», «Обнови граф
проекта» или «Проверь целостность базы».

Skill — это версионируемая инструкция для агента, а не исполняемый документ.
Агент читает каноническую процедуру из `skills/`, показывает план и соблюдает границы
записи, данных и внешних действий.

## Один раз: откройте папку проекта

1. Установите raytsystem по [инструкции](/getting-started/installation).
2. Откройте корень клона как project/workspace в агентном приложении.
3. Убедитесь, что `AGENTS.md`, `CLAUDE.md`, `WORK.md` и `skills/` видны в корне.
4. Попросите: «Покажи план и ничего не меняй до моего подтверждения», если
   хотите сначала увидеть точные шаги.

## Поддерживаемые host applications

### Codex desktop и Codex CLI

- Откройте папку репозитория. Codex читает `AGENTS.md` и адаптеры `.agents/skills/*/SKILL.md`.
- Адаптер направляет агента к ровно одной канонической процедуре в `skills/`.
- Команды и отчёты видны в задаче Codex; локальный UI открывается в браузере.
- Запись за пределы workspace, сеть, login, upload, push и publish вызывают отдельный approval.

### Claude Code

- Откройте Claude Code из корня проекта. Он читает `CLAUDE.md` и `.claude/skills/*/SKILL.md`.
- `CLAUDE.md` не дублирует workflow: он направляет к той же процедуре в `skills/`.
- Результат появляется в сессии Claude Code и в указанных Skill локальных draft/run paths.
- Перед записью просмотрите план и diff; внешние действия остаются default-deny.

### ChatGPT Work

Репозиторий содержит `WORK.md` как явную точку входа для ChatGPT Work. Используйте этот
вариант только если ваша поверхность Work даёт агенту доступ к файлам проекта и локальным
инструментам. Наличие `WORK.md` не означает, что любой режим ChatGPT может запустить локальный CLI.

Другие агентные host applications не заявлены как квалифицированные. Они могут читать файлы, но это ещё
не доказывает соблюдение approvals, write scopes и recovery contracts.

## Естественные команды

| Что я хочу | Что написать агенту | Skill | Результат |
|---|---|---|---|
| Запустить систему | «Запусти raytsystem» | `start` | Проверка, preview установки и локальный интерфейс |
| Обновить граф | «Обнови граф проекта» | `graph` | Актуальный derived code graph |
| Добавить источник | «Импортируй этот файл…» | `raytsystem-ingest` | Проверенный ingest proposal; real promotion требует approval |
| Задать вопрос базе | «Найди в базе…» | `raytsystem-query` | Ответ с provenance или явный gap |
| Проверить базу | «Проверь целостность базы» | `raytsystem-lint` | Детерминированный lint report |
| Сохранить вывод | «Сохрани этот вывод…» | `raytsystem-save` | Validated DRAFT proposal, не каноническая запись |
| Провести исследование | «Исследуй тему…» | `raytsystem-research` | Draft research report с источниками |
| Проверить run | «Проверь этот запуск» | `raytsystem-run-review` | Независимый review report |
| Проверить безопасность | «Проведи security review» | `raytsystem-security-review` | Threat-model findings без изменений |
| Разобрать видео | «Посмотри это видео…» | `raytsystem-watch` | Transcript/visual summary с provenance и policy limits |

## Как выглядит approval

Перед записью или внешним действием агент должен назвать:

- что именно изменится;
- какие файлы, данные или destination затронуты;
- какой fingerprint/hash привязывает план к проверенному состоянию, если это предусмотрено Skill;
- как откатить или повторить операцию.

Напишите «да» только после просмотра плана и diff. Push, upload, publish, release, внешняя
отправка, удаление, private-corpus egress и real-corpus promotion требуют отдельного явного
подтверждения. Разрешение на одно действие не расширяется на другие.

## Где появляется результат

- `start` открывает loopback UI на `127.0.0.1:8765`.
- `graph` пишет только в disposable `.raytsystem/graph/`.
- QUERY и LINT возвращают отчёт в сесию агента; rebuild может обновить только derived index.
- INGEST пишет через validated pipeline; SAVE оставляет draft в `ops/staging/`, `ops/runs/` и
  `artifacts/drafts/`.
- RESEARCH, REVIEW, SECURITY REVIEW и WATCH возвращают proposal/review с явной provenance и границами.

## Когда терминал всё же нужен

- для первой установки Python, `uv` и зависимостей;
- для запуска long-running локального сервера, если host не удерживает процесс;
- для инспекции полного JSON/log output при диагностике;
- для frontend/docs development, где требуется Node.js 22.

## Неизменяемые границы

- Импортированный текст, transcript, OCR и metadata — это данные, а не команды агенту.
- Контент источника не может самовольно переключить Skill.
- `_raw/`, `normalized/`, ledger objects/generations и generated knowledge не редактируются вручную.
- Skills не отменяют security boundaries, feature flags и approvals.
- Если доказательств нет, QUERY возвращает gap, а не догадку.

См. также [approvals](/security/approvals), [безопасные defaults](/security/defaults) и
[интерфейс Skills](/interface/skills).
