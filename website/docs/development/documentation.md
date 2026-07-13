---
title: "Документация: сборка, публикация, синхронизация"
description: "Где живёт публичная база знаний (website/), как запускать её локально (npm start/build), генератор reference и проверки (coverage, frontmatter, docs-impact), правило синхронизации документации и шлюзованная публикация на GitHub Pages."
audience: [developer, contributor]
status: stable
feature_flags: []
related_commands:
  - "npm --prefix website install"
  - "npm --prefix website run build"
  - "python3 scripts/docs/gen_reference.py --check"
  - "python3 scripts/docs/coverage_check.py"
related_pages:
  - /development/contributing
  - /coverage
  - /security/defaults
  - /reference/cli
  - /reference/feature-flags
  - /reference/routes
  - /reference/api
source_of_truth:
  - path: website/docusaurus.config.ts
  - path: website/package.json
  - path: website/sidebars.ts
  - path: scripts/docs/gen_reference.py
  - path: scripts/docs/coverage_check.py
  - path: scripts/docs/frontmatter_lint.py
  - path: scripts/docs/docs_impact_check.py
  - path: .github/workflows/docs.yml
last_verified_against: "schema v1.4.0"
---

# Документация: сборка, публикация, синхронизация

## Что это

Публичная база знаний — сайт на Docusaurus в каталоге `website/`. Она часть продукта: контент
синхронизируется с кодом и проверяется в CI. Ручной сайдбар не ведётся — навигация
генерируется из дерева папок и файлов `_category_.json` (`website/sidebars.ts`), поэтому добавить
статью — значит создать один Markdown-файл.

## Где что лежит

- `website/docs/**` — статьи (Markdown/MDX), сгруппированные по папкам-разделам.
- `website/docusaurus.config.ts` — конфигурация сайта, локали, тема, ссылки.
- `website/package.json` — npm-скрипты сборки и проверок.
- `scripts/docs/` — генератор reference и линтеры документации.
- `.github/workflows/docs.yml` — проверки и публикация в CI.

## Локальный запуск

Из каталога `website/`:

```bash
npm install        # или npm ci для точной установки из lock-файла
npm start          # dev-сервер с горячей перезагрузкой
npm run build      # продакшн-сборка
npm run serve      # отдать уже собранный сайт
```

Сборка строгая: `onBrokenLinks`, `onBrokenAnchors` и `onBrokenMarkdownLinks` установлены в
`throw` (`website/docusaurus.config.ts`), поэтому любая битая ссылка валит `npm run build`.
Поиск полностью локальный (плагин `docusaurus-search-local`), без обращений к сети.

## Генератор reference

`scripts/docs/gen_reference.py` — единственный источник машинных reference-страниц
(дерево CLI, флаги функций, типы узлов workflow, UI-маршруты, Agents/Skills HTTP API и
метаданные реестра схем). Он
**не пишет прозу** — только выгружает контракты, которые уже есть в коде.

```bash
python3 scripts/docs/gen_reference.py --write   # (пере)сгенерировать страницы
python3 scripts/docs/gen_reference.py --check   # упасть, если страницы устарели
```

Каждая сгенерированная страница несёт баннер «Generated — do not edit». Правьте контракт в
коде, затем перегенерируйте; вручную такие страницы не редактируют.

## Проверки качества

Помимо сборки сайта запускаются три линтера (в `website/package.json` они также доступны как
npm-скрипты `check:reference`, `check:coverage`, `check:frontmatter`):

- `scripts/docs/coverage_check.py` — сопоставляет реальные поверхности (CLI-команды,
  веб-маршруты, флаги, реестр схем) со страницами и ловит недокументированный маршрут,
  «страницу-сироту», пропущенную CLI-команду и не раскрытый отключённый по умолчанию флаг.
- `scripts/docs/frontmatter_lint.py` — проверяет обязательный frontmatter и статус,
  уникальность слагов, реальность флагов/команд и разрешимость ссылок, ищет абсолютные пути
  и утечки секретов.
- `scripts/docs/docs_impact_check.py` — падает, если изменение публичной поверхности не
  тронуло `website/docs/**`; escape-hatch — обоснованный `docs-not-needed`.

## Правило синхронизации

Проектная политика проста: не держать вторую копию правил. `CLAUDE.md` указывает читать
`AGENTS.md`, а не заводить параллельный свод политик. Практическое следствие для документации:
публичные изменения документируются в том же change set (см.
[Участие в разработке](/development/contributing)), а отключённые по умолчанию функции
описываются как **off** и через какой шлюз включаются, а не как рабочие (см.
[Что отключено по умолчанию](/security/defaults)).

## Публикация на GitHub Pages

CI-процесс — `.github/workflows/docs.yml`. На PR и пуш в `main` выполняется job проверок
(генератор `--check`, три линтера, `npm ci`, `npm run build`). Job публикации выполняется
**только на пуше в `main`** и шлюзован:

- Владелец репозитория включает GitHub Pages.
- Канонический GitHub owner — `romarayt`; каноническое имя репозитория
  `raytsystem-public-os` уже задано по умолчанию. Значения можно переопределить через
  `DOCS_ORG` / `DOCS_REPO` / `DOCS_URL` / `DOCS_BASE_URL`.

Не публикуйте без разрешения: деплой не запускается на форках и на PR, только на `main` после
прохождения всех проверок.

## Пример

```bash
python3 scripts/docs/gen_reference.py --check   # reference актуален?
python3 scripts/docs/coverage_check.py          # поверхности покрыты?
npm --prefix website run build                  # сборка со строгой проверкой ссылок
```

## Ожидаемый результат

- `gen_reference.py --check` и три линтера завершаются без ошибок.
- `npm run build` собирает статический сайт в `website/build` без битых ссылок.
- Публикация происходит автоматически только после мёржа в `main` при включённых Pages.

## Ограничения и безопасность

- Генератор reference читает только публичные контракты и никогда не выводит абсолютные пути
  или секреты; значения-по-умолчанию, похожие на локальный путь, скрываются
  (`scripts/docs/gen_reference.py`).
- Не вставляйте в статьи абсолютные пути файловой системы, ключи или PII — `frontmatter_lint.py`
  их отклонит.
- Пока плейсхолдеры не заменены, сайт собирается локально, но не должен публиковаться наружу.

## Частые ошибки

- Ручная правка сгенерированной reference-страницы: изменения потеряются, а `--check` упадёт.
- Ссылка на несуществующий слаг: `npm run build` падает из-за `onBrokenLinks: throw`.
- Изменили публичную поверхность без обновления `website/docs/**`: падает `docs_impact_check.py`.

## Связанные страницы

- [Участие в разработке](/development/contributing)
- [Покрытие документации](/coverage)
- [Что отключено по умолчанию](/security/defaults)
- Справочники: [CLI](/reference/cli), [флаги функций](/reference/feature-flags),
  [маршруты](/reference/routes), [HTTP API Agents/Skills](/reference/api)

## Источники истины

- `website/docusaurus.config.ts`
- `website/package.json`
- `website/sidebars.ts`
- `scripts/docs/gen_reference.py`
- `scripts/docs/coverage_check.py`
- `scripts/docs/frontmatter_lint.py`
- `scripts/docs/docs_impact_check.py`
- `.github/workflows/docs.yml`
