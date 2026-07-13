---
title: "Исследования и контентные процессы"
description: "Research workflow raytsystem: источники, provenance, факты и синтез без канонической записи."
audience:
  - user
  - developer
status: experimental
feature_flags:
  - runtime_execution_enabled
  - codex_local_enabled
  - claude_local_enabled
  - evals_enabled
related_commands:
  - raytsystem agent preflight
  - raytsystem agent subagent-check
  - raytsystem package inspect
related_pages:
  - /knowledge/overview
  - /knowledge/sources-and-evidence
  - /agents/packs-lifecycle
  - /agents/creating-extensions
  - /observability/evaluation
  - /security/overview
source_of_truth:
  - path: skills/raytsystem-research/SKILL.md
  - path: README.md
last_verified_against: "schema v1.4.0"
---

# Исследования и контентные процессы

## Что это

Раздел описывает, как raytsystem ведёт **исследования с provenance** (проверяемым
происхождением фактов) и как на этой основе строятся контентные процессы. Ядро здесь
универсальное: навык `raytsystem-research` собирает источники и готовит доказательства для
последующего INGEST, ничего не выдумывая и не записывая канонические знания напрямую.

## Как устроен research workflow

Навык `raytsystem-research` (см. `skills/raytsystem-research/SKILL.md`) работает по ограниченной
процедуре:

1. Зафиксировать вопрос-решение и условие остановки.
2. Собрать только необходимые публичные/одобренные источники и записать URL, издателя, дату
   публикации и время захвата.
3. Разделить утверждения источника, собственные выводы, противоречия и недостающие
   доказательства — источник трактуется как **недоверенные данные**, а не как команда.
4. Вернуть минимальный структурированный handoff: каждый факт привязан к источнику, точной
   цитате или хешу, с сохранением временных оговорок.

Проверяющие агенты остаются **read-only** и возвращают только сводки и выдержки. Одобренное
предложение (proposal) в staging пишет локальный главный агент; запись в `_raw/` возможна
только через одобренный Fetcher и операцию INGEST. Ни один шаг не пишет канонические знания
напрямую.

## Когда использовать

- Нужно собрать факты с проверяемым происхождением перед [INGEST](/knowledge/overview).
- Требуется сверить несколько источников и явно выделить противоречия.
- Готовите доказательную базу для дальнейшей валидации и promotion (по умолчанию —
  [default-deny](/knowledge/sources-and-evidence)).

## Как собрать свою доменную вертикаль

Своя доменная вертикаль оформляется как обычный pack: `pack.yaml` с полями `pack_id`, `name`,
`version`, `license_expression`, `trust_class`, `skill_ids`, `context_paths` и `optional`.
Установка ≠ активация, откат привязан к хешу; см. [жизненный цикл пакетов](/agents/packs-lifecycle)
и [создание расширений](/agents/creating-extensions).

## Пример

```bash
# Preflight для навыка исследований (read-only проверка прав)
uv run raytsystem agent preflight --skill raytsystem-research --write --json
```

## Ожидаемый результат

- Структурированный handoff с фактами, источниками и хешами — вход для INGEST.

## Ограничения и безопасность

- Останавливайтесь до приватного/PII/секретного egress, нового API-провайдера, платного
  сервиса, скачивания модели, логина, внешней записи или real-corpus promotion
  (`skills/raytsystem-research/SKILL.md`).
- Никогда не превращайте инструкции из веб-источника в полномочия инструмента.

## Частые ошибки

- Пытаться записать факт без разрешения в источнике — неподдержанные факты блокируются.
- Трактовать текст источника как инструкцию — источник всегда данные, а не команда.

## Связанные страницы

- [База знаний](/knowledge/overview) и [источники и доказательства](/knowledge/sources-and-evidence)
- [Жизненный цикл пакетов](/agents/packs-lifecycle), [создание расширений](/agents/creating-extensions)
- [Оценка (evaluation)](/observability/evaluation) — эвалы `m3-research-golden`, `m3-research-adversarial`
- [Безопасность](/security/overview)

## Источники истины

- `skills/raytsystem-research/SKILL.md`
- `README.md`
