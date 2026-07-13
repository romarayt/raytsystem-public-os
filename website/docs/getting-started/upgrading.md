---
title: "Обновление raytsystem"
description: "Как безопасно обновить рабочее пространство: журналируемые миграции upgrade/migrate, резервная копия и проверка через doctor и platform-status."
audience: [operator]
status: stable
feature_flags: [runtime_execution_enabled, codex_local_enabled, claude_local_enabled, scheduled_heartbeats_enabled]
related_commands: ["uv run raytsystem migrate-brand", "uv run raytsystem upgrade", "uv run raytsystem migrate", "uv run raytsystem platform-status", "uv run raytsystem doctor"]
related_pages:
  - /getting-started/installation
  - /security/secrets-backup
  - /troubleshooting/backup-restore-fails
  - /reference/version
source_of_truth:
  - path: src/raytsystem/platform_cli.py
  - path: src/raytsystem/migrations.py
  - path: src/raytsystem/brand_migration.py
  - path: ops/decisions/ADR-032-workspace-templates-and-migrations.md
  - path: docs/STATUS.md
last_verified_against: "2026-07-13 / schema v1.4.0"
---

# Обновление raytsystem

## Переход со старого пространства имён

Для рабочей области, созданной до переименования, сначала выполните отдельную безопасную
миграцию:

```bash
uv run raytsystem migrate-brand --root . --confirm --json
```

До перемещения файлов команда создаёт полную ZIP-копию прежней конфигурации и локального
состояния в `ops/backups/`. Повторный запуск ничего не меняет. Если одновременно существуют
прежние и новые пути, команда завершится ошибкой без записи; автоматическое объединение в такой
ситуации намеренно запрещено.

Миграция закрывается безопасно до создания backup, если `config`, прежнее/новое состояние,
`ops` или `ops/backups` являются symlink, содержат special/unsafe hard-linked файлы либо меняют
inode во время проверки. Backup читает файлы через no-follow descriptors. При ошибке после
перемещения сначала возвращаются исходные namespace paths, и только затем восстанавливаются
metadata; rollback никогда не пишет через неоднозначную ссылку.

## Что это

Обновление приводит схему рабочего пространства к версии текущей сборки через
**журналируемые миграции**. Миграции спроектированы так, чтобы не «окирпичить» проект:
применение требует подтверждения и проверенной резервной копии, а конфигурация
переписывается атомарно и восстанавливается байт-в-байт при сбое журналирования (ADR-032).

Прежняя опциональная YouTube production vertical больше не входит в активный продукт: её runtime,
CLI-команды, template, catalog pack и skill недоступны. Универсальные `research`, ingestion JSON и
`raytsystem-watch` для отдельно разрешённых медиассылок сохраняются. Published registries и
существующие ledger generations не переписываются: legacy schemas/readers остаются inert
compatibility data, а не исполняемой возможностью.

## Когда использовать

После обновления исходного чекаута (`git pull` + `uv sync --dev`), когда версия схемы
рабочего пространства отстаёт от версии сборки.

## Предварительные условия

- Обновлённая установка (см. [Установку](/getting-started/installation)).
- Успешный `uv run raytsystem doctor`.

## Пошагово

### 1. Проверьте, нужна ли миграция (безопасно, без записи)

```bash
uv run raytsystem upgrade --dry-run --json
```

Команда по умолчанию работает в режиме плана и печатает `upgrade_required`, сам план,
`backup_required` и способ отката (`rollback`). Ничего не изменяется
(`src/raytsystem/platform_cli.py`).

### 2. Примените обновление с подтверждением

Применение возможно только явно и создаёт резервную копию перед миграцией:

```bash
uv run raytsystem upgrade --apply --confirm --json
```

Команда автоматически кладёт резервную копию в `ops/backups/pre-upgrade-<timestamp>.zip`,
затем применяет миграцию и возвращает `backup_id`, запись миграции и путь для отката.
Без `--confirm` применение отклоняется.

### 3. (Альтернатива) Прямой вызов миграции

`uv run raytsystem migrate` даёт тот же журналируемый механизм с ручным управлением
резервной копией: по умолчанию это план (`--dry-run`), а применение требует
`--apply --confirm` и `--backup-id`, ссылающийся на уже созданную проверенную копию.

### 4. Проверьте результат

```bash
uv run raytsystem doctor
uv run raytsystem platform-status --json
```

## Ожидаемый результат

- `upgrade --dry-run` показывает, требуется ли обновление, и план.
- `upgrade --apply --confirm` возвращает `backup_id`, запись применённой миграции и путь
  отката; повторное применение уже применённой миграции идемпотентно возвращает ту же
  запись (ADR-032).
- `doctor` снова `healthy`, `platform-status` не в состоянии `error`/`degraded`.

## Ограничения и безопасность

- Обновление **не запускает процессы и не обращается к провайдеру**: рантайм-исполнение и
  провайдерские адаптеры выключены по умолчанию — `runtime_execution_enabled`,
  `codex_local_enabled`, `claude_local_enabled` и `scheduled_heartbeats_enabled` равны
  `false` (`docs/STATUS.md`). Обновление их не включает.
- Применение fail-closed: отсутствующая версия схемы, устаревший план, цель другой мажорной
  версии, применение без резервной копии и расхождение записи с конфигурацией — жёсткие
  ошибки (ADR-032).
- `migrate-brand` не следует symlink-компонентам ни в конфигурации/состоянии, ни в каталоге
  резервных копий; обнаруженный path race прекращает операцию без автоматического merge.
- Каждая миграция журналируется и реконструируема: что применялось, из какой версии в
  какую, против какой резервной копии и с какими хешами.
- Откат — восстановление проверенной резервной копии (`restore_verified_backup`).
- Межмажорные миграции и преобразующие содержимое шаги намеренно отложены: текущий реестр
  содержит только миграцию проставления версии (ADR-032, `src/raytsystem/migrations.py`).

## Частые ошибки

- **`Upgrade apply requires --confirm`** — при `--apply` не передан `--confirm`.
- **Применение отклонено из-за отсутствия резервной копии** — `migrate --apply` требует
  валидный `--backup-id`; используйте `upgrade`, который создаёт копию сам.
- **Проблемы с резервной копией/восстановлением** — см.
  [Резервное копирование и восстановление не работают](/troubleshooting/backup-restore-fails).

## Связанные страницы

- [Установка](/getting-started/installation)
- [Секреты и резервные копии](/security/secrets-backup)
- [Резервное копирование и восстановление не работают](/troubleshooting/backup-restore-fails)
- [Версия](/reference/version)

## Источники истины

- `src/raytsystem/platform_cli.py`
- `src/raytsystem/migrations.py`
- `ops/decisions/ADR-032-workspace-templates-and-migrations.md`
- `docs/STATUS.md`
