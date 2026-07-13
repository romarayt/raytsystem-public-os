---
title: "Граф кода отсутствует или устарел"
description: "Что делать, когда линза «Код» показывает «Не построено» или «Устарело», а graph query падает."
audience: [user, developer]
status: stable
feature_flags:
  - code_graph_enabled
  - graph_first_query_enabled
related_commands:
  - "uv run raytsystem graph status --json"
  - "uv run raytsystem graph update --json"
  - "uv run raytsystem graph rebuild --json"
related_pages:
  - /troubleshooting/overview
  - /code-graph/overview
  - /code-graph/freshness-update-rebuild
  - /interface/universe
source_of_truth:
  - path: src/raytsystem/codegraph/projection.py
  - path: src/raytsystem/cli.py
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Граф кода отсутствует или устарел

## Что это

Граф кода — это производный, полностью пересобираемый снимок структуры репозитория под `.raytsystem/graph/`. Он не является канонической истиной, поэтому запросы к нему намеренно «падают закрыто» (fail closed), если снимок отсутствует, устарел или не проходит проверку целостности. В интерфейсе это одна русская линза **Код** во «Вселенной» с индикаторами свежести и кнопками сборки.

## Симптом

- Линза «Код» показывает состояние «Не построено» или «Устарело».
- `uv run raytsystem graph query "..."` завершается ошибкой недоступности графа вместо ответа.

## Вероятная причина

`graph status` возвращает одно из состояний:

- **`missing` / `not_built`** — граф ещё ни разу не собирался.
- **`stale`** — снимок есть, но входные данные изменились. Причина уточняется полем `reason`: `inputs_changed` (изменились файлы), `configuration_changed` (сменился отпечаток конфигурации или экстрактора), `checkout_changed` (сменился Git-HEAD/ветка), `previous_update_abandoned` (предыдущее обновление было прервано).
- **`building`** — обновление сейчас идёт (`update_in_progress`).
- **`unchecked`** — сверка велась без хеширования содержимого (быстрый путь `--fast`).
- **`error`** — снимок не прошёл проверку целостности или конфигурация некорректна.

Запросы к графу требуют состояния, близкого к актуальному; иначе служба возвращает `CodeGraphUnavailable` вместо потенциально ложного ответа.

## Безопасная диагностика

Сначала посмотрите состояние — это операция только для чтения, она не меняет снимок:

```bash
uv run raytsystem graph status --json
```

Обратите внимание на поля `state`, `reason`, `changed_files`, `deleted_files`.

## Решение

- Если состояние `stale` из-за изменённых файлов — сделайте инкрементальное обновление изменённых и удалённых входов:

  ```bash
  uv run raytsystem graph update --json
  ```

- Если граф `missing` (не построен), либо изменились конфигурация/экстрактор, либо нужно детерминированно собрать всё заново — выполните полную атомарную пересборку:

  ```bash
  uv run raytsystem graph rebuild --json
  ```

- В интерфейсе те же действия доступны кнопками сборки и обновления в линзе «Код». Мутации графа идут через типизированный same-origin API, требуют ожидаемый снимок, сессию/Origin/CSRF/идемпотентность и подтверждают, что `ledger/CURRENT` остаётся байт-в-байт неизменным. Обновление графа никогда не трогает канонический реестр.

Подробный разбор режимов свежести — на странице [Свежесть: update и rebuild](/code-graph/freshness-update-rebuild).

## Пример

```bash
# 1. Понять состояние
uv run raytsystem graph status --json
# 2. Если stale по изменённым файлам — обновить инкрементально
uv run raytsystem graph update --json
# 3. Проверить, что запрос снова отвечает
uv run raytsystem graph query "где точка входа CLI" --depth 2 --json
```

## Ожидаемый результат

`graph status` показывает `state: current`, а `graph query`/линза «Код» снова отвечают ограниченным типизированным срезом контекста.

## Ограничения и безопасность

- Граф — производный артефакт; его можно удалить и пересобрать в любой момент.
- Запросы только для чтения (`status`, `query`, `explain`, `neighbors`, `path`, `impact`) никогда не пересобирают граф сами и не считаются каноническим источником истины.
- Сборка идёт под фенсированной блокировкой и с ограничениями по ресурсам парсера.

## Частые ошибки

- Считать ответ устаревшего графа достоверным. При `stale`/`missing` запрос честно отказывает — это не баг.
- Пытаться отредактировать файлы под `.raytsystem/graph/` вручную вместо `update`/`rebuild`.

## Когда открыть issue

Если `graph status` стабильно показывает `state: error` с `reason: integrity_failed` даже сразу после `graph rebuild`, приложите к issue вывод `graph status --json` и `graph rebuild --json`.

## Связанные страницы

- [Обзор графа кода](/code-graph/overview)
- [Свежесть: update и rebuild](/code-graph/freshness-update-rebuild)
- [Интерфейс: Вселенная](/interface/universe)

## Источники истины

- `src/raytsystem/codegraph/projection.py`
- `src/raytsystem/cli.py`
