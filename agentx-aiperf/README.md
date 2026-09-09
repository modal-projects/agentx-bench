Run the following command

```bash
TARGET_URL=https://modal-labs-raymond-dev--ep-qwen3-8-27b-server.us-west.modal.direct  # for example
modal run bench.py --url $TARGET_URL
```

to run an AgentX benchmark against the OpenAI-compatible Chat Completions Service at the `TARGET_URL` (mind the `/v1`!).

Results are stored in a [Modal Volume](https://modal.com/docs/guide/volumes)
called `agentx-bench-artifacts`.

After your first run, the Volume will be created and results populated.

```bash
modal volume list  # see available Modal Volumes
# after your first run, it will exist and this command will list its contents
modal volume list --json | grep agentx && modal volume ls agentx-bench-artifacts
```

## Setup

If you don't have Modal installed, we recommend using `uvx` to run it in an ephemeral virtual environment:

```bash
# install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# set up Modal access
uvx modal setup
# confirm Modal set up
uvx modal run bench.py --help
```

You can also use `modal` with whatever Python environment management you want
by eliding or replacing the `uvx` prefix in any of the commands here.
