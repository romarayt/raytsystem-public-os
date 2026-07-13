---
title: "Жизненный цикл packs"
description: "Как raytsystem принимает pack: discover, inspect, validate, approve, install, activate, update и rollback. Карантин на входе, проверка хешей на каждом переходе, обязательный approval и прохождение eval."
audience: [operator, developer]
status: experimental
feature_flags: [pack_lifecycle_enabled, evals_enabled]
related_commands:
  - "uv run raytsystem package discover --json"
  - "uv run raytsystem package inspect ..."
  - "uv run raytsystem package validate ..."
  - "uv run raytsystem package approve ..."
  - "uv run raytsystem package install ..."
  - "uv run raytsystem package activate ..."
  - "uv run raytsystem package rollback ..."
related_pages:
  - /agents/overview
  - /agents/creating-extensions
  - /security/approvals
  - /observability/evaluation
  - /reference/feature-flags
source_of_truth:
  - path: src/raytsystem/packages/service.py
  - path: ops/decisions/ADR-028-pack-lifecycle.md
  - path: config/raytsystem.toml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Жизненный цикл packs

## Что это

Жизненный цикл packs — управляемый конвейер приёма нового pack (набора агентов, навыков, воркфлоу и фикстур). Он реализован в `PackageLifecycleService` (`src/raytsystem/packages/service.py`) и описан в ADR-028. Каждая ревизия проходит через хранимые состояния, а на каждом переходе пересчитывается хеш содержимого — «доверять директории на слово» система не умеет.

Функция включается флагом `pack_lifecycle_enabled` (в `config/raytsystem.toml` — `true`). Статус раздела — **экспериментальный**: если флаг выключить, **любая** операция «падает закрыто» ошибкой `PackageLifecycleError`.

> Речь идёт о приёме *новых* packs через CLI `package`. Два встроенных pack (core, starter) поставляются в составе каталога — см. [/agents/overview](/agents/overview).

## Когда использовать

Когда нужно принять сторонний или собственный pack в рабочее пространство, отследить его состояние или откатиться на предыдущую проверенную ревизию.

## Состояния ревизии

`PackageLifecycleService` ведёт ревизии через хранимый жизненный цикл (`src/raytsystem/packages/service.py`, ADR-028):

**discovered → quarantined → validated → approved → installed → active**, плюс **superseded**, **rolled_back** и **blocked**.

Записи и hash-цепочка событий хранятся в изолированном платформенном хранилище `ops/platform.sqlite`.

## Пошагово

1. **discover** — `uv run raytsystem package discover --json`. Регистрирует источник (workspace-относительный, без симлинков) и возвращает `discovery_id`.
2. **inspect** — `uv run raytsystem package inspect ...`. Считывает `package.yaml`, сканирует дерево файлов (лимиты: до 2000 файлов, 4 МБ на файл, 64 МБ всего; симлинки запрещены; секрет-скан каждого файла) и **помещает ревизию в карантин** (`quarantined`). Идентичность содержимого — хеш списка пофайловых хешей.
3. **validate** — `uv run raytsystem package validate ...`. Проверяет ревизию и переводит её в `validated` либо `blocked`. Ревизия блокируется, если: зависимости не закреплены точной версией/коммит-хешем или не резолвятся в активные packs, путь-ссылка выходит за рабочее пространство, либо в permissions присутствует `self_modify`. Само-хеш `signature` проверяется, но **никогда не считается подтверждением подлинности** — `signature_verified` остаётся `false`.
4. **approve** — `uv run raytsystem package approve ...`. Разрешено только для `validated`. Требует approval (см. ниже) и прохождения eval.
5. **install** — `uv run raytsystem package install ...`. Разрешено только для `approved`. Источник пере-хешируется против утверждённого хеша, копируется в staging (`ops/staging/packages/`) и атомарно переносится в `.raytsystem/packages/<revision>`. Если pack изменился после approval — ошибка.
6. **activate** — `uv run raytsystem package activate ...`. Разрешено только для `installed`. Повторно проверяет установленные байты, ставит указатель активной ревизии и переводит прежнюю активную ревизию в `superseded`.

**update** (`uv run raytsystem package update ...`) заново принимает источник поверх активной ревизии и сразу запускает `validate`. **rollback** (`uv run raytsystem package rollback ...`) возвращает pack на ранее утверждённую и установленную ревизию, пере-проверяя её хеш; требует явную причину.

## Что требует approval

- **approve** невозможен без `approval_id`. Approval должен резолвиться **точно**: действие `activate_package`, scope `package_activation`, а пока подпись не подтверждена — дополнительно scope `unsigned_pack`. Approval привязан к хешу содержимого (`artifact_sha256`). Логика — в `AuthorityResolver.require_approval`.
- **eval-gating**: переданные `eval_run_ids` должны существовать, иметь статус `passed` и покрывать eval-сьюты манифеста плюс сьюты предыдущей активной ревизии, чтобы обновление не «уронило» покрытие. Прогоны берутся из группы `eval` — см. [/observability/evaluation](/observability/evaluation).
- **activate** проверяет, что `approval_id` совпадает с утверждённой ревизией.
- **rollback** требует непустую причину и допускает только ревизии, которые были утверждены и установлены для этого же pack.

## Ожидаемый результат

Активна ровно одна ревизия pack; прежняя помечена `superseded`. Любая правка на диске после approval делает install/activate невозможными — хеши проверяются заново на каждом шаге.

## Ограничения и безопасность

- Настоящей криптографической проверки подписи нет: неподписанные packs всегда требуют расширенный scope approval (`unsigned_pack`). Маркетплейс и удалённые реестры намеренно отложены (ADR-028).
- `pack_lifecycle_enabled=off` → все операции недоступны, ошибка «Package lifecycle is disabled».
- Симлинки в источнике запрещены; путь источника обязан оставаться в рабочем пространстве.

## Частые ошибки

- «Package validation failed: dependency_not_pinned» — зависимость указана диапазоном; закрепите точную версию или 40–64-символьный коммит-хеш.
- «Only validated packages may be approved» — сначала выполните `validate`.
- «Package changed after approval» — источник изменился; повторите цикл с `inspect`.

## Связанные страницы

- [/agents/overview](/agents/overview)
- [/security/approvals](/security/approvals)
- [/observability/evaluation](/observability/evaluation)
- [/reference/feature-flags](/reference/feature-flags)

## Источники истины

- `src/raytsystem/packages/service.py`
- `ops/decisions/ADR-028-pack-lifecycle.md`
- `config/raytsystem.toml`
