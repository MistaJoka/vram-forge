#!/bin/zsh
# Daily price update: gather, validate, then publish only what passed.
#
# Trust boundary: the Claude step reads the open web, so it has web tools only and
# no shell, file or MCP access. Its answer is plain data that feed/merge.py checks.
# This script, not the AI, decides what gets committed and pushed.
#
# Set DRY_RUN=1 to stop before committing.

set -euo pipefail

REPO="${0:A:h:h}"
HUNTER_REPO="${HUNTER_REPO:-$HOME/gpuWatching}"
WORK="$REPO/feed/work"
BUDGET_USD="${BUDGET_USD:-1.00}"
MODEL="${MODEL:-claude-sonnet-5-5}"
TODAY="$(date +%F)"

# launchd starts with a bare PATH.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"

# Optional private file holding CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`.
ENV_FILE="$HOME/.config/vram-forge/env"
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"

log() { print -r -- "$(date '+%F %T') $*"; }

mkdir -p "$WORK"
cd "$REPO"

if [[ -z "${DRY_RUN:-}" ]]; then
  git pull --quiet --ff-only origin main
fi

# 1. Reddit signal. A failure here must not stop the tracker prices.
signal_args=()
if (cd "$HUNTER_REPO" && .venv/bin/python -m gpu_hunter.price_signal --out "$WORK/market_signal.json"); then
  signal_args=(--signal "$WORK/market_signal.json")
else
  log "market signal export failed; keeping the previous signal"
fi

# 2. Candidate prices from the open web, in an empty directory with web tools only.
hosts="$(python3 -c 'import json,sys; print(", ".join(json.load(open(sys.argv[1]))["allowed_hosts"]))' feed/rules.json)"
prompt="$(<feed/prompt.md)"
prompt="${prompt//\{\{ALLOWED_HOSTS\}\}/$hosts}"
prompt="${prompt//\{\{TODAY\}\}/$TODAY}"
schema="$(<feed/candidates.schema.json)"
sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

candidate_args=()
if (cd "$sandbox" && claude -p "$prompt" \
      --model "$MODEL" \
      --tools WebSearch WebFetch \
      --allowedTools WebSearch WebFetch \
      --strict-mcp-config \
      --json-schema "$schema" \
      --output-format json \
      --max-budget-usd "$BUDGET_USD" \
      --no-session-persistence </dev/null) > "$WORK/claude_result.json"; then
  if python3 feed/extract.py "$WORK/claude_result.json" "$WORK/candidates.json"; then
    candidate_args=(--candidates "$WORK/candidates.json")
  else
    log "Claude returned no usable result; keeping current prices"
  fi
else
  log "Claude step failed; keeping current prices"
fi

# 3. The gate. Exits non-zero and leaves prices.json alone on malformed input.
python3 feed/merge.py --prices prices.json --rules feed/rules.json \
  "${candidate_args[@]}" "${signal_args[@]}" | tee "$WORK/last_report.json"

if [[ -n "${DRY_RUN:-}" ]]; then
  log "dry run: not committing"
  git --no-pager diff --stat -- prices.json
  exit 0
fi

# 4. Publish. Only prices.json is ever staged by this job.
git add prices.json
if git diff --cached --quiet; then
  log "no change"
  exit 0
fi
git commit --quiet -m "prices: $TODAY"
git push --quiet origin main
log "published"
