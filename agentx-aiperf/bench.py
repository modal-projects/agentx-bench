"""Run InferenceX AgentX-style (AIPerf) benchmarks against an inference endpoint on Modal.

Wraps NVIDIA AIPerf's `inferencex-agentx-mvp` scenario: closed-loop replay of real
coding agent session traces in the WEKA format.

Smoke test:
    modal run bench.py \
        --url https://<endpoint> --model Qwen/Qwen3.8-27B \
        --concurrency 1 --num-trajectories 1 --duration-seconds 900
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import modal

# pin aiperf
AIPERF_GIT_SHA = "911a1aa8f7cb72ec1119503c34f294b6c44e78cd"
AIPERF_PACKAGE = (
    f"aiperf @ https://github.com/ai-dynamo/aiperf/archive/{AIPERF_GIT_SHA}.tar.gz"
)

# set default dataset
DEFAULT_DATASET = "semianalysis_cc_traces_weka_with_subagents_256k"

# create a Modal App to attach resources to
app = modal.App("agentx-bench")

# cache datasets and tokenizers across runs on a Modal Volume
cache_volume = modal.Volume.from_name("agentx-bench-cache", create_if_missing=True)
_TIKTOKEN_CACHE_DIR = "/opt/tiktoken-cache"

# store created artifacts on a Modal Volume
artifact_volume = modal.Volume.from_name("agentx-bench-artifacts", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(AIPERF_PACKAGE)
    # add the default tokenizer manually and cache it
    .run_commands(
        "python -c "
        '"from aiperf.common.tokenizer import Tokenizer; '
        "Tokenizer.from_pretrained('builtin')\"",
        env={"TIKTOKEN_CACHE_DIR": _TIKTOKEN_CACHE_DIR},
    )
    .env({"TIKTOKEN_CACHE_DIR": _TIKTOKEN_CACHE_DIR})
)

MINUTES = 60 # seconds
HOURS = 60 * MINUTES

@app.function(
    image=image,
    timeout=3 * HOURS,
    volumes={"/cache": cache_volume, "/artifacts": artifact_volume},
    env={"HF_HOME": "/cache/hf"},
)
def run_agentx(
    *,
    url: str,
    model: str,
    tokenizer: str,
    dataset: str,
    concurrency: int,
    num_trajectories: int,
    duration_seconds: int,
    random_seed: int,
    max_context_length: int,
    api_key: str,
    run_id: str,
    extra_args: str,
) -> dict:
    artifact_root = Path("/artifacts") / run_id
    artifact_root.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "aiperf", "profile",
        "--scenario", "inferencex-agentx-mvp",
        "--url", url,
        "--model", model,
        "--tokenizer", tokenizer,
        "--endpoint-type", "chat",
        "--public-dataset", dataset,
        "--concurrency", str(concurrency),
        "--num-dataset-entries", str(num_trajectories),
        "--benchmark-duration", str(duration_seconds),
        "--random-seed", str(random_seed),
        "--use-server-token-count",
        # scenario-locked values, included for completeness
        "--streaming",
        "--extra-inputs", "ignore_eos:true",
        "--cache-bust", "first_turn_prefix",
        "--system-idle-gap-cap-seconds", "10",
        "--trajectory-start-min-ratio", "0.0",
        "--trajectory-start-max-ratio", "1.0",
        "--ui", "simple",
        "--artifact-dir", str(artifact_root),
    ]
    if max_context_length > 0:
        cmd += ["--max-context-length", str(max_context_length)]
    if api_key:
        cmd += ["--api-key", api_key]
    if extra_args:
        cmd += extra_args.split()

    print(f"[agentx-bench] launching: {' '.join(cmd)}", flush=True)
    started = time.time()
    completed = subprocess.run(cmd, check=False)
    wall_seconds = time.time() - started

    summaries = list(artifact_root.rglob("profile_export_aiperf.json"))
    result = {
        "run_id": run_id,
        "exit_code": completed.returncode,
        "wall_seconds": round(wall_seconds, 1),
        "artifact_dir": str(artifact_root),
        "summary_found": bool(summaries),
    }
    if summaries:
        summary = json.loads(summaries[0].read_text())
        result["summary_path"] = str(summaries[0])
        result["submission_valid"] = summary.get("submission_valid")
        result["metrics"] = _extract_metrics(summary)

    return result


def _stat(summary: dict, metric: str, stat: str):
    payload = summary.get(metric)
    if isinstance(payload, dict):
        return payload.get(stat)
    return None


def _extract_metrics(summary: dict) -> dict:
    keys = (
        "request_count",
        "request_throughput",
        "output_token_throughput",
        "request_latency",
        "time_to_first_token",
        "inter_token_latency",
        "output_token_throughput_per_user",
        "goodput",
    )
    out = {}
    for key in keys:
        entry = {}
        for stat in ("avg", "p50", "p99"):
            value = _stat(summary, key, stat)
            if value is not None:
                entry[stat] = value
        if entry:
            out[key] = entry
    return out


@app.local_entrypoint()
def main(
    url: str,
    model: str,
    tokenizer: str = "Qwen/Qwen3-32B",
    dataset: str = DEFAULT_DATASET,
    concurrency: int = 1,
    num_trajectories: int = 1,
    duration_seconds: int = 30 * MINUTES,  # AgentX default
    random_seed: int = 20260707,
    max_context_length: int = 262_144,  # 0 = no client-side filtering
    api_key: str = "",
    hf_token_secret: str = "",  # Modal secret name holding HF_TOKEN (for gated tokenizers)
    extra_args: str = "",
    run_id: str = "",
) -> None:
    run_id = run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    fn = run_agentx
    if hf_token_secret:
        fn = fn.with_options(secrets=[modal.Secret.from_name(hf_token_secret)])

    print(f"[agentx-bench] run_id={run_id}")
    print(f"[agentx-bench] target={url} model={model} tokenizer={tokenizer}")
    print(
        f"[agentx-bench] dataset={dataset} concurrency={concurrency} "
        f"trajectories={num_trajectories} duration={duration_seconds}s "
        f"seed={random_seed} max_ctx={max_context_length}"
    )

    result = fn.remote(
        url=url,
        model=model,
        tokenizer=tokenizer,
        dataset=dataset,
        concurrency=concurrency,
        num_trajectories=num_trajectories,
        duration_seconds=duration_seconds,
        random_seed=random_seed,
        max_context_length=max_context_length,
        api_key=api_key,
        run_id=run_id,
        extra_args=extra_args,
    )

    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("summary_found"):
        raise SystemExit(f"AIPerf failed (exit {result['exit_code']}); see logs above.")
