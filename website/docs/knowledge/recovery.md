---
title: "Восстановление индексов и диагностика"
description: "Как пересобрать производные представления знаний (FTS5, граф, index.md, hot.md) из ledger/CURRENT и как диагностировать повреждения через lint и doctor."
audience: [operator]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem rebuild-index --json"
  - "uv run raytsystem lint --json"
  - "uv run raytsystem doctor"
related_pages:
  - /knowledge/overview
  - /knowledge/operations
  - /troubleshooting/knowledge-index-stale
  - /troubleshooting/generation-conflict
source_of_truth:
  - path: ops/decisions/ADR-001-local-first-canonical-state.md
  - path: ops/decisions/ADR-011-generation-bound-retrieval-and-draft-save.md
  - path: src/raytsystem/querying.py
  - path: skills/raytsystem-query/SKILL.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Восстановление индексов и диагностика

## Что это

Производные представления знаний — полнотекстовый индекс FTS5, граф JSON, `knowledge/index.md` и `knowledge/hot.md`, а также сгенерированные страницы — можно потерять или повредить, но нельзя потерять само знание. Всё производное пересобирается из канона одной командой (ADR-001, ADR-011). Эта страница — для оператора, которому нужно вернуть систему в рабочее состояние.

## Когда использовать

- Поиск или QUERY жалуются на устаревший/повреждённый индекс.
- Вы удалили `.raytsystem/index.sqlite`, граф JSON или сгенерированный Markdown.
- `lint` сообщает о рассинхронизации проекций.
- После восстановления рабочей области нужно убедиться, что производное соответствует `ledger/CURRENT`.

## Предварительные условия

- Каноническое состояние (`_raw/`, `ledger/`, `ledger/CURRENT`) на месте и не повреждено — именно из него пересобираются проекции.
- Команды запускаются из корня рабочей области через `uv run`.

## Пошагово

1. **Проверьте состояние.** Запустите `uv run raytsystem doctor` и `uv run raytsystem status --json`, чтобы увидеть общее здоровье и активное поколение.
2. **Найдите повреждение.** Запустите `uv run raytsystem lint --json`. LINT детерминирован и только на чтение: он проверяет целостность канона/evidence/raw, свежесть проекций, сгенерированные представления, локальные ссылки, алиасы/ID, секреты и расхождение операций (ADR-011). Он покажет, что именно рассинхронизировано.
3. **Пересоберите производное.** Запустите `uv run raytsystem rebuild-index --json`. Команда заново собирает пакет проекций (FTS5, граф, `index.md`, `hot.md`, сгенерированные страницы) из `ledger/CURRENT`. Пересборка использует временную БД в том же каталоге, проверку целостности, fsync и атомарную замену (ADR-011) — прежний индекс не остаётся в полуразрушенном виде.
4. **Перепроверьте.** Снова выполните `uv run raytsystem lint --json`, затем контрольный `uv run raytsystem query "..." --json`.

## Как система восстанавливается сама

QUERY умеет восстанавливать проекции автоматически. Если индекс устарел или пересёк границу поколения, запрос делает одну принудительную пересборку и повтор, а затем — при устойчивой гонке — отказывает «fail closed», не возвращая смешанные данные (`src/raytsystem/querying.py`, `skills/raytsystem-query/SKILL.md`). Поэтому иногда достаточно просто повторить запрос.

## Ожидаемый результат

- `rebuild-index` завершается успешно, `lint` больше не сообщает о рассинхронизации проекций.
- QUERY возвращает те же логические ответы, что и до потери индекса: удаление кэша не удаляет знание (ADR-011).

## Ограничения и безопасность

- **Derived перестраивается, canonical — нет.** `rebuild-index` восстанавливает только производные представления. Он не создаёт и не меняет знания и не является промоушеном.
- Повреждённый или подделанный кэш может ненадолго снизить доступность, но не способен ввести ложный факт: каждый ответ перепроверяется против канонических объектов (ADR-011).
- Не редактируйте вручную `_raw/`, ledger или сгенерированные `knowledge/` — это только усугубит рассинхронизацию.

## Частые ошибки

- **Правка сгенерированной страницы вместо пересборки** — изменения не сохранятся; используйте `rebuild-index`.
- **Пересборка при действительно повреждённом каноне** — если пострадал сам `ledger/` или `_raw/`, `rebuild-index` не поможет; это уже задача восстановления канонического состояния. Начните с `doctor` и `lint`.

## Когда открыть issue

Откройте issue, если `lint` продолжает сообщать о повреждении канона (а не только проекций) после чистой пересборки, либо если `rebuild-index` стабильно падает при неповреждённом `ledger/CURRENT`. Приложите вывод `doctor --json`, `status --json` и `lint --json`.

## Связанные страницы

- [Модель данных базы знаний](/knowledge/overview)
- [Операции: INGEST, QUERY, LINT, SAVE](/knowledge/operations)
- [Индекс знаний устарел](/troubleshooting/knowledge-index-stale)
- [Конфликт поколений](/troubleshooting/generation-conflict)

## Источники истины

- `ops/decisions/ADR-001-local-first-canonical-state.md`
- `ops/decisions/ADR-011-generation-bound-retrieval-and-draft-save.md`
- `src/raytsystem/querying.py`
- `skills/raytsystem-query/SKILL.md`
