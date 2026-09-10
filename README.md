# Run AgentX and develop/deploy inference servers on Modal

This repository implements a basic architecture for using Modal to run the [AgentX](https://inferencex.semianalysis.com/agentx) benchmark
and develop & deploy inference servers on [Modal](https://modal.com/),
supporting end-to-end agentic optimization of a server for AgentX workloads all on Modal.

That includes:

- running the AgentX benchmark with [AIPerf](https://github.com/ai-dynamo/aiperf) in a [Modal Function](https://modal.com/docs/guide/functions), storing results in a [Modal Volume](https://modal.com/docs/guide/volumes), and retrieving them with the `modal volume` CLI (`./agentx-aiperf`)
- serving performant inference on Modal with a [Modal Server](https://modal.com/docs/guide/servers) (`./serve`)
- hosting a development environment for the inference server in a [Modal Sandbox](https://modal.com/docs/guide/sandboxes) (`./sandbox`).

It also includes a minimal test server, matching the expected API for AIPerf
but without all the slow and expensive work, in `./stub_serve`.
This is deployable on Modal as a simple [Web Function](https://modal.com/docs/guide/webhooks).

This is far from a complete or all-encompassing tool for automating inference server development.
Instead, it's a starting point for end-user customization.
The [Modal Skills](https://modal.com/docs/cli/latest/skills#modal-skills) are included to help agents use and modify this software.

## Example

Here's a (lightly edited) sample output from one round of hill-climbing on AgentX by an agent benchmarking a Qwen 3.8 27B server in a Modal Sandbox.
The agent chose to apply FP8 weight quantization to lower memory-limited decode latency and reported a 45% speedup in output tok/s --
though, as it notes, this is plausibly just due to noise in the benchmark from a very short run.

>  ### What I did
>
>   1. Found your Sandbox — sb-IfcaUu1Boo1eBAMCX5e52I (task ta-01M245D897S2JB90W7FGTSQ39S) under agentx-sandboxes.
>
>   2. Started the server inside it: `setsid nohup python /root/start.py > /root/server.log 2>&1 &`. Weights (52GB base + 3.6GB DFlash draft) were already
>      cached on the `hf-model-cache` volume, so it was healthy in ~3.5 min.
>
>   3. Ran the benchmark twice via `modal run agentx-aiperf/bench.py --url <tunnel> --model Qwen/Qwen3.8-27B --concurrency 1 --num-trajectories 1 --duration-seconds 60 --extra-
>      args "--unsafe-override"`. Workload: 5 requests, ~215k-token agentic coding contexts, 98.6% prefix-cache reads, ~1.35k output tokens/turn.
>
>   4. Edited `/workspace/main.py` in the Sandbox (one line in `EXTRA_SERVER_ARGS`) and re-tested:
>
>       `"--quantization": "fp8",`
>
> ### Results (run IDs in agentx-bench-artifacts: baseline 20260909T224925Z, FP8 20260909T225743Z)
>
```
   Metric                        Baseline (bf16)    FP8        Delta
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━  ━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━
   Inter-token latency           7.85 ms            5.43 ms    −31%
  ────────────────────────────  ─────────────────  ─────────  ──────────────────────
   Output tok/s per user         130.8              189.2      +45%
  ────────────────────────────  ─────────────────  ─────────  ──────────────────────
   E2E output tok/s              75.8               91.7       +21%
  ────────────────────────────  ─────────────────  ─────────  ──────────────────────
   Time to first output token    6.42 s             4.09 s     −36%
  ────────────────────────────  ─────────────────  ─────────  ──────────────────────
   Avg request latency           12.5 s             9.4 s      −25%
  ────────────────────────────  ─────────────────  ─────────  ──────────────────────
   TTFT                          1.75 s             1.85 s     +6% (slightly worse)
  ────────────────────────────  ─────────────────  ─────────  ──────────────────────
   Spec accept length (of 8)     2.55               3.85       +51%
```

> Why FP8 was the right target: the server log showed decode throughput was exactly proportional to spec accept length (44 → 88 → 132 → 176 → 220 tok/s for accept 1–5),
> i.e. each verify step costs a constant ~22.7 ms regardless of how many tokens pass — classic weight-bandwidth-bound decode on 1×H200.
> Halving the 51GB weight read cut step time to ~17ms (accept-1 rate 44 → 59 tok/s).
> TTFT got ~100ms worse because FP8's dynamic-activation path is slightly slower on prefill chunks, but at 99% cache-hit that barely matters.
> The accept-length jump (2.55 → 3.85 with identical prompts) is a numerics side-effect of FP8 on the verify path — worth watching, but here it compounded the win.

## Setup

The only local requirements are Modal and the Python standard library.

If you don't have Modal installed, we recommend using `uvx` to run it in an ephemeral virtual environment:

```bash
# install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# set up Modal access
uvx modal setup
# confirm Modal set up
uvx modal run agentx-aiperf/bench.py --help
```

You can also use `modal` with whatever Python environment management you want
by skipping or replacing the `uvx` prefix in any of the commands here.
Other commands in this repo should work with the ephemeral `uvx` environment,
but elide the prefix for easier copying.
