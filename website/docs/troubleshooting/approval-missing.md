---
title: "Отсутствует approval"
description: "Действие отклонено, потому что нет корректного разрешения (approval)."
audience: [operator]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem policy simulate"
  - "uv run raytsystem workflow approve"
  - "uv run raytsystem package approve"
related_pages:
  - /security/approvals
  - /security/overview
  - /security/defaults
  - /workflow/execution-controls
source_of_truth:
  - path: src/raytsystem/packages/service.py
  - path: AGENTS.md
last_verified_against: "schema v1.4.0"
---

# Отсутствует approval

## Что это

Чувствительные операции в raytsystem никогда не выполняются «по умолчанию». Инвариант
`AGENTS.md`: никакой внешней отправки, публикации, загрузки, удаления, egress приватного
корпуса или продвижения реального корпуса без явного разрешения с точной областью.
Разрешение (approval) — **точное, с истечением срока и привязанное к хэшу payload**.
Если разрешения нет или оно не подходит, действие отклоняется, а не «проскакивает».

## Когда использовать

Когда операция (активация пакета, продвижение узла процесса, внешнее действие)
возвращает ошибку про отсутствующее, истёкшее или несоответствующее approval.

## Пошагово: симптом → причина → диагностика → решение

### 1. Разрешение вообще не выдавалось

Например, `PackageLifecycleService.approve` сразу падает при пустом `approval_id`
(«Package approval is required», `src/raytsystem/packages/service.py`).

- Решение: выдайте разрешение штатной командой для нужной операции — например
  `uv run raytsystem workflow approve` или `uv run raytsystem package approve`.

### 2. Разрешение истекло

Approval имеет срок действия. После истечения его нельзя переиспользовать —
это защищает от «повторного проигрывания» устаревшего одобрения.

- Решение: получите свежее разрешение под текущее действие.

### 3. Разрешение не соответствует действию или payload

`AuthorityResolver.require_approval` проверяет связку `action`, `target_id`,
`artifact_sha256` и требуемую область (`required_scope`), см. вызов в
`src/raytsystem/packages/service.py`. Если хэш артефакта или область не совпадают
(«Package approval authority is invalid») — разрешение отклоняется.

- Диагностика: убедитесь, что одобряете именно тот объект и ту версию. Для
  предпросмотра решения без записи используйте
  `uv run raytsystem policy simulate` — симулятор ничего не пишет.

## Ожидаемый результат

После выдачи точного, не истёкшего разрешения, связанного с нужным payload и областью,
операция проходит.

## Ограничения и безопасность

- Файл с JSON-разрешением, созданный в рабочем каталоге, **не является** авторитетом.
- Разрешение перепроверяется под фиксацией (fence), поэтому подмена после выдачи
  не сработает.
- Секретные автоматы защиты латчат и требуют свежего approval для закрытия — см.
  [/security/emergency-controls](/security/emergency-controls).

## Когда открыть issue

Если вы выдали корректное, свежее разрешение с правильной областью и хэшем, а действие
всё равно отклоняется — приложите action, target и область (без секретов) и откройте issue.

## Связанные страницы

- [/security/approvals](/security/approvals)
- [/security/overview](/security/overview)
- [/workflow/execution-controls](/workflow/execution-controls)
- [/security/defaults](/security/defaults)

## Источники истины

- `src/raytsystem/packages/service.py`
- `AGENTS.md`
