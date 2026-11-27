#!/usr/bin/env python3
"""FarmFinder 실행 스크립트: `python3 run.py`."""

from __future__ import annotations

import os
import uvicorn

from farmfinder.config import load_dotenv_file


def main() -> None:
    load_dotenv_file()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("farmfinder.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
