"""Controlled human maintenance client. No database writes or free-text identity bypass.

Provision SHINE_OPERATOR_ID and SHINE_OPERATOR_TOKEN_SHA256 on the API process.
Read the current snapshot through the Web UI; export a complete decision JSON.
This command prompts for the local operator credential without echoing it.
"""
import argparse
import getpass
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--decision", required=True, type=Path)
    args = parser.parse_args()
    token = getpass.getpass("Local operator credential: ")
    response = httpx.post(
        f"http://127.0.0.1:8000/api/v1/operator/jobs/{args.job_id}/review",
        headers={"Authorization": f"Bearer {token}"},
        json=json.loads(args.decision.read_text(encoding="utf-8")), timeout=360,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
