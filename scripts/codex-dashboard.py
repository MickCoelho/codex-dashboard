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
# Template lives in src/index.html — loaded at build time by main()


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
    template_path = script_dir / "src" / "index.html"
    html = template_path.read_text(encoding="utf-8")
    html = html.replace("{{GENERATED_AT}}",  generated_at)

    out_path = Path(args.out) if args.out else script_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Dashboard  → {out_path}")

    total_tokens  = sum(s["inWindow"] for s in sessions_out)
    total_credits = round(sum(s["credits"] for s in sessions_out), 1)
    print(f"\n  {len(sessions_out)} sessions | {total_tokens:,} tokens | ~{total_credits:,.1f} credits")


if __name__ == "__main__":
    main()
