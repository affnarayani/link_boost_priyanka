"""Shuffle the LinkedIn topic list in place, writing a new shuffled file."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from common import config


def randomize_topics(input_file: str, output_file: str) -> int:
    """Shuffle ``input_file`` and write the result to ``output_file``."""
    source = Path(input_file)
    if not source.is_absolute():
        source = config.JSON_DIR / source
    target = Path(output_file)
    if not target.is_absolute():
        target = config.JSON_DIR / target

    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Error: '{source}' not found. Please check the file is in the right folder.")
        return 1
    except ValueError:
        print("Error: the JSON file is malformed.")
        return 1

    if not isinstance(data, list):
        print("Error: the JSON file does not contain an array.")
        return 1

    random.shuffle(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

    print(f"Success: all elements shuffled and saved to '{target}'.")
    return 0


if __name__ == "__main__":
    sys.exit(randomize_topics("priyanka_linkedin_topics.json", "shuffled_linkedin_topics.json"))