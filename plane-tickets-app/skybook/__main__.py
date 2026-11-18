"""Start the SkyBook web server: ``python3 -m skybook``.

Listens on 0.0.0.0. The port comes from the ``PORT`` environment variable
(default 8000).
"""

from __future__ import annotations

import os

import uvicorn

from .app import create_app


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
