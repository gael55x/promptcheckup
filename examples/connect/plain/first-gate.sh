#!/bin/sh
set -eu
cd "$(dirname "$0")"
python -m promptcheckup.cli.main doctor --no-llm
python -m promptcheckup.cli.main gate --old old-system.md --new prompts/system.md --yes --json result.json
