---
title: "Старт за минуту: команды и скиллы"
description: "Самые быстрые способы запустить raytsystem — команда raytsystem start для интерфейса, скилл start для установки в проект или другую папку и скилл graph для обновления графа. С командами и тем, что каждая делает."
sidebar_position: 1
audience: [user]
status: stable
related_commands:
  - uv run raytsystem start
  - uv run raytsystem bootstrap --target . --dry-run
  - uv run raytsystem graph update
  - uv run raytsystem graph status
  - uv run raytsystem uninstall --target .
  - uv run raytsystem doctor
---

# Старт за минуту

Три вещи, которых достаточно, чтобы начать: одна команда для запуска интерфейса и два коротких
скилла — установить raytsystem и держать граф актуальным. Всё локально, ничего не отправляется наружу.

## Команда `start` — открыть интерфейс

Короткий алиас для `ui`. Поднимает панель управления только на локальном loopback
(`http://127.0.0.1:8765`):

```bash
uv run raytsystem start
```

Указать другой репозиторий можно через `--root <путь>`. Порт и хост — `--port` / `--host`
(нелокальный bind запрещён в этом релизе).

## Скилл `start` — установить и подключить

Запускается из Claude Code или Codex фразой «start», «установи raytsystem», «подключить пространство».
Работает в двух режимах:

1. **в текущий проект** — репозиторий, который открыт;
2. **в другую папку** — путь, который вы укажете (Obsidian-хранилище, папка с заметками, другой репо).

Скилл всегда сначала показывает безопасный предпросмотр (ничего не пишет), объясняет, что будет
создано, что останется без изменений, и просит подтверждение, и только потом применяет план. Файлы
пользователя не перезаписываются: `README.md` остаётся как есть, в `AGENTS.md` дописывается блок
`RAYTSYSTEM:BEGIN/END`, исходные данные индексируются на месте. Всё обратимо:

```bash
uv run raytsystem bootstrap --target <путь> --dry-run --json   # предпросмотр
uv run raytsystem bootstrap --target <путь> --apply --confirm <FINGERPRINT> --json
uv run raytsystem uninstall --target <путь> --json             # откат, данные не трогаются
```

## Скилл `graph` — обновить граф

Запускается фразой «обнови граф», «перестрой граф». Приводит код-граф в актуальное состояние, чтобы
учитывались все текущие файлы. Пишет только в пересобираемый слой `.raytsystem/graph/` и никогда не
меняет canonical knowledge.

```bash
uv run raytsystem graph status --json   # проверить свежесть
uv run raytsystem graph update --json   # инкрементально учесть изменённые и удалённые файлы
uv run raytsystem graph rebuild --json  # полная пересборка (если граф отсутствует)
```

## Что дальше

После установки проверьте `uv run raytsystem doctor` (ожидается `healthy: True`), затем `uv run raytsystem
status`, `uv run raytsystem task list` и `uv run raytsystem query`. Полный список команд — в разделе
[CLI reference](/reference/cli).
