The `main.py` file in this folder demonstrates one way to deploy inference on Modal as a [Modal Server](https://modal.com/docs/guide/servers).

It is based on the format used in [Modal Endpoints](https://modal.com/docs/guide/endpoints).

```bash
modal deploy main.py  # from this folder
modal deploy -m serve  # from outside this folder
```
