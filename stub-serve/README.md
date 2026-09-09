You can deploy this "stub" server that implements enough of the OpenAI Chat Completions API to pass AIPerf's AgentX runs with

```bash
modal deploy main.py
```

It uses no GPUs and no LLMs -- just the Python stdlib and Modal!
