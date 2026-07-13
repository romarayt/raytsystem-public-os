---
title: "Конфликт редактирования Skill"
description: "Что делать с skill_edit_conflict: файл или каталог изменился после открытия редактора, поэтому raytsystem не перезаписал актуальную версию."
audience:
  - user
  - operator
  - developer
status: stable
feature_flags: []
related_commands:
  - raytsystem ui
related_pages:
  - /troubleshooting/overview
  - /troubleshooting/generation-conflict
  - /interface/skills
  - /agents/creating-extensions
source_of_truth:
  - path: src/raytsystem/skill_authoring.py
  - path: web/src/features/SkillDetailView.tsx
last_verified_against: "2026-07-12 / schema v1.4.0"
---

# Конфликт редактирования Skill

## Что это

При открытии редактор запоминает SHA-256 источника и всего каталога. Перед save
сервер снова сравнивает оба значения. Если хотя бы одно изменилось, он возвращает
`skill_edit_conflict` и не пишет файл. Это штатная optimistic-concurrency защита.

## Симптом

Вместо успешного save интерфейс показывает, что skill изменился после открытия
редактора. При разрешённом disclosure видны:

- ваш предложенный текст;
- текущий текст на диске;
- diff между ними;
- expected и current catalog/source hashes.

Если sensitivity policy запрещает раскрытие, текст и diff withheld; сам конфликт всё равно
блокирует запись.

## Вероятная причина

Файл изменил другой редактор, другая сессия или checkout. Либо изменился другой
объект каталога, поэтому `expected_catalog_sha256` больше не совпадает с текущим.

## Безопасное решение

1. Не пытайтесь повторить save со старыми hashes.
2. Изучите обе версии и diff; ваша версия остаётся видимой в блоке конфликта.
3. Нажмите **«Загрузить актуальную основу»**. Редактор заменит рабочую основу актуальным
   содержимым и свежими catalog/source hashes, но не выполнит merge.
4. Вручную перенесите нужные фрагменты из показанной пользовательской версии.
5. Откройте новый preview и сохраните со свежими hashes.

raytsystem не делает automatic merge инструкций: такой merge может незаметно изменить
полномочия или процедуру.

## Ожидаемый результат

После save со свежими hashes возвращаются новые source/catalog SHA-256 и revision/audit IDs.
Эффективный test status будет `pending`, пока отдельный verifier не зафиксирует проверку.

## Ограничения и безопасность

- Отклонённая операция ничего не перезаписывает.
- Повтор с тем же idempotency key и тем же payload не создаст вторую revision.
- Не меняйте hash вручную и не обходите conflict прямой записью в `SKILL.md`.

## Когда открыть issue

Откройте issue, если конфликт повторяется при единственной сессии сразу после нового
preview. Приложите typed error code, `skill_id` и **только hashes**; не публикуйте restricted content.

## Связанные страницы

- [Skills (интерфейс)](/interface/skills)
- [Конфликт поколения](/troubleshooting/generation-conflict)
- [Создание skill, agent и pack](/agents/creating-extensions)

## Источники истины

- `src/raytsystem/skill_authoring.py`
- `web/src/features/SkillDetailView.tsx`
