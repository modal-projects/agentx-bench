The `main.py` file in this folder demonstrates one way to deploy inference on Modal as a [Modal Server](https://modal.com/docs/guide/servers).

It is based on the format used in [Modal Endpoints](https://modal.com/docs/guide/endpoints).

**This Server is unauthenticated!**
To deploy a Modal Server that includes Bearer Auth,
set `unauthenticated = False` and pass in a
[Modal Proxy Token](https://modal.com/docs/guide/webhook-proxy-auth)
where you'd put your LLM service API key.
Requests will be checked for this token in Modal's routing layer.

```bash
modal deploy main.py  # from this folder
modal deploy -m serve  # from outside this folder
```
