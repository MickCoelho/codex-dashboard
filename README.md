# Codex Dashboard

A local dashboard for tracking [Codex](https://openai.com/codex) token usage and credit spend across sessions.

## How it works

The Python script (`scripts/codex-dashboard.py`) reads session data directly from Codex's local SQLite database (`~/.codex/state_5.sqlite`) and each session's rollout JSONL file. It computes token counts, cache hit rates, credit costs (per model), and per-turn breakdowns, then writes two files:

- `index.html` — the dashboard UI (static, no framework)
- `data.json` — the raw session data consumed by the UI at runtime

The UI fetches `data.json` on load and on every manual refresh. Nothing is baked into the HTML.

## Usage

```bash
yarn dev
```

This runs the Python script to generate fresh data, then starts a local server at `http://localhost:3000`.

Other scripts:

| Command         | Description                              |
|-----------------|------------------------------------------|
| `yarn generate` | Regenerate `index.html` and `data.json`  |
| `yarn serve`    | Start the server without regenerating    |

> **Note:** the UI fetches `data.json` over HTTP, so it requires the dev server. Opening `index.html` directly via `file://` will not work.

## Credit estimates

Credits are estimated using OpenAI's published per-model rates (input / cached input / output tokens per 1M). Fast mode usage cannot be detected from local session data, so actual billing may be higher if Fast mode was used.

Rates used (cr / 1M tokens):

| Model               | Input  | Cached | Output |
|---------------------|--------|--------|--------|
| gpt-5.5             | 125    | 12.5   | 750    |
| gpt-5.4             | 62.5   | 6.25   | 375    |
| gpt-5.4-mini        | 18.75  | 1.875  | 113    |
| gpt-5.3-codex       | 43.75  | 4.375  | 350    |
| gpt-4.1             | 2      | 0.5    | 8      |
| gpt-4o              | 2.5    | 1.25   | 10     |
