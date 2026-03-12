"""Upload Cursor billing CSV to hosted-edge and fetch calibration output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=False, help="Path to Cursor billing CSV export")
    parser.add_argument(
        "--edge-url",
        default="http://127.0.0.1:8080",
        help="Hosted-edge base URL",
    )
    parser.add_argument("--api-key", required=True, help="Hosted-edge billing import API key")
    parser.add_argument(
        "--batch-id",
        default=None,
        help="If provided, fetch existing reconciliation summary instead of uploading CSV.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate CSV only")
    parser.add_argument(
        "--candidate-source",
        default="auto",
        choices=["auto", "hosted_edge_ledger", "local_session_db"],
        help="Source for reconciliation candidates.",
    )
    parser.add_argument(
        "--local-session-db",
        default=None,
        help="Optional path to local MCP sessions.db when candidate-source uses local_session_db.",
    )
    parser.add_argument("--imported-by", default="operator", help="Operator identifier")
    parser.add_argument(
        "--output-calibration",
        default=None,
        help="Optional path to write calibration artifact JSON payload",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    with httpx.Client(timeout=30.0) as client:
        if args.batch_id:
            response = client.get(
                f"{args.edge_url.rstrip('/')}/v1/cursor-billing/batches/{args.batch_id}",
                headers={"x-l6e-api-key": args.api_key},
            )
        else:
            if not args.csv:
                raise SystemExit("--csv is required unless --batch-id is provided")
            csv_path = Path(args.csv)
            csv_content = csv_path.read_text(encoding="utf-8")
            payload = {
                "csv_content": csv_content,
                "source_filename": csv_path.name,
                "dry_run": args.dry_run,
                "imported_by": args.imported_by,
                "candidate_source": args.candidate_source,
            }
            if args.local_session_db:
                payload["local_session_db_path"] = args.local_session_db
            response = client.post(
                f"{args.edge_url.rstrip('/')}/v1/cursor-billing/import",
                json=payload,
                headers={"x-l6e-api-key": args.api_key},
            )
    response.raise_for_status()
    result = response.json()
    print(json.dumps(result, indent=2, sort_keys=True))
    artifact = result.get("calibration_artifact")
    if args.output_calibration and isinstance(artifact, dict):
        raw_payload = artifact.get("payload_json")
        if isinstance(raw_payload, str):
            Path(args.output_calibration).write_text(raw_payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
