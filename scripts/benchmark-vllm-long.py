#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "rich>=13.7",
# ]
# ///
"""
vLLM long-task performance benchmark.

Stress-tests the stack at long context (8K/32K/128K) and on a complicated
code-generation task, measured at concurrency=1 and concurrency=3 by default.
Runs for 10-25 minutes depending on configured sizes.

Usage:
    export LITELLM_URL=http://<host>:4000
    export LITELLM_KEY=<litellm-master-key>
    uv run scripts/benchmark-vllm-long.py
    uv run scripts/benchmark-vllm-long.py --long-context-sizes 8k,32k
    uv run scripts/benchmark-vllm-long.py --allow-kv-cache-stress
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

_URL_RE = re.compile(r"https?://[^\s/'\"<>]+")
_BEARER_RE = re.compile(r"[Bb]earer\s+\S+")
_CODE_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
_CODE_START_RE = re.compile(r"^\s*(def |import |from |# ///|class |async def )", re.MULTILINE)


LOREM_BLOCK = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer 42 nec "
    "odio praesent libero. Sed cursus ante dapibus diam. Sed nisi. Nulla quis "
    "sem at nibh elementum imperdiet. Duis sagittis ipsum. Praesent mauris. "
    "Fusce nec tellus sed augue semper porta. Mauris massa. Vestibulum lacinia "
    "arcu eget nulla. Class aptent taciti sociosqu ad litora torquent per "
    "conubia nostra, per inceptos himenaeos. Curabitur sodales ligula in libero. "
    "Sed dignissim lacinia nunc. Curabitur tortor, 2026 pellentesque nibh, est "
    "dictum 13 mi, eget auctor odio purus ut libero. Nullam et lacus aliquet "
    "pede consectetuer placerat. Cras dictum ultricies ligula. Nam at tortor in "
    "tellus interdum sagittis. Aliquam lobortis. Donec orci lectus, aliquam ut, "
    "faucibus non, euismod id, nulla. Curabitur blandit mollis lacus. Nam ac "
    "tortor dignissim convallis aenean et tortor. Sed dictum laoreet erat. "
)
NEEDLE = "The secret authorization code for the maintenance window is FOXTROT-7743."
L2_QUESTION = "Question: What is the secret authorization code for the maintenance window?"
L2_SYSTEM = "You are a careful assistant. Answer using only information from the provided context."


def _sanitize(msg: str) -> str:
    if not msg:
        return msg
    msg = _URL_RE.sub("<redacted-url>", msg)
    msg = _BEARER_RE.sub("Bearer <redacted>", msg)
    return msg


@dataclass
class RequestResult:
    label: str
    concurrency: int
    wall_time: float = 0.0
    ttft: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    effective_prefill_tps: float | None = None
    gen_tps: float | None = None
    code_valid: bool | None = None
    time_to_valid_python: float | None = None
    needle_found: bool | None = None
    error: str | None = None


@dataclass
class TierResult:
    tier: str
    concurrency: int
    size_label: str = ""
    results: list[RequestResult] = field(default_factory=list)

    def pass_count(self, check: str) -> tuple[int, int]:
        if check == "code_valid":
            ok = sum(1 for r in self.results if r.code_valid is True)
        elif check == "needle_found":
            ok = sum(1 for r in self.results if r.needle_found is True)
        elif check == "nonempty":
            ok = sum(
                1 for r in self.results
                if r.error is None and (r.completion_tokens or 0) > 0
            )
        else:
            raise ValueError(f"unknown check {check}")
        return ok, len(self.results)

    def median_or_none(self, attr: str) -> float | None:
        vals = [getattr(r, attr) for r in self.results if getattr(r, attr) is not None]
        return statistics.median(vals) if vals else None

    def p95_or_none(self, attr: str) -> float | None:
        vals = sorted(
            getattr(r, attr) for r in self.results if getattr(r, attr) is not None
        )
        if not vals:
            return None
        if len(vals) == 1:
            return vals[0]
        idx = max(0, int(round(0.95 * (len(vals) - 1))))
        return vals[idx]


def _extract_code(text: str) -> str:
    m = _CODE_FENCE_RE.search(text)
    if m:
        return m.group(1)
    m = _CODE_START_RE.search(text)
    if m:
        return text[m.start():]
    return text


def _validate_code(code: str) -> tuple[bool, str | None]:
    if not code.strip():
        return False, "empty code block"
    fd, path = tempfile.mkstemp(suffix=".py", prefix="bench_long_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
            f.flush()
            os.fsync(f.fileno())
        proc = subprocess.run(
            ["python3", "-m", "py_compile", path],
            capture_output=True,
            timeout=15,
            text=True,
        )
        if proc.returncode == 0:
            return True, None
        return False, _sanitize((proc.stderr or proc.stdout or "py_compile failed").strip())
    except subprocess.TimeoutExpired:
        return False, "py_compile timed out after 15s"
    except Exception as exc:
        return False, _sanitize(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
        pyc = Path(path).with_suffix(".pyc")
        try:
            pyc.unlink(missing_ok=True)
        except OSError:
            pass


def _check_needle(text: str) -> bool:
    """Loose needle retrieval check — tolerates formatting variations."""
    return "FOXTROT" in text and "7743" in text


async def _stream_one_request(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict],
    max_tokens: int,
    *,
    extract_code: bool,
    check_needle: bool,
    label: str,
    concurrency: int,
    api_key: str,
) -> RequestResult:
    result = RequestResult(label=label, concurrency=concurrency)
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "seed": 42,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    start = time.monotonic()
    text_buf: list[str] = []
    try:
        async with client.stream(
            "POST", "/v1/chat/completions", json=body, headers=headers
        ) as resp:
            if resp.status_code != 200:
                raw = (await resp.aread()).decode("utf-8", errors="replace")
                result.error = _sanitize(f"HTTP {resp.status_code}: {raw[:300]}")
                result.wall_time = time.monotonic() - start
                return result
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                usage = chunk.get("usage")
                if usage:
                    pt = usage.get("prompt_tokens")
                    ct = usage.get("completion_tokens")
                    if isinstance(pt, int):
                        result.prompt_tokens = pt
                    if isinstance(ct, int):
                        result.completion_tokens = ct
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    combined = piece + reasoning
                    if combined:
                        if result.ttft is None:
                            result.ttft = time.monotonic() - start
                        text_buf.append(combined)
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
        result.error = _sanitize(f"timeout: {type(exc).__name__}")
    except httpx.HTTPError as exc:
        result.error = _sanitize(f"http error: {type(exc).__name__}: {exc}")
    result.wall_time = time.monotonic() - start

    full_text = "".join(text_buf)

    if (
        result.completion_tokens is not None
        and result.ttft is not None
        and result.wall_time > result.ttft
    ):
        gen_span = result.wall_time - result.ttft
        if gen_span > 0:
            result.gen_tps = result.completion_tokens / gen_span

    if (
        result.prompt_tokens is not None
        and result.ttft is not None
        and result.ttft > 0
    ):
        result.effective_prefill_tps = result.prompt_tokens / result.ttft

    if extract_code and result.error is None:
        code = _extract_code(full_text)
        ok, err = _validate_code(code)
        result.code_valid = ok
        if ok:
            result.time_to_valid_python = time.monotonic() - start
        else:
            result.error = err

    if check_needle and result.error is None:
        result.needle_found = _check_needle(full_text)

    return result


async def _run_tier(
    client: httpx.AsyncClient,
    *,
    tier: str,
    concurrency: int,
    runs: int,
    warmup: int,
    model: str,
    api_key: str,
    messages: list[dict],
    max_tokens: int,
    extract_code: bool,
    check_needle: bool,
    size_label: str,
    console: Console,
) -> TierResult:
    async def one(label: str) -> RequestResult:
        return await _stream_one_request(
            client,
            model,
            messages,
            max_tokens,
            extract_code=extract_code,
            check_needle=check_needle,
            label=label,
            concurrency=concurrency,
            api_key=api_key,
        )

    for _ in range(warmup):
        console.log(f"[dim]{tier} C={concurrency}{f' {size_label}' if size_label else ''}: warmup[/dim]")
        await asyncio.gather(*(one(f"{tier}-warmup") for _ in range(concurrency)))

    tr = TierResult(tier=tier, concurrency=concurrency, size_label=size_label)
    for run_idx in range(runs):
        labels = [f"{tier}-{size_label}-r{run_idx}-s{s}" for s in range(concurrency)]
        batch = await asyncio.gather(*(one(lbl) for lbl in labels))
        tr.results.extend(batch)
        errs = [r for r in batch if r.error]
        if errs:
            console.log(
                f"[yellow]{tier} C={concurrency}{f' {size_label}' if size_label else ''} "
                f"run {run_idx}: {len(errs)}/{len(batch)} errored[/yellow]"
            )

    return tr


async def _calibrate_chars_per_token(
    client: httpx.AsyncClient, model: str, api_key: str, console: Console
) -> float:
    """One-shot: send a known-size filler block, read back usage.prompt_tokens."""
    sample = LOREM_BLOCK * 10  # ~7-10K chars
    body = {
        "model": model,
        "messages": [{"role": "user", "content": sample}],
        "max_tokens": 1,
        "temperature": 0.0,
        "seed": 42,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    resp = await client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=180.0,
    )
    resp.raise_for_status()
    data = resp.json()
    prompt_tokens = (data.get("usage") or {}).get("prompt_tokens")
    if not isinstance(prompt_tokens, int) or prompt_tokens <= 0:
        raise RuntimeError("calibration response did not return usage.prompt_tokens")
    ratio = len(sample) / prompt_tokens
    console.log(
        f"[dim]calibration: {len(sample)} chars → {prompt_tokens} tokens "
        f"({ratio:.2f} chars/token)[/dim]"
    )
    return ratio


def _build_long_prompt(target_tokens: int, chars_per_token: float) -> list[dict]:
    """Build a prompt with ~target_tokens tokens of filler + needle at 80% depth."""
    target_chars = int(target_tokens * chars_per_token * 0.95)  # trim 5% for system + question overhead
    block_size = len(LOREM_BLOCK)
    blocks_needed = max(1, target_chars // block_size)
    filler = (LOREM_BLOCK * blocks_needed)[:target_chars]
    needle_pos = int(len(filler) * 0.8)
    # insert at nearest space boundary for readability
    while needle_pos < len(filler) and filler[needle_pos] != " " and needle_pos < len(filler) - 1:
        needle_pos += 1
    filler = filler[:needle_pos] + " " + NEEDLE + " " + filler[needle_pos:]
    user_content = f"{filler}\n\n{L2_QUESTION}"
    return [
        {"role": "system", "content": L2_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def _parse_size(raw: str) -> int:
    """Parse '8k', '32k', '128k', '200' into an integer token count."""
    raw = raw.strip().lower()
    if raw.endswith("k"):
        return int(float(raw[:-1]) * 1024)
    return int(raw)


async def _preflight_model(client: httpx.AsyncClient, model: str, api_key: str) -> None:
    resp = await client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
    if model not in ids:
        raise SystemExit(
            f"model '{model}' not in /v1/models. available: {', '.join(ids) or '(none)'}"
        )


async def _cold_start_probe(
    client: httpx.AsyncClient, model: str, api_key: str, console: Console
) -> float | None:
    """Send one tiny request with a 180s timeout; return the wall time if notable."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 1,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    start = time.monotonic()
    resp = await client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=180.0,
    )
    resp.raise_for_status()
    elapsed = time.monotonic() - start
    if elapsed > 60.0:
        console.log(f"[yellow]cold_start_s = {elapsed:.1f}s (not folded into tier metrics)[/yellow]")
        return elapsed
    console.log(f"[dim]cold-start probe ok ({elapsed:.2f}s)[/dim]")
    return None


def _fmt(v: float | None, precision: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.{precision}f}{suffix}"


def _parse_concurrency_levels(raw: str) -> list[int]:
    try:
        levels = [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"invalid --concurrency-levels: {raw!r}")
    if not levels:
        raise SystemExit("--concurrency-levels must have at least one value")
    for lv in levels:
        if lv < 1 or lv > 16:
            raise SystemExit(f"--concurrency-levels values must be in [1,16], got {lv}")
    return sorted(set(levels))


def _parse_sizes(raw: str) -> list[tuple[str, int]]:
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        out.append((part, _parse_size(part)))
    if not out:
        raise SystemExit("--long-context-sizes must have at least one value")
    return out


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="vLLM long-task performance benchmark (single-file, uv-runnable).",
        epilog=(
            "Stress-tests long context (8K/32K/128K) and complicated code-gen at two\n"
            "concurrency levels side-by-side. Runs for 10-25 minutes.\n"
            "\n"
            "Live smoke test (replace placeholders with your LiteLLM proxy and key):\n"
            "    export LITELLM_URL=http://<dgx-host>:4000\n"
            "    export LITELLM_KEY=<your-litellm-master-key>\n"
            "    uv run scripts/benchmark-vllm-long.py --long-context-sizes 8k --runs 1\n"
            "\n"
            "Medium run (skip the 128k pass, ~6-10 min):\n"
            "    uv run scripts/benchmark-vllm-long.py --long-context-sizes 8k,32k\n"
            "\n"
            "Full default (includes 128k at C=1; 128k at C=3 is auto-skipped for\n"
            "KV-cache safety — pass --allow-kv-cache-stress to override):\n"
            "    uv run scripts/benchmark-vllm-long.py\n"
            "\n"
            "Full stress including 128k under concurrency (may OOM — advisory only):\n"
            "    uv run scripts/benchmark-vllm-long.py --allow-kv-cache-stress"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--base-url", default=os.environ.get("LITELLM_URL"),
                   help="LiteLLM proxy base URL (env: LITELLM_URL)")
    p.add_argument("--api-key", default=os.environ.get("LITELLM_KEY"),
                   help="LiteLLM master key (env: LITELLM_KEY)")
    p.add_argument("--model", default="qwen35",
                   help="Model alias to benchmark (default: qwen35)")
    p.add_argument("--concurrency-levels", default="1,3",
                   help="Comma-separated concurrency levels to measure (default: 1,3)")
    p.add_argument("--runs", type=int, default=3,
                   help="Measurement runs/batches per tier per concurrency level (default: 3)")
    p.add_argument("--warmup", type=int, default=1,
                   help="Discarded warmup runs per tier per concurrency level (default: 1)")
    p.add_argument("--long-context-sizes", default="8k,32k,128k",
                   help="Comma-separated target prompt token counts for L2 (default: 8k,32k,128k)")
    p.add_argument("--tiers", default="l1,l2",
                   help="Which tiers to run (default: l1,l2)")
    p.add_argument("--allow-kv-cache-stress", action="store_true",
                   help="Allow 128k prompts at concurrency > 1 (may OOM vLLM on unified memory)")
    p.add_argument("--output-json", type=Path, default=None,
                   help="Write sanitized per-request metrics to this JSON file")
    p.add_argument("--verbose", action="store_true",
                   help="Log per-request events (response body only, never headers)")
    return p.parse_args()


def _require(value: str | None, name: str, envvar: str) -> str:
    if not value:
        raise SystemExit(
            f"{name} is required — pass --{name.replace('_','-')} or set ${envvar}"
        )
    return value


L1_MESSAGES = [
    {
        "role": "system",
        "content": (
            "You are an expert Python engineer. Produce complete, correct, runnable "
            "code in a single fenced python block. No prose before or after."
        ),
    },
    {
        "role": "user",
        "content": (
            "Write a complete single-file Python benchmark tool with PEP 723 inline "
            "dependencies (httpx, rich) that measures HTTP GET latency to a configurable URL. "
            "Requirements: async concurrent requests with --concurrency N flag (default 10); "
            "--requests N flag (default 100); prints a rich table of p50/p90/p99 latency, "
            "status code breakdown, and a text histogram of latency buckets; installs a SIGINT "
            "handler that cancels in-flight requests and prints partial results before exiting; "
            "reads URL and concurrency from CLI args via argparse with --help. Include the full "
            "'# /// script' block at top with httpx>=0.27 and rich>=13.7 in dependencies. "
            "Output ONLY the code inside a single fenced ```python block — no explanation, "
            "no markdown headers, no prose."
        ),
    },
]


def _render_l1_table(console: Console, rows: dict[int, TierResult],
                     concurrency_levels: list[int]) -> bool:
    table = Table(show_header=True, header_style="bold",
                  title="Tier L1 — complicated code generation")
    table.add_column("Concurrency", style="cyan", no_wrap=True)
    table.add_column("TTFT med/p95", justify="right")
    table.add_column("gen_tps med", justify="right")
    table.add_column("wall med", justify="right")
    table.add_column("time_to_valid med", justify="right")
    table.add_column("code_valid %", justify="right")
    all_ok = True
    for c in concurrency_levels:
        tr = rows.get(c)
        if tr is None or not tr.results:
            table.add_row(f"C={c}", "—", "—", "—", "—", "—", style="red")
            all_ok = False
            continue
        ok, total = tr.pass_count("code_valid")
        valid_pct = (100 * ok / total) if total else 0.0
        row_ok = total > 0 and (total - ok) <= 1
        if not row_ok:
            all_ok = False
        table.add_row(
            f"C={c}",
            f"{_fmt(tr.median_or_none('ttft'), 2, 's')} / {_fmt(tr.p95_or_none('ttft'), 2, 's')}",
            _fmt(tr.median_or_none('gen_tps'), 1, ' tok/s'),
            _fmt(tr.median_or_none('wall_time'), 2, 's'),
            _fmt(tr.median_or_none('time_to_valid_python'), 2, 's'),
            f"{valid_pct:.0f}%",
            style=None if row_ok else "red",
        )
    console.print(table)
    return all_ok


def _render_l2_table(
    console: Console,
    rows: dict[tuple[str, int], TierResult],
    concurrency_levels: list[int],
    sizes: list[tuple[str, int]],
) -> bool:
    table = Table(show_header=True, header_style="bold",
                  title="Tier L2 — long-context needle-in-haystack")
    table.add_column("Size", style="cyan", no_wrap=True)
    table.add_column("Concurrency", no_wrap=True)
    table.add_column("prompt_tokens med", justify="right")
    table.add_column("TTFT med/p95", justify="right")
    table.add_column("prefill_tps (effective)", justify="right")
    table.add_column("wall med", justify="right")
    table.add_column("needle_found %", justify="right")
    all_ok = True
    for size_label, _target in sizes:
        for c in concurrency_levels:
            tr = rows.get((size_label, c))
            if tr is None:
                table.add_row(size_label, f"C={c}", "—", "—", "—", "—", "skipped (KV-safety)", style="yellow")
                continue
            if not tr.results:
                table.add_row(size_label, f"C={c}", "—", "—", "—", "—", "—", style="red")
                all_ok = False
                continue
            ok, total = tr.pass_count("needle_found")
            pct = (100 * ok / total) if total else 0.0
            row_ok = total > 0 and (total - ok) <= 1
            if not row_ok:
                all_ok = False
            table.add_row(
                size_label,
                f"C={c}",
                _fmt(tr.median_or_none("prompt_tokens"), 0),
                f"{_fmt(tr.median_or_none('ttft'), 2, 's')} / {_fmt(tr.p95_or_none('ttft'), 2, 's')}",
                _fmt(tr.median_or_none("effective_prefill_tps"), 0, ' tok/s'),
                _fmt(tr.median_or_none("wall_time"), 2, 's'),
                f"{pct:.0f}%",
                style=None if row_ok else "red",
            )
    console.print(table)
    return all_ok


def _write_output_json(
    path: Path,
    l1_rows: dict[int, TierResult],
    l2_rows: dict[tuple[str, int], TierResult],
    model: str,
    cold_start_s: float | None,
    chars_per_token: float | None,
) -> None:
    payload: dict[str, Any] = {
        "model": model,
        "cold_start_s": cold_start_s,
        "chars_per_token": chars_per_token,
        "tiers": {"L1": {}, "L2": {}},
    }
    for c, tr in l1_rows.items():
        payload["tiers"]["L1"][str(c)] = [
            {
                "label": r.label, "concurrency": r.concurrency,
                "wall_time": r.wall_time, "ttft": r.ttft,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "effective_prefill_tps": r.effective_prefill_tps, "gen_tps": r.gen_tps,
                "code_valid": r.code_valid, "time_to_valid_python": r.time_to_valid_python,
                "error": _sanitize(r.error) if r.error else None,
            }
            for r in tr.results
        ]
    for (size, c), tr in l2_rows.items():
        key = f"{size}:{c}"
        payload["tiers"]["L2"][key] = [
            {
                "label": r.label, "concurrency": r.concurrency, "size": size,
                "wall_time": r.wall_time, "ttft": r.ttft,
                "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                "effective_prefill_tps": r.effective_prefill_tps, "gen_tps": r.gen_tps,
                "needle_found": r.needle_found,
                "error": _sanitize(r.error) if r.error else None,
            }
            for r in tr.results
        ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def _main_async(args: argparse.Namespace) -> int:
    base_url = _require(args.base_url, "base-url", "LITELLM_URL").rstrip("/")
    api_key = _require(args.api_key, "api-key", "LITELLM_KEY")
    concurrency_levels = _parse_concurrency_levels(args.concurrency_levels)
    sizes = _parse_sizes(args.long_context_sizes)
    tiers_enabled = {t.strip().lower() for t in args.tiers.split(",") if t.strip()}
    run_l1 = "l1" in tiers_enabled
    run_l2 = "l2" in tiers_enabled

    console = Console()
    timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)

    l1_rows: dict[int, TierResult] = {}
    l2_rows: dict[tuple[str, int], TierResult] = {}
    cold_start_s: float | None = None
    chars_per_token: float | None = None

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, http2=False) as client:
        console.log("[dim]preflight: GET /v1/models[/dim]")
        await _preflight_model(client, args.model, api_key)
        cold_start_s = await _cold_start_probe(client, args.model, api_key, console)

        # ---- Tier L1 ----
        if run_l1:
            for c in concurrency_levels:
                console.log(
                    f"[bold]L1 code-gen[/bold] C={c}: {args.runs} runs ({args.runs * c} requests)"
                )
                tr = await _run_tier(
                    client,
                    tier="L1",
                    concurrency=c,
                    runs=args.runs,
                    warmup=args.warmup,
                    model=args.model,
                    api_key=api_key,
                    messages=L1_MESSAGES,
                    max_tokens=2500,
                    extract_code=True,
                    check_needle=False,
                    size_label="",
                    console=console,
                )
                l1_rows[c] = tr

        # ---- Tier L2 ----
        if run_l2:
            chars_per_token = await _calibrate_chars_per_token(
                client, args.model, api_key, console
            )

            # Determine the largest eligible size for warmup (priming FP8 attn kernels)
            largest_size_label, largest_size_tokens = sizes[-1]
            for c in concurrency_levels:
                warmup_size_tokens = largest_size_tokens
                warmup_size_label = largest_size_label
                # If 128k is the largest but not allowed under this concurrency, fall back
                if (c > 1 and not args.allow_kv_cache_stress
                        and warmup_size_tokens > 64 * 1024):
                    eligible = [
                        (lbl, tok) for lbl, tok in sizes if tok <= 64 * 1024 or c == 1
                    ]
                    if eligible:
                        warmup_size_label, warmup_size_tokens = eligible[-1]
                console.log(
                    f"[dim]L2 warmup C={c} at {warmup_size_label} "
                    f"(primes FP8 attn kernels)[/dim]"
                )
                warmup_msgs = _build_long_prompt(warmup_size_tokens, chars_per_token)
                # One silent warmup batch — results discarded
                await asyncio.gather(
                    *(
                        _stream_one_request(
                            client, args.model, warmup_msgs, max_tokens=40,
                            extract_code=False, check_needle=False,
                            label=f"L2-warmup-{warmup_size_label}",
                            concurrency=c, api_key=api_key,
                        )
                        for _ in range(c)
                    )
                )

                for size_label, target_tokens in sizes:
                    if (c > 1
                            and target_tokens > 64 * 1024
                            and not args.allow_kv_cache_stress):
                        console.log(
                            f"[yellow]Skipping {size_label} at C={c} "
                            f"(pass --allow-kv-cache-stress to override — "
                            f"may OOM the vLLM pod on unified memory)[/yellow]"
                        )
                        continue
                    messages = _build_long_prompt(target_tokens, chars_per_token)
                    console.log(
                        f"[bold]L2 needle[/bold] C={c} {size_label}: "
                        f"{args.runs} runs ({args.runs * c} requests)"
                    )
                    # No per-size warmup — the largest-size warmup above primes the path
                    tr = await _run_tier(
                        client,
                        tier="L2",
                        concurrency=c,
                        runs=args.runs,
                        warmup=0,  # warmup already done at largest size
                        model=args.model,
                        api_key=api_key,
                        messages=messages,
                        max_tokens=40,
                        extract_code=False,
                        check_needle=True,
                        size_label=size_label,
                        console=console,
                    )
                    l2_rows[(size_label, c)] = tr

    console.print()
    ok_l1 = True
    ok_l2 = True
    if run_l1:
        ok_l1 = _render_l1_table(console, l1_rows, concurrency_levels)
    if run_l2:
        ok_l2 = _render_l2_table(console, l2_rows, concurrency_levels, sizes)
    console.print(
        f"[dim]Model: {args.model}. Stack tuned for kv-cache-dtype=fp8, "
        f"gpu-memory-utilization=0.7, max-model-len=262144 "
        f"(see ansible/group_vars/all/vars.yml).[/dim]"
    )
    if cold_start_s is not None:
        console.print(f"[dim]cold_start_s = {cold_start_s:.1f}s (not folded into tier metrics)[/dim]")

    if args.output_json:
        _write_output_json(
            args.output_json, l1_rows, l2_rows, args.model, cold_start_s, chars_per_token
        )
        console.log(f"[dim]wrote sanitized metrics to {args.output_json}[/dim]")

    return 0 if (ok_l1 and ok_l2) else 1


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        print(f"unhandled error: {_sanitize(f'{type(exc).__name__}: {exc}')}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
