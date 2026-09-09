# Run AgentX and develop/deploy inference servers on Modal

This repository implements a basic architecture for running AgentX and developing and deploying inference servers on [Modal](https://modal.com/).

That includes:

- running the AgentX benchmark with [AIPerf](https://github.com/ai-dynamo/aiperf) in a [Modal Function](https://modal.com/docs/guide/functions), storing results in a [Modal Volume](https://modal.com/docs/guide/volumes), and retrieving them with the `modal volume` CLI (`./agentx-aiperf`)
- serving performant inference on Modal with a [Modal Server](https://modal.com/docs/guide/servers) (`./serve`)
- hosting a development environment for the inference server in a [Modal Sandbox](https://modal.com/docs/guide/sandboxes) (`./sandbox`).

It also includes a minimal test server, mathcing the expected API but without all the slow and expensive work, in `./stub_serve`.
This is deployable on Modal as a simple [Web Function](https://modal.com/docs/guide/webhooks).

The [Modal Skills](https://modal.com/docs/cli/latest/skills#modal-skills) are included to help agents develop this software.

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
by eliding or replacing the `uvx` prefix in any of the commands here.
