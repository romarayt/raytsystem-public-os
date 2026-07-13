---
title: "Функция описана, но отключена"
description: "Возможность упомянута в документации, но недоступна: она за выключенным флагом или помечена как experimental/disabled/draft."
audience: [user, operator]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem platform-status --json"
related_pages:
  - /reference/feature-flags
  - /security/defaults
  - /getting-started/capabilities-and-limits
  - /troubleshooting/feature-flag-off
source_of_truth:
  - path: config/platform.yaml
  - path: config/raytsystem.toml
  - path: docs/STATUS.md
last_verified_against: "schema v1.4.0"
---

# Функция описана, но отключена

## Что это

В документации raytsystem описаны и те возможности, которые **реализованы, но
выключены по умолчанию**. Это сделано намеренно и честно: наличие описания не
означает, что функция активна в вашей поставке. Если вы нашли возможность в
документации, но не видите её в работе — это, как правило, не баг, а **граница
безопасности**.

## Когда использовать

Когда функция упомянута в статье или списке возможностей, но недоступна в интерфейсе
или CLI.

## Пошагово: как проверить статус

### 1. Посмотрите frontmatter статьи

У каждой статьи есть поля `status` и `feature_flags`. Значение `status`:

- `stable` — стабильно и доступно;
- `experimental` — реализовано, но может меняться;
- `disabled` — выключено по умолчанию, включается сознательно и/или под одобрением;
- `draft` — черновик, поведение не гарантировано.

Если `status: disabled` или в `feature_flags` указан флаг, который у вас выключен —
функция ожидаемо недоступна.

### 2. Проверьте фактические флаги платформы

```bash
uv run raytsystem platform-status --json
```

Сверьте с умолчаниями в `config/platform.yaml` и `config/raytsystem.toml`. Выключены по
умолчанию, среди прочего: runtime-исполнение (`runtime_execution_enabled`,
`codex_local_enabled`, `claude_local_enabled`), запланированные heartbeats, адаптер
Promptfoo и удалённая генерация, экспорт OTLP, адаптеры ACP и A2A-шлюз, внешние
уведомления, ограниченное (прикладное) шифрование, внешнее MCP-исполнение и внешний
KMS. Полный перечень — [/reference/feature-flags](/reference/feature-flags).

### 3. Сопоставьте с известными границами

`docs/STATUS.md` прямо перечисляет, что остаётся выключенным и fail-closed. Например,
удалённый web/yt-dlp-фетч, продвижение реального корпуса (default-deny), OCR и
модельные веса — недоступны без отдельного одобрения.

## Ожидаемый результат

Вы понимаете, почему функция описана, но недоступна, и знаете, каким флагом или
одобрением она гейтится.

## Ограничения и безопасность

- «Выключено по умолчанию» — это осознанная граница, а не недоделка.
- Включение внешних флагов и выдача одобрений — сознательные действия с
  последствиями; см. [/security/defaults](/security/defaults).

## Частые ошибки

- Считать выключенную по умолчанию функцию «работающей».
- Путать `experimental` со `stable`: экспериментальные возможности могут меняться.

## Когда открыть issue

Если `status` статьи — `stable`, флаг включён, а функция всё равно отсутствует —
это расхождение документации и поведения; приложите ссылку на страницу и вывод
`platform-status --json` (без секретов).

## Связанные страницы

- [/reference/feature-flags](/reference/feature-flags)
- [/security/defaults](/security/defaults)
- [/getting-started/capabilities-and-limits](/getting-started/capabilities-and-limits)
- [/troubleshooting/feature-flag-off](/troubleshooting/feature-flag-off)

## Источники истины

- `config/platform.yaml`
- `config/raytsystem.toml`
- `docs/STATUS.md`
