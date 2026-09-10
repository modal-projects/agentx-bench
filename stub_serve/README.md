# Deploy a minimal API to test AIPerf AgentX benchmarking

You can deploy this "stub" server that implements enough of the OpenAI Chat Completions API to pass AIPerf's AgentX runs with

```bash
modal deploy main.py  # from this folder
modal deploy -m stub_serve  # from outside this folder
```

It uses no GPUs and no LLMs -- just the Python stdlib and Modal!
