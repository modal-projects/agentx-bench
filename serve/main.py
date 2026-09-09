"""Qwen/Qwen3.8-27B on 1xH200 with SGLang.

Serving metadata:
engine: sglang
base_model_repo_id: Qwen/Qwen3.8-27B
base_model_revision: 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
speculative_draft_model_repo_id: incoai/Qwen3.8-27B-DFlash2
speculative_draft_model_revision: adde41d8fde3a75dc905a7df0bd5088d2a44b5a1
model_family: qwen38

Deployed with MODAL_IMAGE_BUILDER_VERSION=2025.06"""
import modal


MINUTES = 60
DEFAULT_PORT = 8000
HF_IMAGE_ENV = {
    "HF_XET_HIGH_PERFORMANCE": "1",
}

MODEL_NAME = "Qwen/Qwen3.8-27B"
MODEL_REVISION = "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"
SERVED_MODEL_NAME = MODEL_NAME
ROUTING_REGION = "us-west"
REQUIRE_AUTHENTICATION = False
SPECULATIVE_DRAFT_MODEL_NAME = "incoai/Qwen3.8-27B-DFlash2"
SPECULATIVE_DRAFT_MODEL_REVISION = "adde41d8fde3a75dc905a7df0bd5088d2a44b5a1"
SGLANG_IMAGE_TAG = "lmsysorg/sglang:v0.5.19-cu130"
AUTOINFERENCE_UTILS_VERSION = "0.2.3"

GPU_TYPE = "H200"
N_GPUS = 1
GPU = f"{GPU_TYPE}:{N_GPUS}"
CPU = 4
MEMORY_MB = 16384

SCALEDOWN_WINDOW = 5 * MINUTES
TARGET_INPUTS = 32
STARTUP_TIMEOUT = 60 * MINUTES

hf_model_cache = modal.Volume.from_name("hf-model-cache", create_if_missing=True)

EXTRA_IMAGE_ENV = {
    "HF_XET_HIGH_PERFORMANCE": "1",
    "SGLANG_ENABLE_JIT_DEEPGEMM": "0",
    "SGLANG_TIMEOUT_KEEP_ALIVE": "300",
    "TORCHINDUCTOR_COMPILE_THREADS": "1",
}

serving_image = (
    modal.Image.from_registry(SGLANG_IMAGE_TAG)
    .uv_pip_install(
        f"autoinference-utils=={AUTOINFERENCE_UTILS_VERSION}",
    )
    .env(HF_IMAGE_ENV | EXTRA_IMAGE_ENV)
    .run_commands("rm -rf .cache/huggingface")  # tidy up image
)

EXTRA_SERVER_ARGS = {
    "--attention-backend": "fa3",
    "--chunked-prefill-size": "8192",
    "--cuda-graph-max-bs": "32",
    "--disable-cuda-graph-padding": "",
    "--enable-memory-saver": "",
    "--enable-multimodal": "",
    "--enable-weights-cpu-backup": "",
    "--mamba-radix-cache-strategy": "extra_buffer",
    "--mamba-ssm-dtype": "float32",
    "--max-prefill-tokens": "8192",
    "--mem-fraction-static": "0.85",
    "--preferred-sampling-params": "{\"top_k\":20}",
    "--reasoning-parser": "qwen3",
    "--speculative-algorithm": "DFLASH",
    "--speculative-draft-model-revision": SPECULATIVE_DRAFT_MODEL_REVISION,
    "--speculative-num-draft-tokens": "8",
    "--tool-call-parser": "qwen3_coder",
    "--trust-remote-code": "",
}

SERVER_ARGS = {
    "--served-model-name": SERVED_MODEL_NAME,
    "--revision": MODEL_REVISION,
} | EXTRA_SERVER_ARGS


WARMUP_PAYLOAD = {
    "model": SERVED_MODEL_NAME,
    "messages": [{"role": "user", "content": "Reply with JSON facts about Tokyo."}],
    "max_tokens": 64,
    "temperature": 0,
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "city_facts",
            "schema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "population": {"type": "integer"},
                },
                "required": ["city", "population"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
}


app = modal.App(name="qwen3-8-27b-server")


@app.server(
    image=serving_image,
    gpu=GPU,
    cpu=CPU,
    memory=MEMORY_MB,
    min_containers=0,
    scaledown_window=SCALEDOWN_WINDOW,
    port=DEFAULT_PORT,
    routing_region=ROUTING_REGION,
    unauthenticated=not REQUIRE_AUTHENTICATION,
    exit_grace_period=25,
    startup_timeout=STARTUP_TIMEOUT,
    target_concurrency=TARGET_INPUTS,
    volumes={"/root/.cache/huggingface": hf_model_cache},
)
class Server:
    @modal.enter()
    def startup(self):
        from autoinference_utils.endpoint import SGLangEndpoint, warmup_chat_completions

        self.endpoint = SGLangEndpoint(
            model_path=MODEL_NAME,
            worker_port=DEFAULT_PORT,
            tp=N_GPUS,
            speculative_model_path=SPECULATIVE_DRAFT_MODEL_NAME,
            extra_server_args=SERVER_ARGS,
            health_timeout=STARTUP_TIMEOUT,
            health_poll_interval=5.0,
        )
        self.endpoint.start()
        warmup_chat_completions(
            port=DEFAULT_PORT,
            payload=WARMUP_PAYLOAD,
            successful_requests=2,
            request_timeout=60.0,
        )
        print(f"{SERVED_MODEL_NAME} ({GPU}) sglang deployment is ready.")

    @modal.exit()
    def stop(self):
        if hasattr(self, "endpoint"):
            self.endpoint.stop()
