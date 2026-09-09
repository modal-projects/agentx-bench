This script demonstrates one path for turning an inference server you can deploy on Modal
into an inference server your agents can _develop_ on Modal using [Modal Sandboxes](https://modal.com/docs/guide/sandboxes).

To create a development environment for the sample server in `serve`, run

```bash
python create_sandbox.py --serve-dir ../serve
```

Agents can then manipulate the Sandbox from outside via the [Modal Sandbox SDK](https://modal.com/docs/sdk/py/latest/Sandbox),
e.g. [`Sandbox.exec`](https://modal.com/docs/sdk/py/latest/Sandbox#exec):

```bash
import modal

devbox_id = "sb-abcdefghijklmnop"  # from creation time
devbox = modal.Sandbox.from_id(devbox_id)
devbox.exec("ls")
```

Here's a sample agent action, split into three blocks of reasoning, bash use, and Python code for clarity:

```
Let me do the edit + restart now.
```
```bash
$ cat > /tmp/agentx-work/apply_edit_restart.py <<'EOF'
```
```python
    import asyncio
    import modal


    EDIT = '''
    import re
    p = "/workspace/serve/main.py"
    src = open(p).read()
    assert '"--quantization": "fp8",' not in src, "already applied"
    anchor = ''' + repr('    "--preferred-sampling-params":') + '''
    assert anchor in src, "anchor missing"
    src = src.replace(anchor, '    "--quantization": "fp8",\\n' + anchor, 1)
    open(p, "w").write(src)
    print("edit applied")
    '''


    async def main():
        sb = await modal.Sandbox.from_id.aio("sb-IfcaUu1Boo1eBAMCX5e52I")
        # apply the edit
        p = await sb.exec.aio("python", "-c", EDIT)
        # show the args block
        p = await sb.exec.aio("bash", "-lc", "grep -n '\"--' /workspace/serve/main.py | head -8")
        print(await p.stdout.read.aio())
        # stop old server process group (pid 139 was setsid leader)
        p = await sb.exec.aio("bash", "-lc",
            "kill -TERM -- -139 2>/dev/null; sleep 5; "
            "pkill -f sglang 2>/dev/null; sleep 3; "
            "ps aux | grep -E 'sglang|create_sandbox' | grep -v grep || echo ALL_STOPPED; "
            "nvidia-smi --query-gpu=memory.used --format=csv,noheader")
        print(await p.stdout.read.aio())
        # relaunch fresh
        p = await sb.exec.aio("bash", "-lc",
            "cd /workspace/serve && mv /root/server.log /root/server-run1.log && "
            "setsid nohup python /root/create_sandbox.py run-server > /root/server.log 2>&1 < /dev/null & echo launched pid $!")
        print(await p.stdout.read.aio())


    asyncio.run(main())
```
