"""Report connection-request statistics from ``scraped_connections.json``."""

from __future__ import annotations

from datetime import datetime, timedelta

from common import config
from common.browser import read_json


TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
WITHDRAW_AFTER_DAYS = config.WITHDRAW_AFTER_DAYS


def main() -> None:
    try:
        data = read_json(config.CONNECTIONS_FILE, default=None)
    except FileNotFoundError:
        print(f"Error: '{config.CONNECTIONS_FILE.name}' not found. Please check the file exists.")
        return
    except ValueError:
        print(f"Error: '{config.CONNECTIONS_FILE.name}' is not valid JSON.")
        return

    if not isinstance(data, list):
        print(f"Error: '{config.CONNECTIONS_FILE.name}' does not contain a JSON array.")
        return

    total_elements = len(data)
    total_withdrawn_true = sum(1 for item in data if item.get("withdrawn") is True)
    remaining_withdrawal = total_elements - total_withdrawn_true

    timestamps = []
    for item in data:
        if not item.get("timestamp"):
            continue
        try:
            timestamps.append(datetime.strptime(item["timestamp"], TIMESTAMP_FORMAT))
        except ValueError:
            continue

    target_timestamp_str = "N/A"
    latest_timestamp = "N/A"
    status_message = "No valid timestamp found to check"

    if timestamps:
        latest = max(timestamps)
        latest_timestamp = latest
        future_timestamp = latest + timedelta(days=WITHDRAW_AFTER_DAYS)
        target_timestamp_str = future_timestamp.strftime(TIMESTAMP_FORMAT)
        status_message = "You need to wait" if future_timestamp > datetime.now() else "Go ahead"

    print("--- RESULTS ---")
    print(f"Total elements: {total_elements}")
    print(f"Total withdraw true: {total_withdrawn_true}")
    print(f"Remaining withdrawal: {remaining_withdrawal}")
    if isinstance(latest_timestamp, datetime):
        print(f"Latest timestamp: {latest_timestamp.strftime(TIMESTAMP_FORMAT)}")
    else:
        print(f"Latest timestamp: {latest_timestamp}")
    print(f"Target timestamp (latest + {WITHDRAW_AFTER_DAYS} days): {target_timestamp_str}")
    print(f"Status: {status_message}")


if __name__ == "__main__":
    main()