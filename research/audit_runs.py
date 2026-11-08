"""Read-only inventory of local step captures. Never treats model claims as success."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

STEP_FILE = re.compile(r"step(\d+)(?:(_ui)\.txt|\.(png|jpg|jpeg))$")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(root: Path) -> dict:
    if not root.is_dir():
        raise ValueError("runs root must exist and be a directory")
    runs = []
    for directory in sorted(root.iterdir()):
        if directory.is_symlink() or not directory.is_dir():
            continue
        frames = {}
        for path in sorted(directory.iterdir()):
            match = STEP_FILE.fullmatch(path.name)
            if path.is_symlink() or not path.is_file() or not match:
                continue
            step = int(match[1])
            kind = "ui" if match[2] else "image"
            frame = frames.setdefault(step, {"step": step, "images": [], "ui": []})
            frame["images" if kind == "image" else "ui"].append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            })
        if not frames:
            continue
        ordered = [frames[s] for s in sorted(frames)]
        # Exact image-file occurrences for hypothetical one-request-per-observation
        # replay. Actual loops may request again without observing, or finish early.
        histories = []
        full = recent = full_bytes = recent_bytes = 0
        for frame in ordered:
            histories.extend(frame["images"])
            selected = histories[-2:]
            full += len(histories)
            recent += len(selected)
            full_bytes += sum(f["bytes"] for f in histories)
            recent_bytes += sum(f["bytes"] for f in selected)
        runs.append({
            "run_id": directory.name,
            "observed_steps": len(ordered),
            "image_files": sum(len(f["images"]) for f in ordered),
            "ui_files": sum(len(f["ui"]) for f in ordered),
            "log_present": (directory / "log.txt").is_file(),
            "task_success": None,
            "provider_usage": None,
            "actual_cost": None,
            "hypothetical_replay": {
                "requests": len(ordered),
                "full_image_file_occurrences": full,
                "recent2_image_file_occurrences": recent,
                "full_referenced_image_bytes": full_bytes,
                "recent2_referenced_image_bytes": recent_bytes,
            },
            "frames": ordered,
        })
    return {
        "schema_version": 1,
        "evidence_type": "local_file_inventory",
        "runs": runs,
        "limitations": [
            "Immediate run directories and step capture files only; not a device inventory.",
            "Replay counts are hypothetical; bytes and image counts are not tokens or cost.",
            "Task outcomes, fault labels, model versions and original API usage are unknown.",
            "Hashes identify local bytes; no screenshot content is included.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.runs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
