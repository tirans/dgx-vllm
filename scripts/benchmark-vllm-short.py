#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "rich>=13.7",
# ]
# ///
"""
vLLM short-task performance benchmark.

Measures TTFT, gen throughput, wall time, and code-generation validity at
two concurrency levels side-by-side (C=1 and C=3 by default). Runs in
about 90-120 seconds end-to-end.

Usage:
    export LITELLM_URL=http://<host>:4000
    export LITELLM_KEY=<litellm-master-key>
    uv run scripts/benchmark-vllm-short.py
    uv run scripts/benchmark-vllm-short.py --concurrency-levels 1 --runs 3
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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

_URL_RE = re.compile(r"https?://[^\s/'\"<>]+")
_BEARER_RE = re.compile(r"[Bb]earer\s+\S+")
_CODE_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
_CODE_START_RE = re.compile(r"^\s*(def |import |from |# ///|class |async def )", re.MULTILINE)


def _sanitize(msg: str) -> str:
    """Strip URLs and bearer tokens from free-form strings before logging."""
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
    gen_tps: float | None = None
    code_valid: bool | None = None
    time_to_valid_python: float | None = None
    error: str | None = None


@dataclass
class TierResult:
    tier: str
    concurrency: int
    results: list[RequestResult] = field(default_factory=list)

    def pass_count(self, check: str) -> tuple[int, int]:
        """Return (successes, total) for a named check."""
        if check == "nonempty":
            ok = sum(
                1
                for r in self.results
                if r.error is None and (r.completion_tokens or 0) > 0
            )
        elif check == "code_valid":
            ok = sum(1 for r in self.results if r.code_valid is True)
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
    """Pull a runnable-looking Python block out of a model response."""
    m = _CODE_FENCE_RE.search(text)
    if m:
        return m.group(1)
    m = _CODE_START_RE.search(text)
    if m:
        return text[m.start():]
    return text


def _validate_code(code: str) -> tuple[bool, str | None]:
    """Run py_compile on the extracted code. Returns (ok, sanitized_error)."""
    if not code.strip():
        return False, "empty code block"
    fd, path = tempfile.mkstemp(suffix=".py", prefix="bench_short_")
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


async def _stream_one_request(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict],
    max_tokens: int,
    *,
    extract_code: bool,
    label: str,
    concurrency: int,
    api_key: str,
) -> RequestResult:
    """Execute one streaming chat-completions request and collect metrics."""
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
                result.error = _sanitize(
                    f"HTTP {resp.status_code}: {raw[:300]}"
                )
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

    if extract_code and result.error is None:
        code = _extract_code(full_text)
        compile_start = time.monotonic()
        ok, err = _validate_code(code)
        result.code_valid = ok
        if ok:
            result.time_to_valid_python = (time.monotonic() - start)
        else:
            result.error = err

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
    console: Console,
) -> TierResult:
    """Run warmup + N measurement runs at a given concurrency level."""

    async def one(label: str) -> RequestResult:
        return await _stream_one_request(
            client,
            model,
            messages,
            max_tokens,
            extract_code=extract_code,
            label=label,
            concurrency=concurrency,
            api_key=api_key,
        )

    for _ in range(warmup):
        console.log(f"[dim]{tier} C={concurrency}: warmup[/dim]")
        await asyncio.gather(*(one(f"{tier}-warmup") for _ in range(concurrency)))

    tier_result = TierResult(tier=tier, concurrency=concurrency)
    for run_idx in range(runs):
        labels = [f"{tier}-r{run_idx}-s{s}" for s in range(concurrency)]
        batch = await asyncio.gather(*(one(lbl) for lbl in labels))
        tier_result.results.extend(batch)
        any_err = [r for r in batch if r.error]
        if any_err:
            console.log(
                f"[yellow]{tier} C={concurrency} run {run_idx}: "
                f"{len(any_err)}/{len(batch)} errored[/yellow]"
            )

    return tier_result


async def _preflight_model(
    client: httpx.AsyncClient, model: str, api_key: str
) -> None:
    """Fetch /v1/models and verify the requested model exists."""
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


def _fmt_float(v: float | None, precision: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.{precision}f}{suffix}"


def _render_table(
    console: Console,
    tier_rows: list[tuple[str, dict[int, TierResult]]],
    concurrency_levels: list[int],
    include_code: dict[str, bool],
) -> bool:
    """Render the rich output table and return overall pass/fail."""
    table = Table(show_header=True, header_style="bold", title="vLLM short-task benchmark")
    table.add_column("Tier", style="cyan", no_wrap=True)
    for c in concurrency_levels:
        table.add_column(f"C={c} TTFT med/p95", justify="right")
        table.add_column(f"C={c} gen_tps med", justify="right")
        table.add_column(f"C={c} wall med", justify="right")
        table.add_column(f"C={c} valid %", justify="right")

    all_pass = True
    for tier_name, by_c in tier_rows:
        row = [tier_name]
        tier_ok = True
        for c in concurrency_levels:
            tr = by_c.get(c)
            if tr is None or not tr.results:
                row.extend(["—", "—", "—", "—"])
                tier_ok = False
                continue
            ttft_med = tr.median_or_none("ttft")
            ttft_p95 = tr.p95_or_none("ttft")
            gen_med = tr.median_or_none("gen_tps")
            wall_med = tr.median_or_none("wall_time")
            if include_code.get(tier_name, False):
                ok, total = tr.pass_count("code_valid")
                valid_str = f"{100 * ok / total:.0f}%" if total else "—"
                if total and (total - ok) > 1:
                    tier_ok = False
            else:
                ok, total = tr.pass_count("nonempty")
                valid_str = "n/a"
                if total and (total - ok) > 1:
                    tier_ok = False

            row.append(
                f"{_fmt_float(ttft_med, 2, 's')} / {_fmt_float(ttft_p95, 2, 's')}"
            )
            row.append(_fmt_float(gen_med, 1, ' tok/s'))
            row.append(_fmt_float(wall_med, 2, 's'))
            row.append(valid_str)
        table.add_row(*row, style=None if tier_ok else "red")
        if not tier_ok:
            all_pass = False

    console.print(table)
    return all_pass


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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="vLLM short-task performance benchmark (single-file, uv-runnable).",
        epilog=(
            "Measures TTFT + code-gen correctness at two concurrency levels side-by-side.\n"
            "\n"
            "Live smoke test (replace placeholders with your LiteLLM proxy and key):\n"
            "    export LITELLM_URL=http://<dgx-host>:4000\n"
            "    export LITELLM_KEY=<your-litellm-master-key>\n"
            "    uv run scripts/benchmark-vllm-short.py --runs 1\n"
            "\n"
            "Single-stream only (skip the C=3 pass):\n"
            "    uv run scripts/benchmark-vllm-short.py --concurrency-levels 1\n"
            "\n"
            "Full default run with sanitized JSON output:\n"
            "    uv run scripts/benchmark-vllm-short.py \\\n"
            "        --output-json /tmp/bench-short.json"
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
    p.add_argument("--runs", type=int, default=5,
                   help="Measurement runs/batches per tier per concurrency level (default: 5)")
    p.add_argument("--warmup", type=int, default=1,
                   help="Discarded warmup runs per tier per concurrency level (default: 1)")
    p.add_argument("--timeout", type=float, default=60.0,
                   help="Per-request read timeout in seconds (default: 60)")
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


def _write_output_json(path: Path, tier_rows: list[tuple[str, dict[int, TierResult]]],
                      model: str) -> None:
    """Dump raw per-request metrics to JSON, sanitized."""
    payload: dict[str, Any] = {"model": model, "tiers": []}
    for tier_name, by_c in tier_rows:
        tier_entry: dict[str, Any] = {"tier": tier_name, "by_concurrency": {}}
        for c, tr in by_c.items():
            tier_entry["by_concurrency"][str(c)] = [
                {
                    "label": r.label,
                    "concurrency": r.concurrency,
                    "wall_time": r.wall_time,
                    "ttft": r.ttft,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "gen_tps": r.gen_tps,
                    "code_valid": r.code_valid,
                    "time_to_valid_python": r.time_to_valid_python,
                    "error": _sanitize(r.error) if r.error else None,
                }
                for r in tr.results
            ]
        payload["tiers"].append(tier_entry)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


S1_MESSAGES = [
    {"role": "system", "content": "You are a terse assistant."},
    {"role": "user", "content": "Reply with a single word: READY"},
]

S2_MESSAGES = [
    {
        "role": "system",
        "content": "You are an expert Python programmer. Output only code in a single fenced block. No prose.",
    },
    {
        "role": "user",
        "content": (
            "Write a Python function validate_email(s: str) -> bool that uses a regex "
            "to check RFC-5322-ish email format. Include a complete PEP 723 script header "
            "(# /// script ... # ///) with no external dependencies, and a main() that runs "
            "3 test cases. Output only the code, nothing else."
        ),
    },
]


async def _main_async(args: argparse.Namespace) -> int:
    base_url = _require(args.base_url, "base-url", "LITELLM_URL").rstrip("/")
    api_key = _require(args.api_key, "api-key", "LITELLM_KEY")
    concurrency_levels = _parse_concurrency_levels(args.concurrency_levels)

    console = Console()
    timeout = httpx.Timeout(
        connect=10.0, read=max(args.timeout, 60.0), write=30.0, pool=10.0
    )

    tiers = [
        ("S1-TTFT", S1_MESSAGES, 4, False),
        ("S2-gen", S2_MESSAGES, 400, True),
    ]
    include_code = {name: extract for name, _, _, extract in tiers}

    tier_rows: list[tuple[str, dict[int, TierResult]]] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, http2=False) as client:
        console.log(f"[dim]preflight: GET /v1/models[/dim]")
        await _preflight_model(client, args.model, api_key)
        console.log(f"[dim]preflight ok — model '{args.model}' reachable[/dim]")

        for tier_name, messages, max_tokens, extract_code in tiers:
            by_c: dict[int, TierResult] = {}
            for c in concurrency_levels:
                console.log(f"[bold]{tier_name}[/bold] C={c}: {args.runs} runs ({args.runs * c} requests)")
                tr = await _run_tier(
                    client,
                    tier=tier_name,
                    concurrency=c,
                    runs=args.runs,
                    warmup=args.warmup,
                    model=args.model,
                    api_key=api_key,
                    messages=messages,
                    max_tokens=max_tokens,
                    extract_code=extract_code,
                    console=console,
                )
                by_c[c] = tr
            tier_rows.append((tier_name, by_c))

    console.print()
    all_pass = _render_table(console, tier_rows, concurrency_levels, include_code)
    console.print(
        f"[dim]Model: {args.model}. Stack tuned for kv-cache-dtype=fp8, "
        f"gpu-memory-utilization=0.7 (see ansible/group_vars/all/vars.yml).[/dim]"
    )

    if args.output_json:
        _write_output_json(args.output_json, tier_rows, args.model)
        console.log(f"[dim]wrote sanitized metrics to {args.output_json}[/dim]")

    return 0 if all_pass else 1


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
        # Final safety net — sanitize before surfacing.
        print(f"unhandled error: {_sanitize(f'{type(exc).__name__}: {exc}')}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
