"""Download the model files the bundled workflows need into a ComfyUI models folder.

Usage: python download_models.py <models-dir> [models.json]
Skips files that already exist. Set HF_TOKEN if Hugging Face asks for a login.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "scenegen-model-downloader"})
    token = os.environ.get("HF_TOKEN")
    if token and "huggingface.co" in url:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while chunk := r.read(8 * 1024 * 1024):
            f.write(chunk)
            done += len(chunk)
            if time.monotonic() - last > 10:
                pct = f"{done / total:.0%}" if total else f"{done / 1e9:.1f} GB"
                print(f"  {dest.name}: {pct}", flush=True)
                last = time.monotonic()
    tmp.replace(dest)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    manifest = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).with_name("models.json"))
    failed = []
    for m in json.loads(manifest.read_text()):
        dest = root / m["dir"] / m["file"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"✓ {m['dir']}/{m['file']} already present")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"↓ {m['dir']}/{m['file']}", flush=True)
        for attempt in range(3):
            try:
                download(m["url"], dest)
                print(f"✓ {m['file']}")
                break
            except Exception as e:  # noqa: BLE001
                print(f"  attempt {attempt + 1} failed: {e}", flush=True)
                time.sleep(5 * (attempt + 1))
        else:
            failed.append(m["file"])
    if failed:
        print("Could not download: " + ", ".join(failed) + "\nFix the URL in models.json or place the files manually.")
        return 1
    print("All models present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
