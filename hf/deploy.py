"""Create or update the Hugging Face Space and its private data set.

Usage (token needs write access):
    HF_TOKEN=hf_... python hf/deploy.py <path-to-aw.dump> [space-name]

Secrets are read from deploy/llm-providers.local and sent to the Space as private
secrets; nothing secret is written to the Space's files or to Git.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def main() -> None:
    token = os.environ["HF_TOKEN"]
    dump = Path(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else "acbi"
    api = HfApi(token=token)
    user = api.whoami()["name"]
    data_repo, space_repo = f"{user}/{name}-data", f"{user}/{name}"

    api.create_repo(data_repo, repo_type="dataset", private=True, exist_ok=True)
    api.upload_file(
        path_or_fileobj=str(dump),
        path_in_repo="aw.dump",
        repo_id=data_repo,
        repo_type="dataset",
    )
    print("data set ready:", data_repo)

    api.create_repo(
        space_repo, repo_type="space", space_sdk="docker", private=False, exist_ok=True
    )
    keys = read_env(ROOT / "deploy/llm-providers.local")
    for secret in ("GEMINI_API_KEYS", "LITEROUTER_API_KEY"):
        if keys.get(secret):
            api.add_space_secret(space_repo, secret, keys[secret])
    api.add_space_secret(space_repo, "HF_TOKEN", token)
    api.add_space_variable(
        space_repo,
        "AW_DUMP_URL",
        f"https://huggingface.co/datasets/{data_repo}/resolve/main/aw.dump",
    )

    ref = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    dockerfile = (ROOT / "hf/Dockerfile").read_text(encoding="utf-8")
    dockerfile = re.sub(r"(?m)^ARG REF=.*$", f"ARG REF={ref}", dockerfile, count=1)
    api.upload_file(
        path_or_fileobj=dockerfile.encode(),
        path_in_repo="Dockerfile",
        repo_id=space_repo,
        repo_type="space",
    )
    api.upload_file(
        path_or_fileobj=str(ROOT / "hf/README.md"),
        path_in_repo="README.md",
        repo_id=space_repo,
        repo_type="space",
    )
    print("space:", f"https://huggingface.co/spaces/{space_repo}")
    print("link :", f"https://{user.lower()}-{name}.hf.space")


if __name__ == "__main__":
    main()
