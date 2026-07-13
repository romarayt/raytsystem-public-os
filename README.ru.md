<p align="center">
  <img src="assets/github/logo.svg" width="440" alt="Система Райта — агентная система">
</p>

<h1 align="center">raytsystem</h1>

<p align="center">
  <a href="README.md">English</a> · <strong>Русский</strong>
</p>

<p align="center">
  Локальное self-hosted пространство для работы с базой знаний, документами, задачами, агентами и проверяемыми процессами.
</p>

<p align="center">
  <a href="https://github.com/romarayt/raytsystem-public-os/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/romarayt/raytsystem-public-os/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/romarayt/raytsystem-public-os/actions/workflows/docs.yml"><img alt="Документация" src="https://github.com/romarayt/raytsystem-public-os/actions/workflows/docs.yml/badge.svg"></a>
  <a href="https://github.com/romarayt/raytsystem-public-os/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/romarayt/raytsystem-public-os/actions/workflows/codeql.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="Лицензия Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.12–3.14" src="https://img.shields.io/badge/python-3.12%E2%80%933.14-3776AB.svg">
</p>

raytsystem объединяет документы, доказательства, задачи, агентов, навыки, запуски и средства безопасности в одном локальном контуре. Облачный аккаунт не становится центром системы, а интерфейс доступен только через loopback-адрес компьютера пользователя.

> **Статус проекта:** pre-1.0. Локальные детерминированные процессы и web-интерфейс уже доступны. Выполнение через внешних провайдеров и внешние эффекты по умолчанию отключены; часть интеграций остаётся экспериментальной.

## Интерфейс

![Центр управления raytsystem с синтетическими локальными данными](assets/github/hero.png)

<table>
  <tr>
    <td><img src="assets/github/documents.png" alt="Документы raytsystem"></td>
    <td><img src="assets/github/universe.png" alt="Вселенная знаний raytsystem"></td>
  </tr>
  <tr>
    <td><img src="assets/github/agents.png" alt="Каталог агентов raytsystem"></td>
    <td><img src="assets/github/skills.png" alt="Каталог навыков raytsystem"></td>
  </tr>
  <tr>
    <td><img src="assets/github/tasks.png" alt="Задачи raytsystem"></td>
    <td><img src="assets/github/safety.png" alt="Контур безопасности raytsystem"></td>
  </tr>
</table>

Все публичные скриншоты созданы из синтетических демонстрационных данных.

## Возможности

- Локальный Центр управления с состоянием пространства, задач, запусков и безопасности.
- Markdown-документы с поиском, ссылками, backlinks, историей и контролируемым редактированием.
- Вселенная знаний для исследования связей между знаниями, доказательствами, работой, кодом и агентами.
- Долговечные задачи с зависимостями, явными переходами состояний и неизменяемой историей.
- Каталоги агентов и навыков с прозрачными разрешениями и доступностью runtime.
- Проверяемые процессы INGEST, QUERY, LINT, SAVE, RESEARCH, REVIEW и security review.
- Feature flags, approvals, аварийные ограничения, трассировка и отключённые по умолчанию внешние границы.
- Web-интерфейс на `127.0.0.1` без обязательного облачного аккаунта.

## Быстрый старт

Требования: macOS или Linux; Windows через WSL 2; Python 3.12–3.14; [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/romarayt/raytsystem-public-os.git raytsystem
cd raytsystem
uv sync --dev
uv run raytsystem doctor
uv run raytsystem start
```

Команда `start` открывает `http://127.0.0.1:8765`. Для остановки используйте `Ctrl+C`. API-ключ, облачный аккаунт и расширение браузера для безопасного локального старта не нужны.

Подробные инструкции: [установка](website/docs/getting-started/installation.md), [первый запуск](website/docs/getting-started/first-run.md), [обновление](website/docs/getting-started/upgrading.md), [резервное копирование](website/docs/security/secrets-backup.md) и [удаление](website/docs/getting-started/uninstall.md).

## Модель безопасности

- Локальный режим и web-сервер только на loopback-интерфейсе.
- Внешняя отправка, публикация, загрузка, платежи, удаление, передача приватного корпуса и реальное продвижение данных по умолчанию запрещены.
- Проверки same-origin session, CSRF и idempotency для изменяющих запросов браузера.
- Импортированные файлы и Markdown каталога считаются недоверенными данными, а не инструкциями.
- Секреты, локальные базы, архивы, логи, кеши, runtime-состояние и приватный корпус исключены из публичного репозитория и проверяются в CI.

Подробнее: [SECURITY.md](SECURITY.md). Уязвимости отправляйте приватно Роме Райту в [Telegram](https://t.me/romarayt), не создавая публичный issue.

## Документация и поддержка

- Документация: [romarayt.github.io/raytsystem-public-os](https://romarayt.github.io/raytsystem-public-os/)
- Поддержка и участие: [SUPPORT.md](SUPPORT.md) и [CONTRIBUTING.md](CONTRIBUTING.md)
- Канал автора: [@romarayt](https://t.me/romarayt)
- Ошибки и предложения: [GitHub Issues](https://github.com/romarayt/raytsystem-public-os/issues)

## Лицензия

raytsystem распространяется по лицензии [Apache License 2.0](LICENSE). Сведения об атрибуции и прямых зависимостях находятся в [NOTICE](NOTICE), [AUTHORS.md](AUTHORS.md), [CITATION.cff](CITATION.cff) и [уведомлениях о сторонних компонентах](docs/THIRD_PARTY_NOTICES.md).
