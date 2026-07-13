---
title: "Skills (интерфейс)"
description: "Каталог, безопасный Markdown-просмотр, policy-bound редактор локальных skills и создание локальной копии read-only skill."
audience:
  - user
  - operator
  - developer
status: stable
feature_flags: []
related_commands:
  - raytsystem ui
  - raytsystem agent preflight
related_pages:
  - /agents/overview
  - /agents/creating-extensions
  - /interface/agents
  - /interface/context
  - /interface/overview
  - /security/overview
  - /troubleshooting/generation-conflict
  - /reference/api
source_of_truth:
  - path: src/raytsystem/catalog.py
  - path: src/raytsystem/skill_authoring.py
  - path: src/raytsystem/webapp/app.py
  - path: web/src/features/SkillsSurface.tsx
  - path: web/src/features/SkillDetailView.tsx
last_verified_against: "2026-07-12 / schema v1.4.0"
---

# Skills (интерфейс)

## Что это

Маршрут `/skills` показывает инертные skill-определения из разрешённого каталога.
Каноническое имя всегда остаётся английским и не переводится: `raytsystem-watch`,
`raytsystem-query`, `raytsystem-research`, `raytsystem-save`. Русскими остаются описания,
статусы, названия полей, действия и ошибки.

Наличие skill не означает, что он запущен. Просмотр, предпросмотр правки и
сохранение не выполняют инструкцию, tool или workflow.

## Список

Каждая строка или карточка показывает:

- `skill_id` как каноническое имя;
- description, pack и version;
- trust и sensitivity;
- permissions и test status;
- enabled/restricted;
- editable/read-only и типизированную причину;
- связанных Agent.

Поиск работает по `skill_id` и описанию. Выбор открывает специализированную Skill
Detail View, а не общий metadata Inspector.

![Список Skills с policy, trust, tests, permissions и связанными Agent](/img/interface/skills/skills-list-dark.png)

*Документационный снимок на детерминированном синтетическом fixture; реальные skills не копировались.*

## Skill Detail View

Вкладки детальной страницы:

- **Обзор** — `skill_id`, description, version, pack, trust, sensitivity, test status,
  source hash, безопасный относительный source path, связанные Agent и workflows;
- **Инструкция** — полный безопасный Markdown или точный исходный текст;
- **Permissions** — filesystem, network, tools, requirements к secrets, approvals, side
  effects и sensitivity;
- **Tools** — typed Tool Hub relationships: tool ID, provider, read/write, approval policy и
  health; в текущем контракте availability честно равна `not_modeled`;
- **Tests** — test status, связанные evals, последняя проверка, команды и known
  limitations, если они есть в typed metadata;
- **История** — текущая безопасная authoring revision и hashes, если она записана.

`permission_boundary` — типизированный объект. `declared_permission_ids` содержит только
ID, объявленные каталогом, а секции `filesystem`, `network`, `tools`, `secrets`, `approvals`
и `side_effects` имеют собственные `availability` и `items`. Значение `not_modeled` означает
«данных нет», а не «доступ разрешён»; UI не превращает permission ID в выдуманное эффективное
полномочие.

В текущем API `workflows.availability` и `tools.availability` равны `not_modeled`; полная
цепочка исторических revisions также ещё не проецируется — detail возвращает только
текущую authoring revision, если она есть. Эти пустые состояния не заменяются догадками:
Интерфейс не пытается вывести permissions, tool IDs или workflows из прозы `SKILL.md`.

## Безопасный Markdown

Режим **«Предпросмотр»** показывает headings, paragraphs, lists, code blocks, tables,
blockquotes, ссылки и inline code как React-элементы. Режим **«Исходный Markdown»**
показывает разрешённый текст байт-в-байт после UTF-8 декодирования.

Не исполняются и не вставляются как active DOM:

- HTML и inline event handlers;
- `script`, `iframe`, plugins и remote embeds;
- команды из code block или прозы;
- права, которые текст пытается себе выдать.

Рендерер ограничивает глубину вложенных blockquote, общее число preview nodes, строк/ячеек
таблиц и число колонок. Сверх лимита preview явно сокращается, поэтому специально созданный
Markdown не может вызвать неограниченную рекурсию или разрастание DOM. Режим
**«Исходный Markdown»** при этом остаётся точным.

Если sensitivity gate запрещает disclosure, API не возвращает тело документа ни в одном
режиме.

![Безопасный Markdown preview Skill](/img/interface/skills/skill-preview-dark.png)

*Инструкция рендерится как inert React DOM без HTML execution, remote embeds и выполнения команд.*

## Когда skill можно редактировать

Политику вычисляет сервер. Браузер не может сам выставить `editable`.

| Происхождение | Прямая правка | Локальная копия |
| --- | --- | --- |
| Enabled `pack_local`, trust `user`, sensitivity не restricted/secret, точный `skills/<skill_id>/SKILL.md` | Да | Да |
| Official bundled | Нет | Да |
| Installed pinned pack | Нет | Да |
| Состояние installed packs нельзя достоверно прочитать | Нет, policy закрывается безопасно | Нет |
| Generated или другой safe non-local pack | Нет | По серверной policy |
| Restricted/secret, unverified provenance, unsafe path, disabled | Нет | Нет |
| Historical revision | Нет | Нет |

Рядом с read-only skill показывается типизированная причина. Кнопка «Создать локальную
копию» появляется только при `forkable: true`.

## Редактирование локального skill

1. Откройте editable skill и нажмите **«Редактировать»**.
2. Измените Markdown. Доступны edit/preview, keyboard navigation, cancel и
   предупреждение о несохранённых изменениях.
3. Откройте предпросмотр сохранения. Он покажет validation errors/warnings, нормализованный
   diff, связанных Agent и влияние на активные workflows, если такая typed связь есть.
4. Подтвердите save. Повторное нажатие с тем же idempotency key не создаст вторую
   revision.

![Редактор локального Skill с preview, validation и diff](/img/interface/skills/skill-editor-dark.png)

*Запись доступна только после server-side policy и CAS-проверок; frontend не передаёт filesystem path.*

Перед записью сервер проверяет:

- YAML frontmatter и обязательные `name`, `description`, `version`, `permissions`, `test_status`;
- совпадение `name`, `skill_id` и имени каталога;
- UTF-8, NUL и HTTP-лимит всего JSON body 64 КиБ (request DTO также ограничивает
  поле `content`; байтовая HTTP-граница срабатывает первой), sensitivity и secret scan;
- точный allowlisted path, отсутствие symlink/unsafe hardlink;
- `expected_catalog_sha256` и `expected_source_sha256`;
- локальную сессию, same-origin CSRF и idempotency key.

Путь не передаётся из frontend: сервер сам выводит
`skills/<skill_id>/SKILL.md`. На последней границе записи используется guarded no-replace:
исходная и предложенная версии фиксируются проверяемыми inode/hash-свидетелями, а новая версия
устанавливается только в свободное имя без молчаливого overwrite. После этого `CatalogService`
перечитывает точный файл, пересчитываются hashes, проверяется, что несвязанные определения
каталога не изменились, обновляется catalog projection и записываются revision/audit event.
Fsync-журнал в `ops/skill-authoring-recovery/` связывает filesystem-переход с точным
scope/idempotency key/request hash receipt в SQLite: startup recovery завершает подтверждённую
операцию, а неподтверждённую откатывает только при совпадении ожидаемых hash и inode.
Catalog-derived чтения, execution employee projection и подготовка workspace разделяют с
authoring межпроцессный read/write fence, поэтому не выдают и не используют промежуточную
версию. Неоднозначное или стороннее состояние сохраняется для ручного recovery, а не
перезаписывается. Клиент инвалидирует только связанные `skills`, конкретный `skill`, `catalog`
и `agents` query keys.

Любой сохранённый вариант получает эффективный `test_status: pending`, даже если в отправленном
тексте был `pass`. Это не ошибка: редактор не аттестует тесты и не запускает skill.

## Создание локальной копии

1. Выберите read-only skill с `forkable: true` и нажмите **«Создать локальную копию»**.
2. Проверьте предложенный уникальный `skill_id`, безопасный destination и diff.
3. Подтвердите создание.

Исходный skill не меняется. Копия создаётся как `pack_local` / trust `user`, получает новый
hash, revision/audit record и `test_status: pending`.

## Конфликт параллельной правки

Если catalog или `SKILL.md` изменился после открытия редактора, сервер возвращает typed conflict и
ничего не перезаписывает. Интерфейс показывает вашу версию, актуальную версию и diff,
если disclosure policy разрешает текст.

Автоматического merge нет. Нажмите **«Загрузить актуальную основу»**: редактор подставит
актуальный текст и свежие catalog/source hashes, оставив предыдущую версию видимой в блоке
конфликта. Вручную перенесите нужные фрагменты, снова просмотрите diff и только затем сохраните.
Подробнее —
[Конфликт редактирования Skill](/troubleshooting/skill-edit-conflict).

![Типизированный conflict без автоматического merge](/img/interface/skills/skill-conflict-dark.png)

*Версия пользователя, актуальная версия и diff остаются видимыми; stale source не перезаписывается.*

## Ограничения и безопасность

- Редактор не запускает validation command, skill, workflow, Tool Hub или provider.
- `pass` после save не показывается без отдельной аттестации.
- Official/pinned источник никогда не меняется при fork.
- Restricted content не раскрывается в preview/raw/diff и не копируется.
- Ни frontend, ни `SKILL.md` не выбирают файловый путь и не расширяют permissions.
- Файловая система и SQLite не дают переносимой общей 2PC-транзакции. Durable recovery journal
  закрывает crash-window и никогда не удаляет/перезаписывает версию, происхождение которой нельзя
  доказать; такое неоднозначное состояние возвращает `manual_recovery_required`.
- Для fork остаётся минимальное окно между созданием нового каталога и записью его проверяемого
  recovery marker. Сбой в этой точке может оставить пустой каталог; автоматическое recovery
  закрывается наглухо и не удаляет его без доказательства владения.

## Связанные страницы

- [Агенты и расширение](/agents/overview)
- [Создание skill, agent и pack](/agents/creating-extensions)
- [Агенты (интерфейс)](/interface/agents)
- [Безопасность](/security/overview)
- [Конфликт редактирования Skill](/troubleshooting/skill-edit-conflict)
- [HTTP API: Agents и Skills](/reference/api)

## Источники истины

- `src/raytsystem/catalog.py`
- `src/raytsystem/skill_authoring.py`
- `src/raytsystem/webapp/app.py`
- `web/src/features/SkillsSurface.tsx`
- `web/src/features/SkillDetailView.tsx`
