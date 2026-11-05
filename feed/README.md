# Daily feed

| File | Purpose |
|---|---|
| `run_daily.sh` | Runs the steps in order and is the only thing that commits |
| `prompt.md` | Instructions for the Claude step |
| `candidates.schema.json` | The answer shape Claude must return |
| `extract.py` | Refuses failed runs before they reach the gate |
| `merge.py` | The gate between candidates and `prices.json` |
| `rules.json` | Allowed sites, trusted monthly trackers, and price-move limits |
| `com.vramforge.daily.plist` | launchd schedule, 7:30 each morning |

The Reddit signal comes from `gpu_hunter`, in a separate private project. It exports numbers, dates and post links only, with no post text. `merge.py` rebuilds the signal from known fields, so anything else is dropped.

## Unattended sign-in

Normal Claude sign-ins expire, so give the job a long-lived token:

```bash
claude setup-token
mkdir -p ~/.config/vram-forge && chmod 700 ~/.config/vram-forge
print -r -- 'export CLAUDE_CODE_OAUTH_TOKEN=<token>' > ~/.config/vram-forge/env
chmod 600 ~/.config/vram-forge/env
```

## Held prices

A held item is listed under the price table with its reason. To accept one, edit `prices.json` by hand after checking the source.
