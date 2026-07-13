---
title: "Безопасность: модель"
description: "Как устроена защита raytsystem: local-first, только loopback 127.0.0.1, проверки Host/Origin/CSRF, сессии и идемпотентность, защищённые пути и запрет опасных действий из UI."
audience: [operator, developer]
status: stable
feature_flags: [runtime_execution_enabled, codex_local_enabled, claude_local_enabled]
related_commands:
  - "uv run raytsystem doctor"
  - "uv run raytsystem status"
  - "uv run raytsystem secrets-status"
related_pages:
  - /security/approvals
  - /security/defaults
  - /security/emergency-controls
  - /security/secrets-backup
  - /reference/feature-flags
  - /reference/api
source_of_truth:
  - path: src/raytsystem/webapp/security.py
  - path: src/raytsystem/skill_authoring.py
  - path: docs/10-execution-security.md
  - path: docs/04-security-scope.md
last_verified_against: "schema v1.4.0"
---

# Безопасность: модель

## Что это

raytsystem спроектирован как локальная система (local-first). Веб-интерфейс и API работают
только на петлевом адресе `127.0.0.1` (loopback) и не выставлены наружу. Всё, что приходит
из браузера, из текста задач, из содержимого репозитория, из меток графа, из импортированных
`AGENTS.md` / `SKILL.md`, из вывода модели и из JSON командной строки, считается недоверенными
данными. Полномочия (authority) берутся только из типизированной серверной конфигурации,
текущих неизменяемых привязок, решений политики, точечных разрешений (approvals) и живых
токенов ограждения (fencing tokens). Источник:
`docs/10-execution-security.md`.

## Когда использовать

Читайте эту страницу, когда нужно понять границы доверия перед тем, как включать любой
шлюзованный функционал, разбираться в отказе доступа в UI или объяснять, почему интерфейс
не принимает произвольную команду.

## Слои защиты в вебе

Все запросы проходят через `SecurityMiddleware` до того, как попадут в приложение. Источник:
`src/raytsystem/webapp/security.py`.

- Только loopback. Заголовок `Host` должен совпадать с разрешённым списком, иначе запрос
  отклоняется (`421 host_rejected`) — защита от DNS rebinding.
- Same-origin. Для небезопасных методов (`POST/PUT/PATCH/DELETE`) заголовок `Origin`
  проверяется по allowlist, иначе `403 origin_rejected`.
- Сессия и CSRF. API-запрос без валидной сессионной cookie получает `401`; небезопасный
  метод без совпадающего CSRF-токена — `403 csrf_rejected`. Сессия имеет срок жизни (TTL).
- Идемпотентность. Каждый небезопасный запрос обязан нести корректный `Idempotency-Key`
  (8–512 байт), иначе `400 idempotency_required`.
- Только JSON и ограниченный размер тела (`415`, `413`), плюс строгие заголовки ответа:
  CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`,
  `Permissions-Policy` и `Cache-Control: no-store` для API.

## Что нельзя сделать через HTTP

- Нет ни одного HTTP-эндпоинта для запуска модели, shell, ингеста, promotion или публикации.
- `GET/HEAD` никогда не пишут: они открывают состояние только на чтение и не инициализируют
  базу, граф или рабочую область. Источник:
  `docs/10-execution-security.md`.
- HTTP принимает только типизированные ID, ограниченные перечисления, ожидаемую генерацию
  и ключ идемпотентности. Он никогда не принимает исполняемый файл, argv, фрагмент shell,
  переменные окружения, cwd или произвольный путь. То есть UI не передаёт `cwd/command/argv/env`.

## Отдельная граница редактирования Skills

Веб-интерфейс может изменить только skill, который серверная policy пометила как
editable. Это узкая локальная каталожная мутация, а не запуск модели или команды. Граница
устроена так:

- frontend передаёт `skill_id` и текст, но не filesystem path;
- сервер сам выводит `skills/<skill_id>/SKILL.md` и дополнительно к session/CSRF/idempotency
  требует expected catalog и source SHA-256;
- прямая правка разрешена только enabled, user-trusted `pack_local` skill на точном
  пути и при допустимой sensitivity;
- official, pinned, generated, historical, external, restricted и unverified-origin источники
  read-only; forkability — отдельная policy;
- неизвестное или повреждённое состояние active package manifests закрывает edit/fork policy,
  а не предполагает локальное происхождение;
- перед записью проверяются UTF-8, HTTP-граница 64 КиБ, frontmatter, `name`/directory, permissions,
  sensitivity, symlink и unsafe hardlink;
- последняя граница записи использует guarded no-replace с hash/inode-свидетелями; точный файл
  перечитывается через `CatalogService`, отдельно проверяется неизменность несвязанных catalog
  definitions, затем пишутся revision и audit event;
- fsync-журнал связывает filesystem transition с точным SQLite scope/idempotency key/request hash
  receipt; startup recovery завершает подтверждённую операцию или откатывает неподтверждённую
  только при доказанном hash/inode;
- catalog-derived UI-чтения, execution employee projection и подготовка runtime workspace
  удерживают общий межпроцессный reader fence и не раскрывают/не используют промежуточную
  filesystem-версию незавершённого authoring;
- stale hash возвращает conflict без перезаписи и без автоматического merge;
- эффективный `test_status` после edit/fork всегда `pending` до отдельной аттестации.

Markdown-просмотр не вставляет active HTML и не исполняет script, iframe, event handler,
remote embed или команду из текста; глубина рекурсивных blockquote, общее число render nodes и
размер таблиц ограничены. При превышении preview явно сокращается, а raw mode остаётся точным.
Save также не запускает skill, tool, workflow или provider.

## Защищённые ресурсы и пути

Под защитой находятся каноническое знание, неизменяемый Task Ledger, исходные файлы
пользователя и история Git, учётные данные провайдеров, approvals, транскрипты запусков и
хост за пределами одной управляемой рабочей области. Рабочие области не выходят за пределы
`.raytsystem/workspaces`; символические и небезопасные жёсткие ссылки закрываются наглухо
(no-follow / `lstat`-проверки перед запуском). Источник:
`docs/10-execution-security.md`.

## Ограничения и безопасность

- Реальное исполнение рантайма по умолчанию отключено и требует включения глобального флага,
  флага провайдера и решения политики `ALLOW` (см. `defaults.md`).
- Опасные действия (push, publish, deploy, deletion, payment, egress приватного корпуса,
  real-corpus promotion) требуют отдельного точечного разрешения (см. `approvals.md`).
- Создание/правка локального skill не даёт ему runtime authority и не подтверждает тесты.
- Файловая система и SQLite не объединены переносимой 2PC-транзакцией. Durable journal закрывает
  обычное crash-window, но неоднозначная сторонняя версия приводит к
  `manual_recovery_required`, а не к автоматическому overwrite/delete.
- При fork остаётся минимальное окно между созданием каталога и записью recovery marker: после
  сбоя может остаться пустой каталог, который оператор должен проверить вручную.
- Не проксируйте интерфейс наружу без отдельного решения о развёртывании — модель доверия
  рассчитана на loopback.

## Частые ошибки

- Открыли не тот loopback-URL: получите `421` или `403` — откройте точный адрес из
  `uv run raytsystem ui`.
- Ждёте, что UI выполнит произвольную команду: этого не будет by design.

## Связанные страницы

- `approvals.md`
- `defaults.md`
- `emergency-controls.md`
- `reference/feature-flags.md`
- [HTTP API: Agents и Skills](/reference/api)

## Источники истины

- `src/raytsystem/webapp/security.py`
- `src/raytsystem/skill_authoring.py`
- `docs/10-execution-security.md`
- `docs/04-security-scope.md`
