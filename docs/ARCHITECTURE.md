# Architecture

**Phase 1 design — completed 2026-08-24.** Every decision here respects the hard constraints:
single-core ARM, ~512 MB RAM, runtime dependencies limited to `aiohttp` / `numpy` / `Pillow`,
all I/O async. Inputs: [DATA_SOURCES.md](DATA_SOURCES.md) (phase 0 research, measured numbers),
[CONFIG.md](CONFIG.md) (option semantics). A later session must be able to implement each phase
from this document without re-deriving any decision.

Sources referenced throughout (roles fixed in phase 0):

| Id | Source | Kind | Step | Role |
|---|---|---|---|---|
| `librewxr` | LibreWXR OPERA radar nowcast | tiles (PNG) | 10 min, +10…+60 min | timing precision |
| `icon_eu` | Open-Meteo DWD ICON-EU | point JSON | hourly | reliability baseline |
| `knmi` | Open-Meteo KNMI HARMONIE AROME Europe | point JSON | hourly | independent model, freshest run |
| `metno` | MET Norway Locationforecast 2.0 | point JSON | hourly | provider failover, normally silent |

---

## Module layout

```
custom_components/walk_the_dog/
├── __init__.py        # config entry setup/unload; builds coordinator; forwards platforms
├── const.py           # domain, config/option keys, defaults, intensity class boundaries
├── config_flow.py     # 3-step wizard + options flow (implemented in phase 5)
├── coordinator.py     # WalkCoordinator(DataUpdateCoordinator): polling windows, orchestration
├── schedule.py        # walk-schedule model (3 modes) → next-walk computation; PURE
├── cache.py           # bounded frame/sample cache + persistence via HA Store
├── notifier.py        # notification dispatch, material-change detection, auto-mute check
├── entity.py          # shared entity base: the one service device both entities sit on
├── sensor.py          # the single recommendation sensor
├── switch.py          # enable/disable switch (RestoreEntity)
├── strings.json       # + translations/ (en base, pl priority) — phases 5 and 7
├── sources/
│   ├── __init__.py    # adapter registry: builds the enabled adapter set
│   ├── base.py        # SourceAdapter protocol + SourceSeries / SourceStatus dataclasses
│   ├── librewxr.py    # weather-maps.json + tile fetch + pixel sampling (Pillow + numpy)
│   ├── open_meteo.py  # icon_eu + knmi in ONE HTTP request, returns two SourceSeries
│   └── met_norway.py  # failover adapter: 1 point, If-Modified-Since, honours Expires
└── engine/
    ├── __init__.py
    ├── grid.py        # 10-minute UTC time grid helpers, hourly→slot alignment; PURE
    ├── consensus.py   # per-slot risk + confidence from SourceSeries; PURE
    └── window.py      # window evaluation + recommendation search + material change; PURE
```

Layering rules (enforced by review, testable by import inspection):

- `engine/*` and `schedule.py` are **pure**: no I/O, no `homeassistant` imports, no clock reads —
  `now` is always a parameter. This is what phase 4 unit-tests exhaustively.
- `sources/*` do I/O via one shared `aiohttp.ClientSession` (HA's) and never import `engine`.
- Only `coordinator.py` wires sources → engine → outputs. Entities read coordinator data only.
- `Pillow` and image-related `numpy` usage appear in `sources/librewxr.py` only (phase 0
  deviation: the other sources are point JSON APIs, not tiles).

## Data flow

```
schedule.py: next walk T ──► coordinator: inside active window? ──no──► sleep until window start
                                        │ yes (every 10 min)
                                        ▼
        sources/*.fetch(sample_geometry, now)  ──►  cache.py (skip already-sampled frames)
                                        │
                                        ▼
                    list[SourceSeries] + list[SourceStatus]
                                        │
                                        ▼
        engine/consensus.py: per-slot risk ∈ [0,1] + confidence ∈ [0,1]
                                        │
                                        ▼
        engine/window.py: evaluate [T, T+D]; search earlier/later; Recommendation
                                        │
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
        sensor.py (state+attrs)   notifier.py (at T−earlier_margin, event bus
                                   re-notify on material change)   (walk_the_dog_alert, opt-in)
```

Core data structures (defined in `sources/base.py`, consumed by the engine):

```python
@dataclass(frozen=True)
class SourceSeries:
    source_id: str            # "librewxr" | "icon_eu" | "knmi" | "metno"
    issued_at: datetime       # model run / newest radar frame time (UTC)
    fetched_at: datetime      # when we obtained it (UTC)
    step_s: int               # 600 for librewxr, 3600 for the NWP sources
    slots: tuple[tuple[datetime, float], ...]  # (slot start UTC, intensity mm/h), sorted
    cell_km: float            # effective resolution (phase 0 table)
    reliability: float        # static per-source weight, see Consensus scoring

@dataclass(frozen=True)
class SourceStatus:
    source_id: str
    state: str                # "ok" | "stale" | "failed" | "out_of_range" | "disabled"
    age_s: int | None         # now − issued_at
    contributed: bool
```

Timezone rule: walk times are configured in the HA local timezone and resolved to UTC **per
occurrence** by `schedule.py` (DST-safe — a walk at 07:00 stays at 07:00 local across DST
changes). Everything downstream of `schedule.py` is UTC on a 10-minute grid.

## Frame sampling strategy

All sampling covers a disc of the configured alert radius `r` (default 5 km, see
[Alert radius decision](#alert-radius-decision)) around the configured location.

**LibreWXR (tiles).** Fixed parameters: `z=8`, `size=256`, `color=0` (grayscale ramp, survives
palette decoding exactly), `smooth=0`, `snow=0`, identifying `User-Agent` (mandatory — default
Python UA is rejected with 403, measured in phase 0).

1. Fetch `/public/weather-maps.json` (1 request/cycle). Take the newest `past` frame + all 6
   `nowcast` frames; identify each frame by its `path` string (nowcast frames are re-issued per
   run; `path` is the cache identity, the timestamp alone is not).
2. Project home lat/lon to Web Mercator pixel coordinates at z=8: at 52° N one 256 px tile spans
   ~96.4 km (≈ 0.377 km/px), so the r ≤ 15 km disc spans ≤ 80 px and touches 1 tile in the
   common case, up to 4 at tile corners.
3. Per frame not already in the cache: fetch the tile(s) (317–1 631 bytes each, measured),
   decode with Pillow (paletted 256×256 → 64 KB buffer), convert to a `numpy` uint8 array,
   apply a precomputed boolean disc mask, take the **90th percentile** of the masked pixels
   (robust against single-pixel radar speckle; a max would false-alarm on one noisy cell),
   convert grey → dBZ → mm/h (Marshall–Palmer, boundaries per
   [DATA_SOURCES.md](DATA_SOURCES.md); the grey→dBZ calibration is **`dBZ = grey − 32`**, pinned
   in phase 3 from the AGPL-3.0 LibreWXR source and locked by a fixture test).
4. Store only the resulting float per frame in the cache; **discard tile bytes and arrays
   immediately** (function-local, no references escape). Frames are processed sequentially, so
   peak transient memory is one decoded tile (~64 KB + Pillow overhead), never 7.

Full-frame decoding of a 256×256 tile is unavoidable (PNG is not partially decodable) and
accepted: the buffer is 64 KB, three orders of magnitude under budget. What is *never* done:
fetching tiles beyond the disc, holding more than one decoded tile, or zoom levels above 8.

**Open-Meteo (`icon_eu` + `knmi`).** One `GET /v1/forecast` request with **5 coordinates**
(centre + 4 points at bearings 0°/90°/180°/270° at distance r) ×
`models=icon_eu,knmi_harmonie_arome_europe` × `hourly=precipitation`, `forecast_hours=12`,
`timeformat=unixtime`. Measured at 508 bytes gzipped for 5 points × 3 models in phase 0. Per
model and hour, the sampled intensity is the **max across the 5 points** (few samples of a
smooth NWP field — no speckle risk; max is the conservative choice). mm per hourly step = mm/h
directly; the interpolated `minutely_15` series is never requested (phase 0: carries no
information for Poland and loses precision to quantisation).

**MET Norway (failover only).** One `compact?lat=&lon=` request for the **centre point only** —
its ~10 km cell alone covers any permitted radius, and the terms ask to conserve requests.
`next_1_hours.details.precipitation_amount` mm = mm/h. Mandatory identifying `User-Agent` with
contact info; `If-Modified-Since` on every request; next poll no earlier than the `Expires`
header and never < 10 min apart.

## Consensus scoring

All computation on the shared 10-minute UTC grid (`engine/grid.py`). An hourly source
contributes its hour's value to each of the 6 slots the hour covers (accumulation semantics:
the value for hour H is valid over [H, H+1) — a step function, no interpolation). `librewxr`
contributes only to slots within its +60 min horizon; beyond that it is `out_of_range` for the
slot, not stale.

**Per-source, per-slot weight** `w_i = reliability_i × freshness_i`:

| Source | `reliability` | Rationale |
|---|---|---|
| `librewxr` | 1.00 | radar extrapolation beats NWP inside 0–60 min |
| `knmi` | 0.90 | independent model family, re-run hourly |
| `icon_eu` | 0.80 | 3-hourly runs, coarser cell |
| `metno` | 0.70 | ECMWF-derived, coarsest, failover only |

`freshness_i`: 1.0 while `age ≤ nominal update interval`; linear decay to 0.5 at 3× the
interval; at > 3× the source is **stale and dropped** for the cycle (phase 0 staleness rule —
stale data is excluded, never down-weighted further). Nominal intervals: `librewxr` 10 min,
`knmi` 1 h, `icon_eu` 3 h, `metno` 2 h (observed publication cadence).

`age` is measured from `issued_at`, which each adapter fills with the best truth available
(phase 3): `librewxr` uses the newest past frame's timestamp and `metno` uses
`properties.meta.updated_at`, both real upstream publication times. **Open-Meteo publishes no
model-run timestamp in `/v1/forecast`** (checked 2026-08-25), so its adapters set
`issued_at = fetched_at`; freshness there measures how long ago *we* last got an answer, which is
what actually degrades when the provider stops responding. Recorded in `STATE.md`, phase 3.

**Per-slot risk and confidence.** With the user's intensity threshold θ ∈ {light, moderate,
heavy} mapped to its mm/h lower bound (0.1 / 2.5 / 7.6):

- `vote_i(t) = 1 if intensity_i(t) ≥ θ else 0`, over sources contributing at slot t
- `risk(t) = Σ w_i·vote_i(t) / Σ w_i` ∈ [0, 1] — weighted fraction of sources predicting rain
- `agreement(t) = |2·risk(t) − 1|` — 1 when unanimous either way, 0 at a 50/50 split
- `confidence(t) = agreement(t) × cap(n_t)` where `n_t` = number of contributing sources and
  `cap` is the phase 0 degradation table: 3 → 1.0, 2 → 0.8, 1 → 0.5, 0 → slot has no data
- a slot is **wet** when `risk(t) ≥ 0.5` (strict majority by weight)

Correlated sources never both contribute (phase 0 rule): the adapter registry enables at most
one member of each correlated pair — concretely, `metno` is polled only while Open-Meteo is
failed, so `metno`+`icon_eu`+`knmi` never all vote at once. `n_t` counts actual contributors.

Slot intensity for display (not for the vote): weighted mean of `intensity_i(t)`, classified on
the common scale — shown as "expected intensity" in sensor attributes and notifications.

**Degraded modes** (from phase 0, made precise): 0 contributing sources overall → coordinator
sets the sensor `unavailable`, no notification, ever. Any evaluated slot with `n_t = 1` marks
the result `degraded: true`. Slots with `n_t = 0` inside an evaluated window (walk longer than
horizon) make the window verdict `horizon_limited` and confidence is reported for the covered
slots only, capped at 0.5.

## Walk-window evaluation & recommendation search

Definitions: `T` = next scheduled walk start (UTC), `D` = `average_walk_duration`,
`E` = `earlier_margin` (default 1 h), `L` = `later_margin` (default 30 min). A candidate window
starting at `s` covers the slots in `[s, s + D)`.

- **Window risk** = max slot risk over the window; **window confidence** = min slot confidence
  over the window (weakest link — one uncertain slot makes the verdict uncertain).
- A window is **dry** iff every slot has `risk < 0.5` and every slot has `n_t ≥ 1`.
- **Evaluation:** compute the verdict for the scheduled window `[T, T + D)`.
- **Search** (only when the scheduled window is not dry): candidate starts on the 10-minute
  grid at offsets 0, −10, +10, −20, +20, … bounded by `[T − E, T + L]`. First dry candidate in
  that order wins — nearest wins, and at equal distance **earlier beats later** (the dog waits
  less and nearer-term forecasts are more reliable).
- **Output** (`Recommendation` dataclass): `direction` (`none` = walk as planned / `earlier` /
  `later` / `no_dry_window` / `unknown`), `recommended_start`, scheduled-window risk + confidence +
  peak intensity class, per-source breakdown (each source's verdict over the scheduled window +
  its `SourceStatus`), `degraded` and `horizon_limited` flags. `unknown` is the phase 4 precision
  of the sensor's `unknown` state: no source reaches the scheduled window at all, so no search
  runs and no notification may fire — never reported as good news.

**Material change** (gates re-notification after the first dispatch; any one suffices):

1. `direction` changes (any transition between none / earlier / later / no_dry_window /
   unknown);
2. `recommended_start` moves by ≥ 20 min (2 slots) from the last **notified** value;
3. the scheduled-window verdict flips with hysteresis — wet→dry only when window risk < 0.4,
   dry→wet only when ≥ 0.6 (crossing 0.5 alone never re-notifies, preventing flapping);
4. the scheduled window's peak intensity class changes by ≥ 1 class (e.g. light → moderate).

Comparison is always against the last *notified* recommendation, not the previous cycle.

## Coordinator scheduling & polling windows

**Decision: `lead_time` = 30 min.** Rationale: it covers KNMI HARMONIE's hourly publication
cadence (the decisive fresh input) and gives ≥ 3 LibreWXR cycles of warm cache before the
notification decision at `T − E`; it is also exactly what the phase 0 request budget assumed.

- **Active window** per walk: `[T − E − lead_time, max(T, recommended_start) + D]` — with
  defaults and a 30-min walk that is the budgeted ~2.5 h. A "later" recommendation extends the
  window's end; overlapping windows of consecutive walks merge.
- **Inside the active window:** one update cycle every **10 min** (LibreWXR's frame cadence —
  polling faster cannot observe new data). Per-source fetch cadence within the cycle loop:
  `librewxr` every cycle; **Open-Meteo every 3rd cycle (30 min)** — its freshest model re-runs
  hourly, so a 10-min fetch cadence is ≥ ⅔ guaranteed-identical responses (refinement of the
  phase 0 budget, which allowed every cycle; recorded in STATE.md); `metno` only in failover,
  per its `Expires` (30 min measured). Skipped-fetch cycles reuse the cached series and re-run
  the engine (freshness weights and the time grid still move).
- **Outside the active window: zero polling and zero requests.** The coordinator computes the
  next window start from `schedule.py` and arms a single `async_track_point_in_time` timer.
  No `update_interval`-based idle ticking.
- **Enable switch off:** the timer is cancelled too — no timers, no requests, no cycles. On
  re-enable (or HA start, or options change) the schedule is recomputed immediately and, if
  already inside a window, a cycle runs at once.
- **Cycle grid anchoring** (phase 6): the 10-minute cycles are counted from the **window
  start**, not from the wall clock. Since `lead_time` is a whole number of slots, a cycle
  therefore falls exactly on `T − E` whatever minute the walk itself is scheduled at — which is
  what makes "the notification arrives at `T − E`" true for a 07:15 walk as well as a 07:00 one.
- **Notification dispatch** (`notifier.py`): evaluated on the cycle that lands on `T − E`, fires
  with the freshest coordinator data **only if** the scheduled window is not dry; afterwards
  every cycle until `walk end` re-checks material change. Suppressed entirely by: switch off,
  auto-mute entity not `home`, or 0 contributing sources. A muted alert is suppressed, not
  queued — the decision state advances either way, so coming home does not release a stale
  message. The module is `notifier.py`, not `notify.py`: a file named after a platform inside an
  integration *is* that platform to Home Assistant, and this one is not a notify platform.
- **Provider failover** (phase 0 rule, owned by the adapter registry): Open-Meteo failed on 2
  consecutive cycles → enable `metno`; Open-Meteo healthy twice in a row → disable it again.

## Resource budget

Estimates to be replaced by measurements in phase 8; these are the ceilings tuning must meet.

| Quantity | Budget | Basis |
|---|---|---|
| Transient RAM per update cycle | **< 1 MB typical, 5 MB hard cap** | dominated by one decoded tile (64 KB) + Pillow/numpy overhead; JSON bodies < 100 KB parsed |
| Steady-state RAM (cache + series) | **< 100 KB** | ~50 floats of sampled data + last Open-Meteo response (6.5 KB raw) |
| Persisted storage | **≤ 20 KB** | one HA Store JSON, see Frame cache |
| CPU per cycle (single-core ARM ~1 GHz) | **< 250 ms** | PNG decode of a ≤ 2 KB tile is ms-scale; masking/percentile over 64 KB uint8 is trivial; engine is arithmetic over ≤ ~90 slots |
| CPU outside active windows | **0** (one armed timer) | no polling design |
| HTTP requests, active hour | **≤ 22 typical / ≤ 28 worst** | `librewxr` 6 metadata + ≤ 12 tiles (warm cache: 1–2 new frames/cycle; worst = cold start 7 tiles in cycle 1) ≤ 20/h self-cap; Open-Meteo 2/h; `metno` ≤ 2/h failover-only |
| HTTP requests, daily (4 walks) | **≤ 200** | matches the phase 0 budget table; ≤ 3 % of Open-Meteo's daily allowance under conservative call counting |
| HTTP requests while idle / switch off | **0** | hard requirement |

All numbers stay consistent with the per-provider limits established in
[DATA_SOURCES.md](DATA_SOURCES.md); the Open-Meteo refinement only lowers usage.

## Frame cache

Purpose: never refetch or re-sample an already-processed frame; survive a HA restart inside an
active window without a cold refetch. The cache stores **sampled results, never raw tiles or
responses** — that is what keeps it tiny.

- **LibreWXR sample cache** (the persisted part): map `frame path` (string — the identity that
  changes when a nowcast frame is re-issued) → `{slot_utc, mm_per_h}`. Bound: **32 entries,
  LRU** (12 past + 6 nowcast live frames = 18; 32 gives slack across runs). Persisted with
  `homeassistant.helpers.storage.Store` (version 1, key `walk_the_dog.frame_cache`), written at
  most once per cycle via delayed save; ≤ 20 KB.
- **Open-Meteo cache** (memory only): last parsed per-source series + `fetched_at`; reused for
  the 2 skip-cycles between fetches. Refetching is 508 bytes — not worth persisting.
- **MET Norway cache** (memory only): last series + `Expires` + `Last-Modified` (drives
  `If-Modified-Since`; a 304 refreshes `fetched_at` without a body).
- **Invalidation:** location or radius change clears everything (samples are
  geometry-specific); entries older than 2 h are evicted at each cycle; the persisted store is
  versioned and discarded on schema mismatch.

## Alert radius decision

Phase 0 effective resolutions (measured): LibreWXR/OPERA ~2 km; KNMI 5.5 km; ICON-EU
**4.3 km E-W × 6.95 km N-S** (coarsest regular source); MET Norway ~10 km (failover only).

**Decision: default 5 km, minimum 4 km, maximum 15 km.**

- **Minimum 4 km:** the sampled disc has diameter 8 km ≥ 6.95 km, so it always spans at least
  one full cell of the coarsest regularly-contributing source (ICON-EU) in its coarse (N-S)
  direction — the phase 1 requirement. A smaller radius would pretend spatial precision the
  data does not have.
- **Default 5 km:** the disc then holds ~19 OPERA cells, enough for a stable 90th-percentile
  radar sample; it also absorbs a walker's roaming range (≤ ~2 km in 30 min) plus typical
  nowcast advection error over the +60 min horizon (a few km).
- **Maximum 15 km:** keeps the disc ≤ 80 px inside (usually) one z=8 tile and stays meaningful
  for "will it rain on *my walk*" — beyond that the question is a different one.
- **MET Norway's ~10 km cell does not raise the minimum:** it is normally silent, its
  single-point sample is by definition the value of one full containing cell, and failover mode
  already caps confidence (≤ 0.8 / ≤ 0.5). Recorded as a decision in `STATE.md`.

## Outputs (contract for phases 5–7)

- **Sensor** (exactly one, for the next upcoming walk). State: `ok` | `earlier` | `later` |
  `no_dry_window` | `unknown` (`unknown` = degraded to 0 usable sources or outside any
  computable schedule; HA-`unavailable` when the coordinator has no data at all). Attributes:
  scheduled time, risk, confidence, recommended time, direction, expected intensity class,
  per-source breakdown (verdict, status, age, contributed), data freshness, `degraded`,
  `horizon_limited`, and the attribution strings required by
  [DATA_SOURCES.md](DATA_SOURCES.md). One serialization (`WalkData.payload()`) feeds both the
  sensor attributes and the event payload; the exact schema is in [CONFIG.md](CONFIG.md)
  § Event payload. Scored fields are `null`, never `0`, when no source reaches the scheduled
  window — "we do not know" must never read as "no rain".
- **Switch**: enable/disable alerting, `RestoreEntity`, default on. The coordinator starts in
  the **off** position and the switch restores the real one, so a Home Assistant started with
  alerting disabled makes no request even once.
- **Notification**: via the configured `notify.mobile_app_*` service, per the dispatch rules
  above; content localized (phase 7).
- **Event** `walk_the_dog_alert` (opt-in): fired whenever a notification would fire (even if
  muted by auto-mute — automations may want it), payload = the `Recommendation` serialized,
  plus `muted`. Schema in [CONFIG.md](CONFIG.md) § Event payload.
- **Notification texts** live under the `common` key of `strings.json` with a `notification_`
  prefix and are read at runtime through `homeassistant.helpers.translation`. That is the only
  top-level key hassfest allows for user-facing strings that belong to no form and no entity —
  verified against the real hassfest image, which rejects any other.
