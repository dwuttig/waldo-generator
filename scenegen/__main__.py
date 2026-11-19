import argparse
import logging

import uvicorn

from .app import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="Family hidden-object scene generator")
    ap.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 only on a trusted network; there is no login.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", default=None, help="Data folder (default ./data or $SCENEGEN_DATA)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(args.data), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
