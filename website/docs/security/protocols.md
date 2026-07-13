---
title: "MCP, ACP и A2A"
description: "Как raytsystem управляет протоколами агентов: MCP-каталог без исполнения, отключённый ACP-адаптер и A2A-шлюз, который отказывается открывать сеть даже при выставленном флаге."
audience: [operator, developer]
status: experimental
feature_flags: [mcp_governance_enabled, external_mcp_execution_enabled, acp_adapter_enabled, a2a_gateway_enabled, a2a_network_exposure_enabled]
related_commands:
  - "uv run raytsystem mcp list --json"
  - "uv run raytsystem mcp validate <revision_id>"
  - "uv run raytsystem mcp approve <revision_id> --approval-id <id>"
  - "uv run raytsystem mcp transition <revision_id> --state <enable|disable|degrade|block> --reason \"...\""
  - "uv run raytsystem protocols status --json"
related_pages:
  - /security/overview
  - /security/approvals
  - /security/defaults
  - /agents/tool-hub
  - /reference/feature-flags
  - /reference/cli
source_of_truth:
  - path: src/raytsystem/tooling/mcp.py
  - path: src/raytsystem/toolhub/mcp_server.py
  - path: ops/decisions/ADR-025-mcp-governance.md
  - path: ops/decisions/ADR-026-acp-adapter-boundary.md
  - path: ops/decisions/ADR-027-a2a-gateway-boundary.md
  - path: src/raytsystem/protocols/acp.py
  - path: config/platform.yaml
last_verified_against: "schema v1.4.0"
---

# MCP, ACP и A2A

## Что это

Три протокольных границы для взаимодействия с внешним кодом и агентами. Все три спроектированы
как границы управления и записи, а не как рабочие каналы исполнения: MCP работает в режиме
каталога, ACP-адаптер и A2A-шлюз выключены по умолчанию. Источник:
`config/platform.yaml`.

## MCP: каталог без исполнения

MCP-управление включено (`mcp_governance_enabled: true`), но внешнее исполнение выключено
(`external_mcp_execution_enabled: false`). Серверы проходят монотонный автомат состояний
DISCOVERED → VALIDATED → APPROVED → ENABLED плюс QUARANTINED, BLOCKED, DEGRADED, DISABLED.
Каноническое знание при этом никогда не пишется. Источник:
`src/raytsystem/tooling/mcp.py`.

На этапе discovery сервер уходит в карантин при: несовпадении хэша пакета, небезопасном
stdio-исполняемом (абсолютные пути, traversal, недопустимые символы), несовпадении манифеста
инструментов/ресурсов/промптов, небезопасных ключевых словах схемы (`$ref`, `$dynamicRef`,
content-кодировки, чрезмерная вложенность) и любой начальной политике, кроме `catalog_only`.

Пока `external_mcp_execution_enabled` выключен, каждая политика инструмента обязана оставаться
`catalog_only` и не может запрашивать сеть, корни ФС или секреты; попытка вызова инструмента
завершается ошибкой, а health отчитывается как `disabled` с кодом
`external_mcp_execution_disabled`. Установка или одобрение сервера не дают никакой рантайм-полномочности.

Одобрение каталога требует точного approval `approve_mcp_catalog` (scope `mcp_catalog`),
привязанного по хэшу к зафиксированному определению (см. `approvals.md`).

```bash
uv run raytsystem mcp list --json
uv run raytsystem mcp validate <revision_id>
uv run raytsystem mcp approve <revision_id> --approval-id <id>
uv run raytsystem mcp transition <revision_id> --state enable --reason "..."
```

`mcp list` возвращает снимок каталога в состоянии `catalog_only`. Каждый переход требует
причины.

### Первосторонний Tool Hub не открывает внешнее MCP execution

Отдельная команда `uv run raytsystem tool serve-mcp --root .` поднимает локальный stdio-сервер
ровно для восьми типизированных `video.*` операций. Это первосторонняя [Tool Hub](/agents/tool-hub),
а не включение произвольного сервера из MCP-каталога. Она не меняет
`external_mcp_execution_enabled: false`, не даёт generic shell и не открывает сеть: штатный диспетчер
не имеет `DestinationBoundDownloadExecutor`. CLI-backed local media tools также fail closed,
пока доверенный host не внедрит pinned executables и root-confined/network-denied executor.

## ACP-адаптер: выключен

`acp_adapter_enabled: false`. Адаптер governs и записывает границу протокола, но не ведёт
глубокую интеграцию рантайма. При выключенном флаге любая операция завершается ошибкой, а
снимок отчитывается как `disabled`. Даже когда адаптер включён, содержимое событий никогда не
сохраняется: каждое событие ограничено 256 КиБ, сводится к `payload_sha256`, сканируется на
секреты и всегда помечается redacted. Привилегированные события (`tool_call`,
`permission_request`, `file_change`) требуют уже существующего решения политики; `tool_call` и
`file_change` дополнительно — approval со scope `acp_privileged_event`. Источник:
`src/raytsystem/protocols/acp.py`,
`ops/decisions/ADR-026-acp-adapter-boundary.md`.

## A2A-шлюз: выключен и только loopback

`a2a_gateway_enabled: false`. Шлюз принимает запросы только с loopback (`127.0.0.1`, `::1`,
`localhost`). Ключевое свойство безопасности: если выставить `a2a_network_exposure_enabled`,
шлюз отказывается работать вовсе — эта реализация не поддерживает удалённую экспозицию, поэтому
комбинация флагов закрывается наглухо, а не открывает слушающий сокет. Всё входящее помещается
в карантин (`trusted=False`, `quarantined=True`); ни один входящий запрос не касается Task
Ledger или канонического знания. Источник:
`ops/decisions/ADR-027-a2a-gateway-boundary.md`.

## Сводный статус

```bash
uv run raytsystem protocols status --json
```

Команда возвращает снимки `acp` и `a2a`, честно сообщающие `disabled` или `loopback_only`.

## Ограничения и безопасность

- Статус страницы — experimental: MCP работает только как каталог, ACP и A2A выключены по
  умолчанию и описаны как границы, а не рабочие каналы.
- Включение внешнего исполнения или сетевой экспозиции — отдельное решение под approval, а не
  «переключение флага»; см. `defaults.md`.

## Связанные страницы

- `overview.md`
- `approvals.md`
- `defaults.md`
- [/agents/tool-hub](/agents/tool-hub)

## Источники истины

- `src/raytsystem/tooling/mcp.py`
- `src/raytsystem/toolhub/mcp_server.py`
- `ops/decisions/ADR-025-mcp-governance.md`
- `ops/decisions/ADR-026-acp-adapter-boundary.md`
- `ops/decisions/ADR-027-a2a-gateway-boundary.md`
- `src/raytsystem/protocols/acp.py`
- `config/platform.yaml`
