# VRAM Forge

An AI hardware platform in one browser page: a VRAM calculator, price tracking, a component explainer, and a 3D build teardown. No install or build step.

## How prices update

Once a day, `feed/run_daily.sh` runs on a home machine:

1. **Reddit signal:** [gpu_hunter](feed/README.md) exports median asking prices from single-item posts. These are shown separately and never change a headline price.
2. **Tracker prices:** a headless Claude run reads an allowlist of trackers and retailers, with web tools only.
3. **The gate:** `feed/merge.py` decides what is published. A price needs a valid date and an allowlisted https source. A move over 25% needs two sites agreeing, and a move over 60% is held for review.
4. **Publish:** only `prices.json` is committed and pushed.

Items that fail keep their last good price and date, and the page lists why. If `prices.json` can't load, the page falls back to its built-in snapshot and says so.

## Limits

- The gate checks that a price is plausible and well sourced. It doesn't re-read the cited page, so a misread within the allowed move can still get through.
- Items with no confirmed price that day keep their old date, so check the date next to each price.
- Reddit figures are asking prices, not sold prices.

## Run the tests

```bash
npm ci
npx playwright install chromium-headless-shell
npm test
```

Preview the daily job without publishing, which spends up to `BUDGET_USD` on the Claude step:

```bash
DRY_RUN=1 feed/run_daily.sh
```
