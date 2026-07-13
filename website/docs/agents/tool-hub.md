---
title: "Tool Hub: типизированные video-инструменты"
description: "Первая встроенная поверхность Tool Hub: восемь типизированных video.* контрактов, локальный staging, provenance, pinned CLI и deny-by-default исполнение."
audience: [operator, developer]
status: experimental
feature_flags: []
related_commands:
  - "uv run raytsystem tool list --json"
  - "uv run raytsystem tool invoke video.transcript --input tool-input.json --root . --json"
  - "uv run raytsystem tool watch TEXT --source-kind transcript --mode timeline --root . --json"
  - "uv run raytsystem tool serve-mcp --root ."
related_pages:
  - /agents/overview
  - /agents/creating-extensions
  - /security/defaults
  - /security/approvals
  - /reference/cli
source_of_truth:
  - path: src/raytsystem/toolhub/contracts.py
  - path: src/raytsystem/toolhub/registry.py
  - path: src/raytsystem/toolhub/video.py
  - path: src/raytsystem/toolhub/runner.py
  - path: src/raytsystem/toolhub/dispatch.py
  - path: src/raytsystem/toolhub/mcp_server.py
  - path: src/raytsystem/toolhub/cli.py
last_verified_against: "tool contract 1.2.0 / 2026-07-12"
---

# Tool Hub: типизированные video-инструменты

## Что это

Tool Hub — это первая встроенная граница исполнения raytsystem для обработки видео,
аудио, транскриптов и кадров под контролем внешней capability. Вместо сырого shell он
публикует ровно восемь
контрактов `video.*`. Каждый контракт имеет Pydantic/JSON Schema для входа и выхода,
зафиксированные побочные эффекты, таймаут, форматы, границы файлов, правило approval,
политику редакции и поля provenance. Версия контрактов — `1.2.0`.

Это пока **экспериментальный фундамент**, а не готовый кроссплатформенный `/watch`.
Project-local адаптеры Claude Code и Codex не входят в эту поверхность. `/watch` нельзя
называть встроенным или кроссплатформенным, пока оба адаптера и их end-to-end проверки
не пройдут отдельную аттестацию.

## Восемь контрактов

| Tool ID / MCP-имя | Типизированный вход | Выход | CLI-зависимость | Таймаут |
|---|---|---|---|---:|
| `video.probe` / `video_probe` | Локальный `VideoSource` + `VideoLimits` | Длительность, формат, media streams, provenance | `ffprobe` | 30 с |
| `video.download` / `video_download` | HTTP(S) `VideoSource`, `NetworkApproval`, limits | Загруженный media artifact + network provenance | `yt-dlp` | 900 с |
| `video.transcript` / `video_transcript` | Готовый текст/файл или локальное media с sidecar | Метод `supplied`, `sidecar` или `unavailable`; сегменты | нет | 30 с |
| `video.extract_audio` / `video_extract_audio` | Локальное media, PCM codec, sample rate, channels, limits | WAV artifact + provenance | `ffprobe`, `ffmpeg` | 900 с |
| `video.extract_frames` / `video_extract_frames` | Локальное media, timestamps или interval, width, limits | Набор JPEG-кадров с timestamp и SHA-256 | `ffprobe`, `ffmpeg` | 900 с |
| `video.ocr_frames` / `video_ocr_frames` | Ссылки на staged-кадры, `eng`/`rus`/`eng+rus` | OCR-текст и текстовые artifacts | `tesseract` | 300 с |
| `video.inspect_frames` / `video_inspect_frames` | Staged-кадры + опциональный OCR | Локальный evidence manifest; статус `partial` | нет | 60 с |
| `video.summarize_timeline` / `video_summarize_timeline` | SHA-256 источника, сегменты речи, frame evidence | Детерминированная JSON/Markdown timeline | нет | 60 с |

Политика `ToolSpec` по каждому ID:

| Tool ID | `side_effects` | `network_access` / `approval` | `max_frames` | Набор форматов |
|---|---|---|---:|---|
| `video.probe` | `filesystem_read`, `filesystem_write` | `none` / `none` | 0 | media |
| `video.download` | `network_read`, `filesystem_write` | `destination_bound_approval` / `destination_bound_network` | 0 | media |
| `video.transcript` | `filesystem_read`, `filesystem_write` | `none` / `none` | 0 | transcript |
| `video.extract_audio` | `filesystem_read`, `filesystem_write` | `none` / `none` | 0 | media |
| `video.extract_frames` | `filesystem_read`, `filesystem_write` | `none` / `none` | 48 | media |
| `video.ocr_frames` | `filesystem_read`, `filesystem_write` | `none` / `none` | 48 | frame |
| `video.inspect_frames` | `filesystem_read`, `filesystem_write` | `none` / `none` | 48 | frame |
| `video.summarize_timeline` | `filesystem_read`, `filesystem_write` | `none` / `none` | 48 | timeline |

У всех восьми записано `generic_shell: false`; точные списки форматов раскрыты ниже.

`video.inspect_frames` не притворяется моделью зрения. Он сверяет staged-кадры и OCR,
создаёт `local_evidence_manifest` и возвращает `host_visual_analysis_required`. Поле
`visual_observation` остаётся пустым, пока доверенный host не передаст результат визуального
анализа. `video.summarize_timeline` тоже не вызывает модель: он детерминированно объединяет
уже полученные доказательства.

## Единая схема входа и выхода

`VideoSource.kind` принимает `local_file`, `url` или `transcript`. Конкретный tool сужает этот набор:

- `probe`, `extract_audio` и `extract_frames` принимают только локальное media;
- `download` — только абсолютный HTTP(S) URL;
- `transcript` — готовый текст, `.txt`/`.md`/`.vtt`/`.srt` или локальное media с одноимённым
  `.vtt`, `.srt` или `.txt` sidecar;
- `ocr_frames` и `inspect_frames` — только `ArtifactRef`, уже созданные Tool Hub в staging;
- `summarize_timeline` — типизированные `TranscriptSegment` и `FrameEvidence`, а не произвольный текст.

Общий выход содержит `status` (`completed`, `partial` или `blocked`), `partial_reasons`,
`artifacts` и `provenance`. Каждый `ArtifactRef` хранит project-relative путь, SHA-256, размер,
media type и, если это кадр, timestamp.

Полные JSON Schema не дублируются вручную. Их выдаёт реестр:

```bash
uv run raytsystem tool list --json
```

## Границы и форматы

Зарегистрированный operating budget для всех восьми tools: файл до 2 ГиБ и media до 4 часов.
Для кадровых стадий реестр указывает до 48 кадров. `VideoLimits` по умолчанию совпадает с
этим бюджетом и дополнительно ограничивает транскрипт 8 МиБ. Верхние границы валидатора для
явно переданного `VideoLimits` — 8 ГиБ, 24 часа, 240 кадров и 32 МиБ транскрипта. Эти потолки
не являются approval и не открывают сеть; внешняя policy может сузить их дальше.

Поддерживаются:

- media: `mp4`, `mov`, `m4v`, `webm`, `mkv`, `avi`, `mpeg`, `mpg`, `mp3`, `m4a`, `wav`,
  `flac`, `ogg`, `opus`;
- транскрипты: `vtt`, `srt`, `txt`, `md`;
- кадры: `jpg`, `jpeg`, `png`, `webp`;
- timeline artifacts: `json`, `md`.

## Staging, идемпотентность и provenance

Реестр объявляет три корня: `workspace:read`, `ops/staging/watch:write` и
`launcher-pinned-runtime:read`. Последний не является свободным корнем: он означает только чтение конкретных
бинарников, зафиксированных доверенным launcher. Tool Hub проверяет,
что локальный источник лежит внутри project root, а производные артефакты попадают только в
`ops/staging/watch/`. Каждый компонент stage проверяется на symlink и выход за корень. Ни исходное
media, ни каноническое knowledge, ни ledger не изменяются.

Путь stage детерминирован хэшем источника, tool ID, версией контракта и каноническим
запросом. Повтор того же запроса читает `result.json`, сверяет tool ID, invocation, source,
network origin, approval, размер и SHA-256 артефактов, а затем не запускает CLI повторно.
Семантические поля probe, transcript, OCR, frame evidence и timeline повторно сверяются с
соответствующими retained-артефактами. Несовпадение привязки, семантики или хэша, попытка перезаписи или
конкурентный writer завершаются ошибкой.
Новый payload сначала `fsync`-ится как retained `.pending`, затем публикуется no-overwrite hard link;
pending-файл не удаляется автоматически и остаётся под той же retention policy.

Provenance включает:

- `source_identity` с SHA-256 и безопасным locator;
- `invocation_sha256` и `tool_contract_version`;
- версии фактически использованных allowlisted-бинарников;
- SHA-256 выходных артефактов;
- сетевой origin и `approval_id`, если они были применены;
- обязательные `untrusted_content: true` и `retention_policy: retain_until_review`.

Tool Hub не удаляет retained-артефакты автоматически. Retention-policy означает «сохранять
до проверки»; отдельная процедура очистки в текущем контракте не зарегистрирована.

## Безопасность и сеть

Транскрипт, OCR, текст на кадрах и само импортированное media всегда считаются
недоверенными данными, а не инструкциями. Политика редакции запрещает сохранять
query/fragment URL, credentials, cookies, tokens, сырой stderr процесса и секреты окружения. В Markdown timeline
контрольные символы, HTML и синтаксис ссылок/изображений нейтрализуются, чтобы imported text
не запускал внешние fetch при рендеринге.

Внешние бинарники ограничены `ffprobe`, `ffmpeg`, `yt-dlp` и `tesseract`. Для каждого executable
доверенный launcher должен передать `ExecutablePin`: абсолютный real path, SHA-256 бинарника, точную
строку версии, `sys.platform` и архитектуру `machine`. Поиск по унаследованному `PATH` запрещён;
хэш, версия и file identity проверяются перед запуском. Argv проходит строгую грамматику для
конкретной операции. Shell не запускается; свободного `command`, `argv` или выбора executable в публичном
контракте нет. Для локального media `ffprobe`/`ffmpeg` дополнительно ограничены протоколом `file`
и списком demuxer-форматов, выведенным из расширения проверенного source.

Одних pins недостаточно. `probe`, `extract_audio`, `extract_frames` и `ocr_frames` требуют от host
отдельный local executor, который гарантирует запрет сети и доступ только к одобренному source/stage
на уровне OS capability sandbox. Низкоуровневый pinned runner сам такой изоляции не обещает, поэтому
штатные CLI/MCP fail closed для CLI-backed media tools до внедрения такого executor доверенным host.

### Удалённые URL запрещены по умолчанию

`video.download` требует одновременно:

1. неистёкший `NetworkApproval` сроком не более 15 минут, привязанный к `video.download`,
   точному origin и SHA-256 валидированной URL-identity;
2. внешний `DestinationBoundDownloadExecutor`, который владеет DNS resolution, redirect-policy и
   сетевыми socket-ограничениями и принимает только одобренный origin.

Переданного в JSON approval **недостаточно**. Штатные `raytsystem tool invoke`, `raytsystem tool watch`
и `raytsystem tool serve-mcp` не внедряют download executor, поэтому всегда fail closed для URL. Удалённый URL
нельзя называть поддерживаемым в штатной сборке, пока внешний runtime не внедрит и не проверит такую
возможность. URL с credentials, private/loopback/single-label destination и нестандартным портом отклоняются
до запуска `yt-dlp`.

## CLI

Посмотреть контракты и MCP-имена:

```bash
uv run raytsystem tool list --json
```

Вызвать один tool. `tool-input.json` должен содержать JSON-объект, совпадающий с `input_schema`:

```json title="tool-input.json"
{
  "source": {
    "kind": "transcript",
    "value": "00:01 --> 00:03\nПример транскрипта"
  }
}
```

```bash
uv run raytsystem tool invoke video.transcript \
  --input tool-input.json --root . --json
```

Локальный progressive-pipeline имеет отдельную CLI-команду:

```bash
uv run raytsystem tool watch "Готовый текст транскрипта" \
  --source-kind transcript --mode timeline --root . --json
```

Это `raytsystem tool watch`, а не slash-command `/watch`. Доступные режимы CLI: `summary`, `timeline`,
`automation`, `frames`, `transcript`; auto-detection различает существующий локальный файл, URL и
остальной текст. Без host-injected local executor со средой sandbox локальное media не дойдёт до
`ffprobe`/`ffmpeg`: штатная команда fail closed. Даже в аттестованном host без supplied/sidecar транскрипта
конвейер вернёт `partial`, потому что speech-to-text adapter не входит в текущую сборку.

## MCP stdio

```bash
uv run raytsystem tool serve-mcp --root .
```

Сервер — минимальная newline-delimited JSON-RPC stdio-поверхность. Он поддерживает MCP
`2024-11-05`, `2025-03-26` и `2025-06-18`, требует `initialize` + `notifications/initialized`, а затем
обслуживает `ping`, `tools/list` и `tools/call`. Один JSON-RPC request ограничен 16 МиБ.
Ошибки возвращают ограниченные сообщения без сырого stderr, путей, tokens и environment values.

Каждая MCP-дефиниция публикует те же `inputSchema` и `outputSchema`, что и канонический
реестр. В MCP используются underscore-имена из таблицы выше; `title` сохраняет канонический
`video.*` ID. Сама наличие сервера не включает внешнее MCP execution и не аттестует адаптер конкретного host.

## Troubleshooting

- **`Tool input did not match the declared schema`.** Сверьте JSON с `input_schema` из
  `uv run raytsystem tool list --json`; лишние поля запрещены.
- **`A required allowlisted media dependency is unavailable`.** Самой установки бинарника недостаточно.
  Доверенный launcher должен передать полный `ExecutablePin`, а host — root-confined/network-denied executor.
  Одни значения из `PATH` не используются.
- **URL отклонён даже с approval.** Для штатного CLI/MCP это ожидаемо: в них нет
  `DestinationBoundDownloadExecutor`. Не ослабляйте проверку в обход.
- **`partial` и `host_visual_analysis_required`.** Кадры и OCR созданы, но host ещё не внёс
  визуальные наблюдения. Это ограничение текущего фундамента, а не успешный visual analysis.
- **Транскрипт `unavailable`.** Tool Hub ищет готовый текст или sidecar; ASR/speech-to-text adapter не
  входит в текущую сборку.
- **Cached artifact не прошёл provenance.** Не подменяйте файл в stage и не перезаписывайте manifest:
  Tool Hub намеренно отказывается повторно использовать изменённый результат.
- **MCP не отвечает на `tools/list`.** Сначала отправьте `initialize`, затем notification
  `notifications/initialized`; до этого сервер вернёт `Server is not initialized`.

## Ограничения и статус готовности

- Штатные CLI/MCP могут обработать supplied/local transcript без внешнего CLI. Любая стадия
  с `ffprobe`, `ffmpeg`, `yt-dlp` или `tesseract` fail closed без аттестованного host executor; удалённый URL
  также требует точный approval.
- Нет ASR/speech-to-text adapter; без supplied/sidecar транскрипта конвейер честно вернёт `partial`.
- `video.inspect_frames` готовит evidence для host, но не делает самостоятельный visual inference.
- MCP-транспорт минимальный и newline-delimited; его совместимость с конкретными host-адаптерами
  не аттестована.
- Staging привязан к исходному real path/inode и проверяет symlink перед записью. Модель опирается на
  инвариант raytsystem «один writer на partition»; защита от враждебной одновременной замены между
  `lstat` и `open` потребует platform-specific handle-based sandbox (`openat/O_NOFOLLOW` или
  Windows no-reparse handles).
- Pinning спроектирован для `sys.platform`/`machine`, но отдельная Windows regression-матрица ещё не
  выполнена; это входит в будущую аттестацию host adapters.
- Project-local Claude Code/Codex `/watch` не входит в эту поставку. Compatibility report и оба end-to-end
  запуска ещё не прошли; статус — **not qualified**.

## Связанные страницы

- [Агенты и расширение](/agents/overview)
- [Создание расширений](/agents/creating-extensions)
- [Что отключено по умолчанию](/security/defaults)
- [Approvals](/security/approvals)
- [Справочник CLI](/reference/cli)

## Источники истины

- `src/raytsystem/toolhub/contracts.py`
- `src/raytsystem/toolhub/registry.py`
- `src/raytsystem/toolhub/video.py`
- `src/raytsystem/toolhub/runner.py`
- `src/raytsystem/toolhub/dispatch.py`
- `src/raytsystem/toolhub/mcp_server.py`
- `src/raytsystem/toolhub/cli.py`
