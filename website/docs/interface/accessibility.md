---
title: "Доступность и управление интерфейсом"
description: "Клавиатура, focus, modal dialogs, touch targets, темы, reduced motion и безопасное закрытие форм в web-интерфейсе raytsystem."
audience:
  - operator
  - developer
status: stable
route_document: false
feature_flags: []
related_commands:
  - raytsystem ui
related_pages:
  - /interface/overview
  - /documents/overview
  - /interface/safety
source_of_truth:
  - path: web/src/components/Dialog.tsx
  - path: web/src/components/Menu.tsx
  - path: web/src/components/SurfaceTabs.tsx
  - path: web/src/styles.css
  - path: web/src/test/accessibility.browser.test.tsx
last_verified_against: "working tree 2026-07-13 / WCAG 2.2 AA audit"
---

# Доступность и управление интерфейсом

Web-интерфейс raytsystem поддерживает мышь, single-pointer touch и keyboard-only работу. Доступные названия, состояния и связи строятся на native HTML; ARIA добавляется для tabs, tree, menu, dialog, alertdialog, status и alert.

## Клавиатура

- `Tab` и `Shift+Tab` перемещаются по действиям в логичном порядке.
- Tabs используют `←`/`→`, `Home` и `End`.
- Меню задач использует `↑`/`↓`, `Home`, `End`, `Enter`/`Space` и `Escape`.
- Дерево документов использует стрелки, `Home`, `End`, `Enter` и `Space`.
- `Cmd/Ctrl+K` открывает палитру; внутри editor используется `Cmd/Ctrl+Shift+P`.
- Skip link переводит к `main`; после смены маршрута focus перемещается в основной scroll-region.
- Canvas графа имеет равнозначное табличное представление «Список».

## Focus и target size

Focus-visible обозначается контрастным контуром 2 CSS px и дополнительным halo. В forced-colors используется системный `Highlight`. Минимальный target — 24×24 CSS px; на coarse-pointer основные controls, icon buttons и строки дерева увеличиваются до 44×44.

## Dialogs и сохранность данных

Modal dialogs блокируют фон через `inert`, удерживают focus, блокируют background scroll и возвращают focus к trigger. Безопасные read-only окна закрываются по `Escape` и backdrop. Dirty forms сначала показывают «Закрыть без сохранения?». Conflict, restore, destructive и security-sensitive окна не закрываются простым нажатием backdrop; во время async commit закрытие и повторная отправка блокируются.

## Темы, reflow и движение

Поддерживаются dark, light и high-contrast темы, forced-colors, 320 CSS px и эквивалент 200% zoom. При `prefers-reduced-motion: reduce` отключаются decorative motion, smooth scrolling и бесконечное вращение; текстовые loading/status сообщения остаются.

## Автоматическая и ручная проверка

`npm --prefix web run test:a11y` запускает axe-core в Chromium по всем маршрутам и трём темам. Layout browser suite проверяет desktop, tablet, mobile, 320 px и zoom equivalents. Эти проверки дополняют, но не заменяют ручной keyboard, focus, touch и screen-reader semantics review.
