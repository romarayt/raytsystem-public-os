---
title: "Backup или restore не проходит проверку"
description: "Восстановление отклонено из-за traversal-членов, непустого destination или несоответствия хэшей."
audience: [operator]
status: stable
feature_flags: [backup_enabled]
related_commands:
  - "uv run raytsystem backup"
  - "uv run raytsystem restore"
  - "uv run raytsystem export"
related_pages:
  - /security/secrets-backup
  - /security/defaults
  - /troubleshooting/overview
source_of_truth:
  - path: src/raytsystem/backup/service.py
  - path: config/platform.yaml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Backup или restore не проходит проверку

## Что это

Резервное копирование и восстановление (`backup_enabled: true`,
`config/platform.yaml`) построены так, чтобы **отказывать при любом нарушении
целостности или раскрытия** (`src/raytsystem/backup/service.py`). Отклонённый restore —
это, как правило, сработавшая защита, а не сбой: bundle повреждён, назначение не
пустое или содержимое не совпадает с манифестом.

## Когда использовать

Когда `restore` завершается ошибкой `BackupError`, а восстановление не происходит.

## Пошагово: симптом → причина → диагностика → решение

### 1. Члены с traversal или небезопасными путями

`_validate_member` отклоняет любой член с абсолютным путём, `..`, обратным слэшем
или NUL-байтом («Backup contains a path traversal member»). Так же отклоняется
destination-симлинк («Restore destination cannot be a symlink»).

- Решение: используйте только bundle, созданный штатным `raytsystem backup`; не
  редактируйте архив вручную.

### 2. Назначение не пустое

Restore требует **пустой** каталог назначения: при наличии конфликтов —
«Restore requires an empty destination».

- Решение: восстанавливайте в новый пустой каталог.

### 3. Несоответствие хэшей bundle или манифеста

`verify` перепроверяет всё: хэш и идентичность манифеста, хэш отчёта редакции,
соответствие списка членов манифесту и SHA-256 каждого члена
(«Backup manifest hash is invalid», «Backup members do not match the manifest»,
«Backup member hash failed: …»). Во время restore хэш каждого члена сверяется ещё
раз («Restore member hash changed»).

- Решение: возьмите неповреждённый, проверенный bundle; при сомнении создайте
  его заново.

### 4. Несовместимая схема

Если старший компонент версии схемы не совпадает, план восстановления помечается
`incompatible` и restore отклоняется («Backup schema is incompatible»).

## Ожидаемый результат

С проверенным bundle и чистым пустым назначением restore проходит; отчёт указывает
на необходимость пересборки проекций после восстановления.

## Ограничения и безопасность

- Restore никогда не перезаписывает существующие пути (атомарная замена пустого
  каталога).
- Экспорты с раскрытием (public/diagnostic) удаляют секреты и редактируют
  абсолютные локальные пути; «unresolved» находки блокируют экспорт.
- Restore выполняется в стейджинг и заменяется атомарно; при ошибке стейджинг
  удаляется.

## Частые ошибки

- Попытка восстановить поверх непустого каталога.
- Ручное изменение архива, из-за которого ломается хэш члена.

## Когда открыть issue

Если bundle создан штатным `backup`, назначение пустое, схема совместима, но restore
всё равно падает по хэшу — приложите текст `BackupError` (без секретов) и откройте issue.

## Связанные страницы

- [/security/secrets-backup](/security/secrets-backup)
- [/security/defaults](/security/defaults)
- [/troubleshooting/overview](/troubleshooting/overview)

## Источники истины

- `src/raytsystem/backup/service.py`
- `config/platform.yaml`
