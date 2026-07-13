# Sources and modes

## Source normalization

| Source | Canonical kind | Acquisition | Visual track |
|---|---|---|---|
| YouTube URL | `url` | destination-approved `video.download` | probe result |
| Loom URL | `url` | public share only, destination-approved | probe result |
| Zoom share URL | `url` | public recording only, destination-approved | probe result |
| Direct HTTP(S) media | `url` | validated redirect chain, destination-approved | probe result |
| Local video | `local_file` | approved local regular file | yes when probed |
| Local audio | `local_file` | approved local regular file | no |
| Supplied transcript | `transcript` | inline text or approved local text file | no |

The typed source kind describes transport (`url`, `local_file`, `transcript`); provider/media
subtypes are detected during probe and never encoded as invented enum values.

Do not accept data URLs, pipes, devices, sockets, symlinks escaping the approved root, credentialed
URLs, browser cookie stores, or unsupported schemes. Normalize URLs for identity by dropping
fragments and userinfo and redacting sensitive query values; never use a raw secret-bearing URL as
a display value.

## Mode mapping

| Flag | Goal | Required stages |
|---|---|---|
| `--summary` | concise combined account | probe, transcript, frames/OCR/inspection when visual, timeline |
| `--timeline` | chronological evidence | probe, transcript, frames/OCR/inspection when visual, timeline |
| `--automation` | reproducible manual-process brief | probe, transcript, denser bounded frames, OCR, inspection, timeline |
| `--frames` | visual evidence index | probe, frames, OCR, inspection; transcript optional |
| `--transcript` | speech text only | probe when media, audio/transcript; no implied visual analysis |

Default mode is `summary`. Treat a natural-language request for "steps" from a screen recording as
`automation`; treat "what happened when" as `timeline`; treat "extract the text" as `transcript`
unless the user also asks what was shown.

## Sampling budgets

- Default source limit: 2 GiB and four hours; lower policy limits win.
- Default frame ceilings: 96 (`summary`), 144 (`timeline`), 180 (`automation` or `frames`).
- Always include the first and last decodable frame, detected scene changes, and sparse coverage
  points where the scene detector leaves long gaps. Deduplicate near-identical frames.
- Do not claim frame completeness. Report the sample count, selection strategy, rejected frames,
  and uncovered intervals.

For a transcript with no reliable timestamps, produce ordered sections and set temporal precision
to `none`. Never invent timecodes.
