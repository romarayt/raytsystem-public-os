---
title: "Секреты, backup, export, restore"
description: "Честное состояние шифрования секретов в raytsystem и безопасные бэкапы: почему ничего не выдаётся за зашифрованное и как restore отклоняет traversal, непустую папку и несовпадение хэшей."
audience: [operator]
status: stable
feature_flags: [restricted_encryption_enabled, external_kms_enabled, backup_enabled]
related_commands:
  - "uv run raytsystem secrets-status --json"
  - "uv run raytsystem backup <destination>"
  - "uv run raytsystem export <destination> --kind <public|diagnostic|transfer|private>"
  - "uv run raytsystem restore <bundle> <destination> --dry-run"
related_pages:
  - /security/overview
  - /security/defaults
  - /security/emergency-controls
  - /reference/feature-flags
  - /reference/cli
source_of_truth:
  - path: src/raytsystem/secrets/service.py
  - path: src/raytsystem/backup/service.py
  - path: ops/decisions/ADR-030-secrets-encryption.md
  - path: ops/decisions/ADR-031-backup-export-restore.md
  - path: config/platform.yaml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Секреты, backup, export, restore

## Что это

Две подсистемы: честный отчёт о состоянии шифрования секретов и типизированные бэкапы с
проверяемой целостностью. Обе спроектированы так, чтобы ничего не выдавать за большее, чем
есть на самом деле. Источники:
`src/raytsystem/secrets/service.py`,
`src/raytsystem/backup/service.py`.

## Состояние шифрования: честный отчёт

```bash
uv run raytsystem secrets-status --json
```

Команда сообщает состояние провайдера ключей, никогда не заявляя отсутствующую возможность.
По умолчанию `restricted_encryption_enabled: false`
(`config/platform.yaml`), поэтому статус — `UNAVAILABLE` с
кодом `restricted_encryption_disabled`. Даже при включённом флаге backend AES-256-GCM
(библиотека `cryptography`) намеренно не установлен: если он не импортируется, шифрование
недоступно, и статус остаётся `UNAVAILABLE`. macOS Keychain считается `AVAILABLE` только после
доказанного round-trip с брелоком, а не из-за наличия бинарника в PATH. Внешний KMS —
fail-closed заглушка: всегда `UNAVAILABLE`, а до него нужно ещё и `external_kms_enabled`
(по умолчанию off) поверх `restricted_encryption_enabled`. Итог: ничего не выдаётся за
зашифрованное. Источник:
`ops/decisions/ADR-030-secrets-encryption.md`.

Расшифровка — событие полномочности, а не удобство: каждый decrypt требует свежего approval
`decrypt_secret` (scope `secret_decrypt`), привязанного по хэшу к blob, плюс совпадения
привязки blob к текущему провайдеру, key ID и алгоритму. Ambient-decrypt не существует.
Blob-файлы удерживаются в `ops/encrypted/` с проверкой на traversal.

## Backup, export, restore

`backup_enabled: true`. Один глагол скрывает четыре разных поверхности раскрытия, поэтому
типов бэкапа четыре (`BackupKind`) с разными наборами корней и правилами редактирования:
`private_backup` (полное локальное состояние, без редактирования), `public_release_export`
(релизная поверхность: секреты удалены, абсолютные пути отредактированы),
`diagnostic_export` (конфиги и ops-манифесты — никогда каноническое знание) и
`workspace_transfer` (приватный набор минус машинно-локальное состояние). Источник:
`ops/decisions/ADR-031-backup-export-restore.md`.

```bash
uv run raytsystem backup <destination>                      # private_backup
uv run raytsystem export <destination> --kind public        # публичный релиз
uv run raytsystem restore <bundle> <destination> --dry-run  # план восстановления
```

`backup` создаёт приватную копию, `export` по умолчанию делает `public_release_export`.
Любой редактирующий export прерывается, если после редактирования остались неразрешённые
находки раскрытия — частичное редактирование считается ошибкой, а не предупреждением.

## Restore: строгие проверки

`restore` без `--apply` печатает план; с `--apply` восстанавливает. Восстановление отклоняется,
если:

- назначение — симлинк;
- назначение непустое (есть конфликты) — restore идёт только в пустую папку;
- хэш члена бандла не совпал с манифестом (в том числе повторная сверка при стейджинге);
- схема бандла несовместима;
- член бандла содержит traversal (`..`, абсолютный путь, обратный слэш, NUL) или пытается
  перезаписать существующий путь.

Проверка предшествует доверию: ограниченные счётчики и размеры членов, точное совпадение
набора членов с манифестом, пофайловые хэши и проверка хэшей манифеста и отчёта о
редактировании. Восстановление стейджится атомарно и всегда требует пересборки проекций.
Источник:
`src/raytsystem/backup/service.py`.

## Ограничения и безопасность

- Зашифрованные blob включаются только в нераскрывающие типы, только при
  `restricted_encryption_enabled: true` и только если каждый файл валиден как `EncryptedBlob`.
- Плановые бэкапы, удалённые назначения и любая выгрузка бандлов наружу отложены: подсистема
  пишет только локальные файлы. Источник:
  `ops/decisions/ADR-031-backup-export-restore.md`.
- `diagnostic_export` физически не может содержать `_raw/` или ledger знания.

## Частые ошибки

- Считать секреты «зашифрованными» при `secrets-status` в состоянии `UNAVAILABLE` — они не
  зашифрованы.
- Пытаться восстановить в непустую папку или поверх существующего workspace — restore
  откажет.

## Связанные страницы

- `overview.md`
- `defaults.md`
- `emergency-controls.md`

## Источники истины

- `src/raytsystem/secrets/service.py`
- `src/raytsystem/backup/service.py`
- `ops/decisions/ADR-030-secrets-encryption.md`
- `ops/decisions/ADR-031-backup-export-restore.md`
- `config/platform.yaml`
