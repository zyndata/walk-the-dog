# Data sources

> **Stub — filled in during phase 0** (see [PLAN.md](../PLAN.md)). Hard requirement for every
> source: publicly documented/official API publishing **ready-made forecast frames ≥ 30 min
> ahead** covering all of Poland. Radar-only (past) frames disqualify. Verify every claim against
> current official documentation and note the date checked.

## Candidates to evaluate

Starting list — unverified, extend during research; none of these are accepted yet:

- IMGW-PIB (Polish national met service) — public data offerings, nowcast products
- RainViewer — nowcast API
- Open-Meteo — free weather API with minutely/15-min precipitation
- DWD (German met service) — RADOLAN/ICON nowcast products, coverage of Poland to verify
- MET Norway — nowcast product (coverage area to verify — likely Nordic-only, confirm rejection properly)
- Any other publicly documented nowcast provider discovered during research

## Comparison table

TODO (phase 0): one row per candidate; columns: forecast frames ≥ 30 min? / horizon & step /
coverage / effective resolution (km per cell) / update frequency & latency / format / intensity
encoding & mapping to light-moderate-heavy / licence / attribution / rate limits / API key /
cost / stability / date checked.

## Ranked recommendation

TODO (phase 0): 2–4 recommended sources with rationale; rejected candidates with reasons.

## Intensity mapping

TODO (phase 0): per recommended source, the exact mapping from its native encoding onto the
common light / moderate / heavy scale.

## Fallback strategy

TODO (phase 0): behavior when a source is down or stale (degraded consensus, confidence impact,
minimum viable source count).

## Request budget

TODO (phase 0): worst-case requests/hour per source and total, shown to fit inside every
provider's documented limits.
