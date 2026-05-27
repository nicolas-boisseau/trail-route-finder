"""Upload a generated GPX as a Course on Garmin Connect, via gccli.

Workflow:
  1. `gccli courses import <gpx> --name <name> --type <type>` → creates the course
  2. Optionally `gccli courses send <course-id> <device-id>` → pushes to the watch
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


VALID_TYPES = ["running", "trail_running", "hiking", "cycling", "mountain_biking", "gravel_cycling"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gpx", help="Path to the GPX file to upload as a Garmin Course")
    ap.add_argument("--name", help="Course name (defaults to the GPX file stem)")
    ap.add_argument("--type", default="trail_running", choices=VALID_TYPES)
    ap.add_argument("--send-to", help="Device ID to push the course to (use 'gccli devices list')")
    ap.add_argument("--public", action="store_true", help="Make the course public (default: private)")
    args = ap.parse_args()

    gpx = Path(args.gpx).resolve()
    if not gpx.exists():
        print(f"GPX not found: {gpx}", file=sys.stderr)
        sys.exit(2)

    if not shutil.which("gccli"):
        print("gccli not on PATH. Install/activate the gccli skill first.", file=sys.stderr)
        sys.exit(3)

    name = args.name or gpx.stem
    cmd = [
        "gccli", "courses", "import", str(gpx),
        "--name", name,
        "--type", args.type,
        "--json",
    ]
    if args.public:
        cmd.extend(["--privacy", "1"])
    print("→", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.exit(r.returncode)
    print(r.stdout)

    if args.send_to:
        try:
            data = json.loads(r.stdout)
            course_id = data.get("courseId") or data.get("id")
        except (json.JSONDecodeError, AttributeError):
            print("Could not parse course id from gccli output; skipping device push.", file=sys.stderr)
            sys.exit(0)
        if not course_id:
            print("No courseId in gccli response; skipping device push.", file=sys.stderr)
            sys.exit(0)
        send_cmd = ["gccli", "courses", "send", str(course_id), args.send_to]
        print("→", " ".join(send_cmd))
        rs = subprocess.run(send_cmd)
        sys.exit(rs.returncode)


if __name__ == "__main__":
    main()
