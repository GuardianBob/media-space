"""Run with: python -m mediamanager [--port 8787]"""

import argparse

import uvicorn

from .app import create_app
from .config import HOST


def main() -> None:
    parser = argparse.ArgumentParser(description="Media Manager")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    print(f"Media Manager: http://{HOST}:{args.port}")
    # Loopback unless MEDIA_MANAGER_HOST says otherwise (e.g. in Docker).
    uvicorn.run(create_app(port=args.port), host=HOST, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
