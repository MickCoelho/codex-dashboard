#!/usr/bin/env python3
"""
Codex Token Usage Dashboard Generator
Reads session data from ~/.codex and generates a self-contained HTML dashboard.

Usage:
    python3 scripts/codex-dashboard.py          # regenerate with all data
    python3 scripts/codex-dashboard.py --out report.html
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

CODEX_DIR = Path.home() / ".codex"
STATE_DB  = CODEX_DIR / "state_5.sqlite"

MODEL_RATES = {
    "gpt-5.5":             {"in": 125.0,  "cached": 12.5,   "out": 750.0},
    "gpt-5.4":             {"in": 62.5,   "cached": 6.25,   "out": 375.0},
    "gpt-5.4-mini":        {"in": 18.75,  "cached": 1.875,  "out": 113.0},
    "gpt-5.3-codex":       {"in": 43.75,  "cached": 4.375,  "out": 350.0},
    "gpt-5.3-codex-spark": {"in": 0.0,    "cached": 0.0,    "out": 0.0},   # research preview
    "gpt-5.2":             {"in": 43.75,  "cached": 4.375,  "out": 350.0},
    "gpt-4.1":             {"in": 2.0,    "cached": 0.5,    "out": 8.0},
    "gpt-4o":              {"in": 2.5,    "cached": 1.25,   "out": 10.0},
}
DEFAULT_RATE = {"in": 125.0, "cached": 12.5, "out": 750.0}


# ── Data extraction ──────────────────────────────────────────────────────────

def get_sessions() -> list[dict]:
    if not STATE_DB.exists():
        print(f"ERROR: {STATE_DB} not found", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(STATE_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, created_at, model, cwd, first_user_message,
               tokens_used, rollout_path, model_provider
        FROM threads
        WHERE archived = 0
          AND first_user_message IS NOT NULL
          AND LENGTH(TRIM(COALESCE(first_user_message, ''))) > 0
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id":           r[0],
            "created_at":   r[1],
            "model":        r[2] or "unknown",
            "cwd":          r[3] or "",
            "first_prompt": r[4] or "",
            "tokens_used":  r[5] or 0,
            "rollout_path": r[6],
        }
        for r in rows
    ]


def parse_rollout(path: str) -> dict:
    result = {
        "input": 0, "cached": 0, "output": 0, "reasoning": 0,
        "in_window": 0, "tool_freq": {}, "per_turn": [],
    }
    if not path or not Path(path).exists():
        return result

    turns = []
    current_turn = None
    prev_cumulative = 0

    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except Exception:
        return result

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue

        t = obj.get("type", "")

        if t == "event_msg":
            payload = obj.get("payload", {})
            ptype = payload.get("type", "")

            if ptype == "task_started":
                current_turn = {
                    "turn_id": payload.get("turn_id"),
                    "mode":    payload.get("collaboration_mode_kind", "default"),
                    "tools":   0,
                    "cum_tokens": 0,
                }

            elif ptype == "task_complete" and current_turn:
                delta = current_turn["cum_tokens"] - prev_cumulative
                turns.append({
                    "t":     len(turns) + 1,
                    "cum":   current_turn["cum_tokens"],
                    "delta": delta,
                    "tools": current_turn["tools"],
                    "mode":  current_turn["mode"],
                })
                prev_cumulative = current_turn["cum_tokens"]
                current_turn = None

            elif ptype == "token_count":
                info  = payload.get("info") or {}
                usage = info.get("total_token_usage") or {}
                if usage:
                    result["input"]     = usage.get("input_tokens", 0)
                    result["cached"]    = usage.get("cached_input_tokens", 0)
                    result["output"]    = usage.get("output_tokens", 0)
                    result["reasoning"] = usage.get("reasoning_output_tokens", 0)
                    total = usage.get("total_tokens", 0)
                    result["in_window"] = max(result["in_window"], total)
                    if current_turn:
                        current_turn["cum_tokens"] = total

        elif t in ("function_call", "custom_tool_call"):
            name = (obj.get("name")
                    or obj.get("payload", {}).get("name", "unknown"))
            result["tool_freq"][name] = result["tool_freq"].get(name, 0) + 1
            if current_turn:
                current_turn["tools"] += 1

    result["per_turn"] = turns
    return result


def compute_credits(model: str, input_tok: int, cached_tok: int, output_tok: int) -> float:
    rates   = MODEL_RATES.get(model, DEFAULT_RATE)
    uncached = max(0, input_tok - cached_tok)
    return round(
        (uncached    / 1_000_000) * rates["in"] +
        (cached_tok  / 1_000_000) * rates["cached"] +
        (output_tok  / 1_000_000) * rates["out"],
        2
    )


# ── HTML template ────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Codex Token Usage</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@300;400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #07070d;
      --surface: #0d0d1a;
      --surface-2: #111120;
      --surface-hover: #14142a;
      --border: #1a1a32;
      --border-bright: #272745;
      --text: #dde1f5;
      --text-dim: #7a7d9c;
      --text-muted: #3d3f62;
      --amber: #f5a623;
      --amber-dim: rgba(245,166,35,0.12);
      --cyan: #22d3ee;
      --green: #34d399;
      --red: #f87171;
    }
    * { margin:0; padding:0; box-sizing:border-box; }
    html { scroll-behavior:smooth; }
    body { background:var(--bg); color:var(--text); font-family:'JetBrains Mono',monospace; font-size:12px; line-height:1.5; min-height:100vh; }

    /* ── header ── */
    .site-header { padding:24px 40px 16px; border-bottom:1px solid var(--border); background:var(--bg); position:sticky; top:0; z-index:50; }
    .header-row-1 { display:flex; align-items:baseline; justify-content:space-between; margin-bottom:6px; gap:16px; flex-wrap:wrap; }
    h1 { font-family:'Syne',sans-serif; font-size:20px; font-weight:800; letter-spacing:-0.03em; color:var(--text); }
    h1 em { font-style:normal; color:var(--amber); }
    .header-right { display:flex; align-items:center; gap:12px; }
    .generated-at { font-size:10px; color:var(--text-muted); }

    /* refresh button */
    .btn-refresh {
      display:flex; align-items:center; gap:6px;
      padding:5px 12px; border-radius:5px;
      background:var(--surface-2); border:1px solid var(--border-bright);
      color:var(--text-dim); font-family:'JetBrains Mono',monospace; font-size:10.5px;
      cursor:pointer; transition:all .15s; white-space:nowrap;
    }
    .btn-refresh:hover { border-color:var(--amber-border,rgba(245,166,35,.25)); color:var(--amber); }
    .btn-refresh .spin { display:inline-block; transition:transform .3s; }
    .btn-refresh.loading .spin { animation:spin .6s linear infinite; }
    @keyframes spin { to { transform:rotate(360deg); } }

    /* refresh toast */
    .refresh-toast {
      display:none; position:fixed; bottom:24px; right:24px; z-index:999;
      background:var(--surface-2); border:1px solid var(--border-bright);
      border-radius:8px; padding:14px 18px; max-width:360px;
      box-shadow:0 12px 40px rgba(0,0,0,.6); animation:toastIn .2s ease;
    }
    .refresh-toast.show { display:block; }
    @keyframes toastIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
    .toast-title { font-family:'Syne',sans-serif; font-size:11px; font-weight:700; color:var(--text); margin-bottom:8px; }
    .toast-cmd {
      display:flex; align-items:center; gap:8px;
      background:var(--bg); border:1px solid var(--border); border-radius:4px;
      padding:6px 10px; font-size:10.5px; color:var(--cyan);
    }
    .toast-copy { margin-left:auto; cursor:pointer; color:var(--text-muted); transition:color .15s; flex-shrink:0; }
    .toast-copy:hover { color:var(--amber); }
    .toast-close { position:absolute; top:10px; right:12px; cursor:pointer; color:var(--text-muted); font-size:14px; }

    /* filter pills */
    .filter-row { display:flex; align-items:center; gap:8px; margin-bottom:14px; }
    .filter-lbl { font-size:9.5px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.1em; margin-right:4px; }
    .pill {
      padding:4px 11px; border-radius:20px; font-family:'JetBrains Mono',monospace; font-size:10.5px;
      background:transparent; border:1px solid var(--border-bright); color:var(--text-dim);
      cursor:pointer; transition:all .15s;
    }
    .pill:hover { border-color:rgba(245,166,35,.3); color:var(--text); }
    .pill.active { background:var(--amber-dim); border-color:var(--amber); color:var(--amber); font-weight:500; }

    /* stats bar */
    .stats-bar { display:flex; gap:0; }
    .stat-chip { display:flex; flex-direction:column; padding:6px 20px 6px 0; margin-right:20px; border-right:1px solid var(--border); gap:2px; }
    .stat-chip:last-child { border-right:none; }
    .stat-num { font-family:'Syne',sans-serif; font-size:17px; font-weight:700; color:var(--text); letter-spacing:-0.02em; transition:color .2s; }
    .stat-num.c-cyan { color:var(--cyan); }
    .stat-num.c-amber { color:var(--amber); }
    .stat-num.c-green { color:var(--green); }
    .stat-lbl { font-size:9.5px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.1em; }

    /* table */
    .table-wrapper { overflow:auto; height:calc(100vh - var(--header-h, 160px)); }
    table { width:100%; border-collapse:collapse; }
    thead th { padding:9px 12px; font-family:'Syne',sans-serif; font-size:9.5px; font-weight:600; letter-spacing:.1em; text-transform:uppercase; color:var(--text-muted); text-align:left; border-bottom:1px solid var(--border-bright); background:var(--bg); white-space:nowrap; cursor:pointer; user-select:none; transition:color .15s; position:sticky; top:0; z-index:10; }
    thead th:hover { color:var(--text-dim); }
    thead th.sorted { color:var(--amber); }
    thead th.sorted::after { content:' ↓'; font-size:8px; }
    thead th.sorted.asc::after { content:' ↑'; }
    thead th.nosort { cursor:default; }
    thead th.nosort:hover { color:var(--text-muted); }

    tr.s-row { border-bottom:1px solid var(--border); cursor:pointer; transition:background .12s; }
    tr.s-row:hover { background:var(--surface-hover); }
    tr.s-row.open { background:var(--surface); }
    tr.s-row td { padding:9px 12px; vertical-align:middle; white-space:nowrap; }
    tr.d-row td { padding:0; background:var(--surface); border-bottom:1px solid var(--border-bright); }

    .detail-inner { overflow:hidden; max-height:0; transition:max-height .35s cubic-bezier(0.16,1,0.3,1); }
    .detail-inner.open { max-height:900px; }
    .detail-body { padding:20px 28px 24px; border-top:1px solid var(--border-bright); }

    .c-num { color:var(--text-muted); font-size:10px; width:30px; text-align:center; }
    .expand-caret { display:inline-block; margin-right:4px; color:var(--text-muted); transition:transform .2s,color .2s; font-size:9px; }
    tr.s-row.open .expand-caret { transform:rotate(90deg); color:var(--amber); }
    .c-started { color:var(--text-dim); font-size:11px; }

    .token-cell { min-width:160px; }
    .token-val { font-size:11.5px; font-weight:500; color:var(--cyan); margin-bottom:5px; }
    .t-bar-track { height:2px; background:rgba(34,211,238,0.08); border-radius:2px; width:130px; }
    .t-bar-fill { height:2px; background:linear-gradient(90deg,var(--cyan) 0%,rgba(34,211,238,.25) 100%); border-radius:2px; width:0; transition:width .9s cubic-bezier(0.16,1,0.3,1); }

    .c-credits { color:var(--amber); font-weight:500; position:relative; }
    .credit-tooltip { display:none; position:absolute; bottom:calc(100% + 6px); left:50%; transform:translateX(-50%); background:var(--surface-2); border:1px solid var(--border-bright); border-radius:5px; padding:7px 11px; font-size:10px; color:var(--text-dim); white-space:nowrap; z-index:100; pointer-events:none; box-shadow:0 8px 24px rgba(0,0,0,.5); }
    .credit-tooltip .rate-row { display:flex; gap:10px; justify-content:space-between; margin-bottom:2px; }
    .credit-tooltip .rate-key { color:var(--text-muted); }
    .credit-tooltip .rate-val { color:var(--amber); font-weight:500; }
    .c-credits:hover .credit-tooltip { display:block; }

    /* breakdown row */
    .breakdown-row { display:grid; grid-template-columns:auto 1fr; gap:24px; margin-top:14px; padding-top:14px; border-top:1px solid var(--border); align-items:start; }
    .burn-cards { display:flex; gap:0; }
    .burn-card { display:flex; flex-direction:column; gap:2px; padding-right:20px; margin-right:20px; border-right:1px solid var(--border); }
    .burn-card:last-child { border-right:none; }
    .burn-val { font-family:'Syne',sans-serif; font-size:17px; font-weight:700; letter-spacing:-0.02em; color:var(--amber); }
    .burn-lbl { font-size:9.5px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.1em; }
    .burn-proj { font-size:10px; color:var(--text-muted); margin-top:1px; }
    .burn-proj span { color:var(--text-dim); }
    .model-breakdown { display:flex; flex-direction:column; gap:6px; }
    .model-row { display:flex; align-items:center; gap:8px; }
    .model-row-name { width:130px; font-size:10.5px; color:var(--text-dim); flex-shrink:0; overflow:hidden; text-overflow:ellipsis; }
    .model-row-track { flex:1; height:4px; background:var(--amber-dim); border-radius:2px; overflow:hidden; min-width:60px; }
    .model-row-fill { height:4px; background:linear-gradient(90deg,var(--amber),rgba(245,166,35,.25)); border-radius:2px; transition:width .6s cubic-bezier(0.16,1,0.3,1); }
    .model-row-credits { width:72px; text-align:right; font-size:10.5px; color:var(--amber); flex-shrink:0; }
    .model-row-pct { width:34px; text-align:right; font-size:10px; color:var(--text-muted); flex-shrink:0; }
    .model-row-sessions { width:28px; text-align:right; font-size:10px; color:var(--text-muted); flex-shrink:0; }

    .c-input,.c-output,.c-reasoning { color:var(--text-dim); }
    .cache-val { font-weight:500; }
    .cache-hi { color:var(--green); }
    .cache-mid { color:var(--amber); }
    .cache-lo { color:var(--red); }
    .session-id-short { font-size:10.5px; color:var(--text-muted); letter-spacing:.03em; }
    .model-badge { display:inline-block; padding:2px 6px; background:var(--surface-2); border:1px solid var(--border-bright); border-radius:3px; font-size:10px; color:var(--text-dim); }
    .c-cwd { max-width:180px; overflow:hidden; text-overflow:ellipsis; color:var(--text-muted); font-size:10.5px; }
    .c-prompt { max-width:260px; overflow:hidden; text-overflow:ellipsis; color:var(--text-dim); font-size:11px; }

    /* empty state */
    .empty-state { text-align:center; padding:60px 0; color:var(--text-muted); font-size:11px; }
    .empty-state .empty-title { font-family:'Syne',sans-serif; font-size:14px; font-weight:700; color:var(--text-dim); margin-bottom:8px; }

    /* detail panel */
    .detail-meta-row { display:flex; gap:20px; margin-bottom:14px; flex-wrap:wrap; }
    .dmeta { display:flex; flex-direction:column; gap:2px; }
    .dmeta-lbl { font-size:9px; text-transform:uppercase; letter-spacing:.1em; color:var(--text-muted); }
    .dmeta-val { font-size:12px; font-weight:500; color:var(--text); }
    .dmeta-val.amber { color:var(--amber); }
    .dmeta-val.cyan { color:var(--cyan); }
    .dmeta-val.green { color:var(--green); }
    .d-section-title { font-family:'Syne',sans-serif; font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:.15em; color:var(--text-muted); margin-bottom:10px; padding-bottom:6px; border-bottom:1px solid var(--border); }
    .detail-grid { display:grid; grid-template-columns:1fr 1fr 240px; gap:24px; }

    .turns-tbl { width:100%; border-collapse:collapse; font-size:10.5px; }
    .turns-tbl th { padding:4px 6px; font-size:9px; text-transform:uppercase; letter-spacing:.08em; color:var(--text-muted); border-bottom:1px solid var(--border); text-align:right; }
    .turns-tbl th:first-child { text-align:center; }
    .turns-tbl td { padding:3px 6px; text-align:right; color:var(--text-dim); border-bottom:1px solid rgba(26,26,50,.6); }
    .turns-tbl td:first-child { text-align:center; color:var(--text-muted); }
    .mode-plan { color:var(--amber); }
    .mode-default { color:var(--cyan); }
    .delta-pos { color:var(--green); }
    .delta-neg { color:var(--red); }
    .delta-zero { color:var(--text-muted); }

    .tool-list { display:flex; flex-direction:column; gap:7px; }
    .tool-item { display:flex; align-items:center; gap:8px; }
    .tool-name { width:110px; font-size:10.5px; color:var(--text-dim); flex-shrink:0; overflow:hidden; text-overflow:ellipsis; }
    .tool-track { flex:1; height:3px; background:var(--amber-dim); border-radius:2px; overflow:hidden; }
    .tool-fill { height:3px; background:linear-gradient(90deg,var(--amber) 0%,rgba(245,166,35,.3) 100%); border-radius:2px; }
    .tool-count { font-size:10.5px; color:var(--text-muted); width:34px; text-align:right; }

    .site-footer { padding:14px 40px; border-top:1px solid var(--border); font-size:10px; color:var(--text-muted); text-align:center; }

    @keyframes rowIn { from{opacity:0;transform:translateX(-6px)} to{opacity:1;transform:translateX(0)} }
    tr.s-row { animation:rowIn .4s ease backwards; }
    ::-webkit-scrollbar { width:6px; height:6px; }
    ::-webkit-scrollbar-track { background:var(--bg); }
    ::-webkit-scrollbar-thumb { background:var(--border-bright); border-radius:3px; }
    ::-webkit-scrollbar-thumb:hover { background:var(--text-muted); }
  </style>
</head>
<body>

<header class="site-header">
  <div class="header-row-1">
    <h1>Codex Token Usage <em id="title-range">— all time</em></h1>
    <div class="header-right">
      <span class="generated-at">Generated <span id="gen-at">{{GENERATED_AT}}</span></span>
      <button class="btn-refresh" id="btn-refresh">
        <span class="spin">↻</span> Refresh
      </button>
    </div>
  </div>

  <div class="filter-row">
    <span class="filter-lbl">Show</span>
    <button class="pill" data-hours="24">24h</button>
    <button class="pill" data-hours="-1">WTD</button>
    <button class="pill" data-hours="168">7d</button>
    <button class="pill" data-hours="720">1m</button>
    <button class="pill active" data-hours="0">All</button>
  </div>

  <div class="stats-bar">
    <div class="stat-chip"><span class="stat-num" id="stat-sessions">0</span><span class="stat-lbl">Sessions</span></div>
    <div class="stat-chip"><span class="stat-num c-cyan" id="stat-tokens">0</span><span class="stat-lbl">Tokens</span></div>
    <div class="stat-chip"><span class="stat-num c-amber" id="stat-credits">0</span><span class="stat-lbl">Credits</span></div>
    <div class="stat-chip"><span class="stat-num c-green" id="stat-cache">—</span><span class="stat-lbl">Avg Cache</span></div>
    <div class="stat-chip"><span class="stat-num" id="stat-tools">0</span><span class="stat-lbl">Tool Calls</span></div>
  </div>

  <div class="breakdown-row">
    <div class="burn-cards">
      <div class="burn-card">
        <span class="burn-val" id="burn-day">—</span>
        <span class="burn-lbl">cr / day</span>
        <span class="burn-proj" id="burn-proj"></span>
      </div>
    </div>
    <div class="model-breakdown" id="model-breakdown"></div>
  </div>
</header>

<div class="table-wrapper">
  <table id="main-table">
    <thead>
      <tr>
        <th class="nosort"></th>
        <th data-col="num">#</th>
        <th data-col="started">Started</th>
        <th data-col="tokens">In-window tokens</th>
        <th data-col="credits">~Credits</th>
        <th data-col="input">Input</th>
        <th data-col="cache">Cache%</th>
        <th data-col="output">Output</th>
        <th data-col="reasoning">Reasoning</th>
        <th data-col="tools">Tools</th>
        <th class="nosort">Session ID</th>
        <th class="nosort">Model</th>
        <th class="nosort">CWD</th>
        <th class="nosort">First Prompt</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<footer class="site-footer">
  <div>gpt-5.5: 125/12.5/750 &nbsp;·&nbsp; gpt-5.4: 62.5/6.25/375 &nbsp;·&nbsp; gpt-5.4-mini: 18.75/1.875/113 &nbsp;·&nbsp; gpt-5.3-codex: 43.75/4.375/350 &nbsp;·&nbsp; gpt-5.2: 43.75/4.375/350 &nbsp;&nbsp;(cr/1M in/cached/out)</div>
  <div style="margin-top:5px;color:#2a2a48;">⚠ Credits shown are a floor estimate. Fast mode ("About 1.5x faster, with increased plan usage") is not recorded in local session data and cannot be detected — actual billing may be higher if Fast mode was used.</div>
</footer>

<!-- refresh toast -->
<div class="refresh-toast" id="refresh-toast">
  <span class="toast-close" id="toast-close">✕</span>
  <div class="toast-title">Re-run the script to refresh</div>
  <div class="toast-cmd">
    <span id="toast-cmd-text">python3 scripts/codex-dashboard.py</span>
    <span class="toast-copy" id="toast-copy" title="Copy">⎘</span>
  </div>
</div>

<script>
let ALL_SESSIONS = [];
let activeHours  = 0;   // 0 = all
let sortCol      = 'num';
let sortAsc      = true;
const openSet    = new Set();

// ── helpers ──────────────────────────────────────────────────────────────
function fmt(n)  { return n == null ? '—' : n.toLocaleString('en-US'); }
function fmtC(n) { return Number.isInteger(n) ? fmt(n)+' cr' : n.toFixed(1)+' cr'; }
function fmtShort(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2)+'B';
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return String(n);
}
function fmtDelta(d) {
  if (d === 0) return '0';
  const s = d > 0 ? '+' : '', a = Math.abs(d);
  if (a >= 1e6) return s+(a/1e6).toFixed(1)+'M';
  if (a >= 1e3) return s+(a/1e3).toFixed(1)+'K';
  return s+String(d);
}
function cacheClass(p) { return p >= 90 ? 'cache-hi' : p >= 70 ? 'cache-mid' : 'cache-lo'; }
function deltaClass(d) { return d > 0 ? 'delta-pos' : d < 0 ? 'delta-neg' : 'delta-zero'; }

// ── filter ───────────────────────────────────────────────────────────────
function weekStart() {
  const d = new Date();
  const day = d.getDay(); // 0=Sun
  d.setDate(d.getDate() - (day === 0 ? 6 : day - 1)); // back to Monday
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function getFiltered() {
  if (!activeHours) return ALL_SESSIONS;
  const cutoff = activeHours === -1
    ? weekStart()
    : Date.now() - activeHours * 3_600_000;
  return ALL_SESSIONS.filter(s => s.createdAtMs >= cutoff);
}

// ── stats bar ────────────────────────────────────────────────────────────
function updateStats(sessions) {
  const total   = sessions.reduce((a,s) => a + s.inWindow, 0);
  const credits = sessions.reduce((a,s) => a + s.credits, 0);
  const tools   = sessions.reduce((a,s) => a + s.tools, 0);
  const caches  = sessions.filter(s => s.input > 0).map(s => s.cache);
  const avgCache = caches.length ? Math.round(caches.reduce((a,b)=>a+b,0)/caches.length) : null;

  document.getElementById('stat-sessions').textContent = sessions.length.toLocaleString();
  document.getElementById('stat-tokens').textContent   = fmtShort(total);
  document.getElementById('stat-credits').textContent  = '~'+credits.toFixed(1);
  document.getElementById('stat-cache').textContent    = avgCache != null ? avgCache+'%' : '—';
  document.getElementById('stat-tools').textContent    = tools.toLocaleString();

  const titleMap = { 0:'all time', '-1':'week to date', 24:'last 24h', 168:'last 7d', 720:'last 1m' };
  document.getElementById('title-range').textContent = '— ' + (titleMap[String(activeHours)] || 'filtered');
}

// ── breakdown (burn rate + model split) ──────────────────────────────────
function renderBreakdown(sessions) {
  const totalCredits = sessions.reduce((a,s) => a + s.credits, 0);

  // Burn rate: use filter window or actual date span for "all"
  let days;
  if (activeHours === -1) {
    days = Math.max(1, (Date.now() - weekStart()) / 86_400_000);
  } else if (activeHours > 0) {
    days = activeHours / 24;
  } else if (sessions.length > 1) {
    const oldest = Math.min(...sessions.map(s => s.createdAtMs));
    const newest = Math.max(...sessions.map(s => s.createdAtMs));
    days = Math.max(1, (newest - oldest) / 86_400_000);
  } else {
    days = 1;
  }
  const perDay     = totalCredits / days;
  const projMonth  = perDay * 30;
  const projWeek   = perDay * 7;

  document.getElementById('burn-day').textContent  = perDay >= 1 ? perDay.toFixed(1)+' cr' : '<1 cr';
  document.getElementById('burn-proj').innerHTML   =
    `<span>~${projWeek.toFixed(0)} cr/wk</span> &nbsp;·&nbsp; <span>~${projMonth.toFixed(0)} cr/mo</span>`;

  // Per-model breakdown
  const byModel = {};
  sessions.forEach(s => {
    if (!byModel[s.model]) byModel[s.model] = { credits:0, count:0 };
    byModel[s.model].credits += s.credits;
    byModel[s.model].count   += 1;
  });
  const models     = Object.entries(byModel).sort((a,b) => b[1].credits - a[1].credits);
  const maxCredits = models.length ? models[0][1].credits : 1;

  document.getElementById('model-breakdown').innerHTML = models.map(([name, d]) => {
    const pct    = totalCredits > 0 ? Math.round(d.credits / totalCredits * 100) : 0;
    const barPct = Math.round(d.credits / maxCredits * 100);
    return `<div class="model-row">
      <span class="model-row-name">${name}</span>
      <div class="model-row-track"><div class="model-row-fill" style="width:${barPct}%"></div></div>
      <span class="model-row-credits">${d.credits < 1 ? d.credits.toFixed(2) : d.credits.toFixed(1)} cr</span>
      <span class="model-row-pct">${pct}%</span>
      <span class="model-row-sessions">${d.count}s</span>
    </div>`;
  }).join('');

  syncTableHeight();
}

// ── detail panel ─────────────────────────────────────────────────────────
function buildDetail(s) {
  const maxTool = s.toolFreq.length ? Math.max(...s.toolFreq.map(t => t.count)) : 1;
  const turnRows = s.perTurn.slice(0,25).map(t =>
    `<tr><td>${t.t}</td><td>${fmtShort(t.cum)}</td><td class="${deltaClass(t.delta)}">${fmtDelta(t.delta)}</td><td>${t.tools||'—'}</td><td class="mode-${t.mode}">${t.mode}</td></tr>`
  ).join('');
  const overflow = s.perTurn.length > 25
    ? `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);font-size:9.5px;font-style:italic;padding:6px">+${s.perTurn.length-25} more turns…</td></tr>` : '';
  const toolRows = s.toolFreq.map(t =>
    `<div class="tool-item"><span class="tool-name">${t.name}</span><div class="tool-track"><div class="tool-fill" style="width:${Math.round(t.count/maxTool*100)}%"></div></div><span class="tool-count">${t.count}</span></div>`
  ).join('') || '<span style="color:var(--text-muted);font-size:10.5px">No tool calls recorded</span>';

  return `<div class="detail-body">
    <div class="detail-meta-row">
      <div class="dmeta"><span class="dmeta-lbl">Full Session ID</span><span class="dmeta-val" style="font-size:10.5px;color:var(--text-muted)">${s.fullId}</span></div>
      <div class="dmeta"><span class="dmeta-lbl">Started</span><span class="dmeta-val" style="font-size:11px">${s.startedTs}</span></div>
      <div class="dmeta"><span class="dmeta-lbl">Model</span><span class="dmeta-val">${s.model}</span></div>
      <div class="dmeta"><span class="dmeta-lbl">Turns</span><span class="dmeta-val cyan">${s.perTurn.length}</span></div>
      <div class="dmeta"><span class="dmeta-lbl">In-window</span><span class="dmeta-val cyan">${fmt(s.inWindow)}</span></div>
      <div class="dmeta"><span class="dmeta-lbl">Cached</span><span class="dmeta-val green">${fmt(s.cachedTok)} (${s.cache}%)</span></div>
      <div class="dmeta"><span class="dmeta-lbl">Credits</span><span class="dmeta-val amber">${fmtC(s.credits)}</span></div>
    </div>
    <div class="detail-grid">
      <div>
        <div class="d-section-title">Per-Turn Token Growth</div>
        <table class="turns-tbl"><thead><tr><th>#</th><th>Cumulative</th><th>Delta</th><th style="text-align:left">Tools</th><th style="text-align:left">Mode</th></tr></thead>
        <tbody>${turnRows}${overflow}</tbody></table>
      </div>
      <div></div>
      <div>
        <div class="d-section-title">Tool Call Frequency</div>
        <div class="tool-list">${toolRows}</div>
      </div>
    </div>
  </div>`;
}

// ── render ───────────────────────────────────────────────────────────────
function renderTable(sessions) {
  const maxTok = Math.max(...sessions.map(s => s.inWindow), 1);
  const tbody  = document.getElementById('tbody');
  tbody.innerHTML = '';

  if (!sessions.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="14"><div class="empty-state"><div class="empty-title">No sessions in this window</div>Try selecting a broader time range.</div></td>`;
    tbody.appendChild(tr);
    return;
  }

  const sorted = [...sessions].sort((a,b) => {
    const va = a[sortCol], vb = b[sortCol];
    if (typeof va === 'string') return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    return sortAsc ? va - vb : vb - va;
  });

  sorted.forEach((s, idx) => {
    const barPct = Math.max(1, (s.inWindow / maxTok) * 100);
    const cwd    = s.cwd.replace(/\/Users\/[^/]+/, '~');

    const tr = document.createElement('tr');
    tr.className = 's-row' + (openSet.has(s.fullId) ? ' open' : '');
    tr.style.animationDelay = `${idx * 0.02}s`;
    tr.dataset.id = s.fullId;
    tr.innerHTML = `
      <td class="c-num"><span class="expand-caret">▶</span></td>
      <td class="c-num">${idx+1}</td>
      <td class="c-started">${s.started}</td>
      <td class="token-cell">
        <div class="token-val">${fmtShort(s.inWindow)}</div>
        <div class="t-bar-track"><div class="t-bar-fill" data-pct="${barPct}" style="width:0"></div></div>
      </td>
      <td class="c-credits" style="position:relative">
        ${fmtC(s.credits)}
        <div class="credit-tooltip">
          <div class="rate-row"><span class="rate-key">in / 1M</span><span class="rate-val">${s.rateIn}</span></div>
          <div class="rate-row"><span class="rate-key">cached / 1M</span><span class="rate-val">${s.rateCached}</span></div>
          <div class="rate-row"><span class="rate-key">out / 1M</span><span class="rate-val">${s.rateOut}</span></div>
        </div>
      </td>
      <td class="c-input">${fmt(s.input)}</td>
      <td><span class="cache-val ${cacheClass(s.cache)}">${s.cache}%</span></td>
      <td class="c-output">${fmt(s.output)}</td>
      <td class="c-reasoning">${fmt(s.reasoning)}</td>
      <td style="color:var(--text-dim)">${fmt(s.tools)}</td>
      <td class="session-id-short">${s.sessionId}</td>
      <td><span class="model-badge">${s.model}</span></td>
      <td class="c-cwd" title="${s.cwd}">${cwd}</td>
      <td class="c-prompt" title="${s.prompt}">${s.prompt}</td>`;
    tr.addEventListener('click', () => toggleDetail(s.fullId));
    tbody.appendChild(tr);

    const dr = document.createElement('tr');
    dr.className = 'd-row';
    const td = document.createElement('td');
    td.colSpan = 14;
    const inner = document.createElement('div');
    inner.className = 'detail-inner' + (openSet.has(s.fullId) ? ' open' : '');
    inner.dataset.id = s.fullId;
    inner.innerHTML = buildDetail(s);
    td.appendChild(inner);
    dr.appendChild(td);
    tbody.appendChild(dr);
  });

  requestAnimationFrame(() => {
    document.querySelectorAll('.t-bar-fill[data-pct]').forEach(el => {
      el.style.width = el.dataset.pct + '%';
    });
  });
}

function toggleDetail(id) {
  const row   = document.querySelector(`tr.s-row[data-id="${id}"]`);
  const inner = document.querySelector(`.detail-inner[data-id="${id}"]`);
  if (!row || !inner) return;
  if (openSet.has(id)) {
    openSet.delete(id); row.classList.remove('open'); inner.classList.remove('open');
  } else {
    openSet.add(id); row.classList.add('open'); inner.classList.add('open');
  }
}

function applyFilter() {
  const filtered = getFiltered();
  updateStats(filtered);
  renderBreakdown(filtered);
  renderTable(filtered);
}

// ── sort ─────────────────────────────────────────────────────────────────
const COL_MAP = {
  num:'num', started:'started', tokens:'inWindow', credits:'credits',
  input:'input', cache:'cache', output:'output', reasoning:'reasoning', tools:'tools'
};
document.querySelectorAll('thead th[data-col]').forEach(th => {
  th.addEventListener('click', () => {
    const col = COL_MAP[th.dataset.col];
    if (!col) return;
    sortAsc = sortCol === col ? !sortAsc : (col === 'num');
    sortCol = col;
    document.querySelectorAll('thead th').forEach(t => t.classList.remove('sorted','asc'));
    th.classList.add('sorted');
    if (sortAsc) th.classList.add('asc');
    applyFilter();
  });
});

// ── filter pills ─────────────────────────────────────────────────────────
document.querySelectorAll('.pill').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeHours = parseInt(btn.dataset.hours, 10);
    applyFilter();
  });
});

// ── refresh ──────────────────────────────────────────────────────────────
const btnRefresh = document.getElementById('btn-refresh');
const toast      = document.getElementById('refresh-toast');

btnRefresh.addEventListener('click', async () => {
  btnRefresh.classList.add('loading');
  try {
    const res  = await fetch('./data.json?t=' + Date.now());
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    ALL_SESSIONS = data.sessions;
    document.getElementById('gen-at').textContent = data.generatedAt;
    applyFilter();
  } catch {
    toast.classList.add('show');
  } finally {
    btnRefresh.classList.remove('loading');
  }
});

document.getElementById('toast-close').addEventListener('click', () => toast.classList.remove('show'));
document.getElementById('toast-copy').addEventListener('click', () => {
  const cmd = document.getElementById('toast-cmd-text').textContent;
  navigator.clipboard.writeText(cmd).then(() => {
    const el = document.getElementById('toast-copy');
    el.textContent = '✓';
    setTimeout(() => { el.textContent = '⎘'; }, 1500);
  });
});

// ── init ─────────────────────────────────────────────────────────────────
function syncTableHeight() {
  const h = document.querySelector('.site-header').offsetHeight;
  document.documentElement.style.setProperty('--header-h', h + 'px');
}
syncTableHeight();
document.fonts.ready.then(syncTableHeight);
window.addEventListener('resize', syncTableHeight);

document.querySelector('thead th[data-col="num"]').classList.add('sorted','asc');

(async () => {
  try {
    const res  = await fetch('./data.json?t=' + Date.now());
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    ALL_SESSIONS = data.sessions;
    document.getElementById('gen-at').textContent = data.generatedAt;
  } catch {
    const tbody = document.getElementById('tbody');
    tbody.innerHTML = `<tr><td colspan="14"><div class="empty-state">
      <div class="empty-title">Could not load data.json</div>
      Serve this file via a local HTTP server, e.g.:<br><br>
      <code style="color:var(--cyan)">python3 -m http.server 8000</code>
    </div></td></tr>`;
  }
  applyFilter();
})();
</script>
</body>
</html>"""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="Output HTML file")
    args = parser.parse_args()

    import datetime

    print(f"Reading sessions from {STATE_DB} …")
    raw_sessions = get_sessions()
    print(f"Found {len(raw_sessions)} sessions, parsing rollout files…")

    sessions_out = []
    for i, s in enumerate(raw_sessions, 1):
        rollout  = parse_rollout(s["rollout_path"])
        in_tok   = rollout["input"]
        cach_tok = rollout["cached"]
        out_tok  = rollout["output"]
        reas_tok = rollout["reasoning"]
        in_window = rollout["in_window"] or s["tokens_used"]
        cache_pct = round(cach_tok / in_tok * 100) if in_tok > 0 else 0

        model   = s["model"]
        rates   = MODEL_RATES.get(model, DEFAULT_RATE)
        credits = compute_credits(model, in_tok, cach_tok, out_tok)

        tool_freq   = sorted(
            [{"name": k, "count": v} for k, v in rollout["tool_freq"].items()],
            key=lambda x: -x["count"]
        )
        total_tools = sum(t["count"] for t in tool_freq)

        dt = datetime.datetime.fromtimestamp(s["created_at"], tz=datetime.UTC)
        sessions_out.append({
            "num":        i,
            "fullId":     s["id"],
            "sessionId":  s["id"].split("-")[-1][:12],
            "started":    dt.strftime("%Y-%m-%d %H:%M"),
            "startedTs":  dt.strftime("%Y-%m-%dT%H:%M:%S UTC"),
            "createdAtMs": s["created_at"] * 1000,
            "inWindow":   in_window,
            "credits":    credits,
            "input":      in_tok,
            "cache":      cache_pct,
            "cachedTok":  cach_tok,
            "output":     out_tok,
            "reasoning":  reas_tok,
            "tools":      total_tools,
            "model":      model,
            "rateIn":     rates["in"],
            "rateCached": rates["cached"],
            "rateOut":    rates["out"],
            "cwd":        s["cwd"],
            "prompt":     (s["first_prompt"] or "")[:200],
            "perTurn":    rollout["per_turn"],
            "toolFreq":   tool_freq,
        })
        if i % 50 == 0 or i == len(raw_sessions):
            print(f"  [{i}/{len(raw_sessions)}] processed")

    generated_at = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")

    # ── write data JSON for live refresh ──────────────────────────────────
    script_dir = Path(__file__).parent.parent
    data_path  = script_dir / "data.json"
    data_payload = {"generatedAt": generated_at, "sessions": sessions_out}
    data_path.write_text(json.dumps(data_payload, ensure_ascii=False), encoding="utf-8")
    print(f"Data JSON  → {data_path}")

    # ── write HTML ────────────────────────────────────────────────────────
    html = HTML_TEMPLATE
    html = html.replace("{{GENERATED_AT}}",  generated_at)

    out_path = Path(args.out) if args.out else script_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Dashboard  → {out_path}")

    total_tokens  = sum(s["inWindow"] for s in sessions_out)
    total_credits = round(sum(s["credits"] for s in sessions_out), 1)
    print(f"\n  {len(sessions_out)} sessions | {total_tokens:,} tokens | ~{total_credits:,.1f} credits")


if __name__ == "__main__":
    main()
