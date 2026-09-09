Run the following command

```bash
TARGET_URL="https://modal-labs-autoinference--stub-agentx-serve.modal.run"  # fake endpoint, see stub-serve
TARGET_MODEL="stub"
modal run bench.py --url $TARGET_URL --model $TARGET_MODEL
```

to run an AgentX benchmark against the OpenAI-compatible Chat Completions Service at the `TARGET_URL`.

For a quick test, run

```bash
modal run bench.py --url $TARGET_URL --model $TARGET_MODEL --duration-seconds 30 --extra-args "--unsafe-override"
```

Results are stored in a [Modal Volume](https://modal.com/docs/guide/volumes)
called `agentx-bench-artifacts`.

After your first run, the Volume will be created and results populated.

```bash
modal volume list  # see available Modal Volumes
# after your first run, it will exist and this command will list its contents
modal volume list --json | grep agentx && modal volume ls agentx-bench-artifacts
```

You can locally copy the entire contents, or individual files, via the CLI with `modal volume get`:

```bash
modal volume get agentx-bench-artifacts /  # copy all files from /
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
