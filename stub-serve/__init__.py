"""Re-export the Modal App so it's visible to modal CLI on module-mode run/serve/deploy."""
from . import main

app = main.app

__all__ = ["app"]
