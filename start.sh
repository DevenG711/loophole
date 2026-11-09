#!/bin/bash
# Start Loophole:  bash start.sh   (add --demo for the stage demo)
# Keep this Terminal window open while Loophole runs. Closing it quits Loophole.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] || [ ! -f .env ]; then
  echo "Loophole isn't set up yet. Run this first:  bash setup.sh"
  exit 1
fi
exec .venv/bin/python -m loophole.menubar "$@"
