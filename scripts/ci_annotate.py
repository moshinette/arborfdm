"""Turn a pytest log into GitHub Actions error annotations.

Annotations are readable through the API even when raw logs are not,
so CI failures can be diagnosed without downloading the log archive.
"""
import sys


def esc(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main(path: str) -> int:
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError as exc:
        print(f"::error::cannot read {path}: {exc}")
        return 0
    failed = [l for l in lines if l.startswith(("FAILED ", "ERROR "))]
    for line in failed[:8]:
        print(f"::error title=pytest::{esc(line[:900])}")
    # The tail shows crashes (aborts, segfaults) that produce no FAILED line.
    print(f"::error title=pytest log tail::{esc(chr(10).join(lines[-40:])[-3500:])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "pytest.log"))
