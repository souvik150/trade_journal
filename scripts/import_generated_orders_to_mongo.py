from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = ROOT_DIR / "data" / "generated_orders"
sys.path.insert(0, str(ROOT_DIR))

import mongo_store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing orders_YYYY-MM-DD.json files.",
    )
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Delete all existing Mongo orders before import.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv(dotenv_path=ROOT_DIR / ".env")
    args = parse_args()

    if not mongo_store.is_available():
        raise SystemExit("MongoDB is not reachable. Check MONGO_URI / container status.")

    order_files = sorted(args.input_dir.glob("orders_*.json"))
    if not order_files:
        raise SystemExit(f"No order files found in {args.input_dir}")

    if args.clear_existing:
        mongo_store.clear_orders()

    imported_dates = 0
    imported_orders = 0
    for file_path in order_files:
        trade_date = file_path.stem.replace("orders_", "")
        orders = json.loads(file_path.read_text())
        mongo_store.save_orders_for_date(date=trade_date, orders=orders)
        imported_dates += 1
        imported_orders += len(orders)

    print(
        f"Imported {imported_dates} dates and {imported_orders} orders into Mongo "
        f"from {args.input_dir}."
    )


if __name__ == "__main__":
    main()
