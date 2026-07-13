# GitHub screenshots

These images are captured from the real raytsystem React interface in headless Chromium. The browser
uses the deterministic, synthetic API fixture in `web/src/test/mockApi.ts`; it never opens private
corpus data, contacts an external service, or writes canonical knowledge.

## Reproduce

```bash
npm --prefix web ci
npm --prefix web run browser:install
npm --prefix web run screenshots:github
```

The screenshot command starts a loopback-only Vitest browser server, waits for route data, fonts,
and two animation frames, and fails if a route, fixture, browser, or output write fails.

| File | Route/state | Viewport | Theme |
|---|---|---:|---|
| `hero.png` | `/command-center` | 1440×900 | dark |
| `command-center.png` | `/command-center` | 1440×900 | dark |
| `documents.png` | `/documents`, `Layout note` open | 1440×900 | dark |
| `universe.png` | `/universe` | 1440×900 | dark |
| `agents.png` | `/agents` | 1440×900 | dark |
| `skills.png` | `/skills` | 1440×900 | dark |
| `tasks.png` | `/tasks` | 1440×900 | dark |
| `safety.png` | `/safety` | 1440×900 | dark |
| `social-preview.png` | `/command-center` | 1280×640 | dark |

The checked-in images are regenerated from the current reviewed interface fixtures.
