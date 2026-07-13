---
title: "Pack не активируется"
description: "Активация или установка пакета отклонена: не пройдены validate/approve, карантин или недостаточное доверие."
audience: [operator]
status: experimental
feature_flags: [pack_lifecycle_enabled]
related_commands:
  - "uv run raytsystem package inspect"
  - "uv run raytsystem package validate"
  - "uv run raytsystem package approve"
related_pages:
  - /agents/packs-lifecycle
  - /agents/overview
  - /security/approvals
source_of_truth:
  - path: src/raytsystem/packages/service.py
  - path: config/platform.yaml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Pack не активируется

## Что это

Жизненный цикл пакета — это строгая последовательность состояний
(`src/raytsystem/packages/service.py`): `discovered` → `quarantined` (после `inspect`) →
`validated`/`blocked` (после `validate`) → `approved` (после `approve`) →
`installed` (после `install`) → `active` (после `activate`). Каждый переход
проверяет целостность, карантин и разрешения. `package activate` или `install`
отклоняется, если предыдущий шаг не пройден.

## Когда использовать

Когда `package install` или `package activate` возвращает ошибку, а пакет не
переходит в `active`.

## Предварительные условия

`pack_lifecycle_enabled: true` (`config/platform.yaml`). В этой поставке паки —
это индексы каталога с фиксированным корнем, а не marketplace установки.

## Пошагово: симптом → причина → диагностика → решение

### 1. Осмотрите пакет (только чтение)

```bash
uv run raytsystem package inspect <источник>
```

`inspect` заносит ревизию в карантин (`quarantined`) и вычисляет `content_sha256`.
Пакеты с симлинками, секретами или превышением лимитов файлов/размера падают сразу.

### 2. Прогоните валидацию

```bash
uv run raytsystem package validate <revision_id>
```

Валидация переводит ревизию в `blocked`, если находит проблемы. Возможные причины
отказа (`src/raytsystem/packages/service.py`): `invalid_package_signature`,
`dependency_not_pinned` (зависимость не закреплена точной версией),
`dependency_unresolved` (нет активной ревизии зависимости),
`unsafe_reference_path` и `self_modifying_skill` (навык с правом `self_modify`).
Только `validated` ревизию можно одобрять.

### 3. Одобрение и доверие

`approve` требует непустой `approval_id` и покрытия обязательных eval-наборов
пройденными прогонами. Так как целостностный self-hash **не является** подписью
(`signature_verified` всегда `false`), для неподписанного пака требуется область
разрешения `unsigned_pack` в дополнение к `package_activation`. Недостаточная
область — «Package approval authority is invalid». См.
[/security/approvals](/security/approvals).

### 4. Установка и активация

`install` требует состояния `approved` и заново сверяет `content_sha256` («Package
changed after approval», если содержимое изменилось). `activate` требует состояния
`installed`, совпадения `approval_id` и повторной проверки хэша установленного
содержимого.

## Ожидаемый результат

При пройденных validate/approve, совпадающих хэшах и корректном разрешении пакет
переходит в `active`.

## Ограничения и безопасность

- Карантин обязателен: неизвестный пак не может сам объявить себя доверенным.
- Секреты, YAML-якоря/merge-ключи, дубли ключей и слишком сложные деревья
  fail-closed.
- Хэш содержимого перепроверяется на каждом шаге — подмена после одобрения не пройдёт.

## Когда открыть issue

Если пакет прошёл validate без находок, одобрен корректной областью и хэши совпадают,
но `activate` всё равно отклоняется — приложите `revision_id` и текст ошибки (без
секретов) и откройте issue.

## Связанные страницы

- [/agents/packs-lifecycle](/agents/packs-lifecycle)
- [/agents/overview](/agents/overview)
- [/security/approvals](/security/approvals)

## Источники истины

- `src/raytsystem/packages/service.py`
- `config/platform.yaml`
