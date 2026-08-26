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
| `chmi` | CHMI CZRAD nowcast (opendata.chmi.cz) | full-domain PNG | 10 min, now…+60 min | second radar, **SW Poland only** |

`chmi` was added after phase 6 (see [DATA_SOURCES.md](DATA_SOURCES.md) § CHMI). It is the first
*regional* source: everything below that says "every source" applies to it only inside its own
coverage box, and outside that box it reports `not_applicable` and is never polled.

---

## Module layout

```
custom_components/walk_the_dog/
├── __init__.py        # config entry setup/unload; builds coordinator; forwards platforms
├── const.py           # domain, config/option keys, defaults, intensity class boundaries
├── config_flow.py     # wizard + options flow: location, schedule, per-walk alerts, params
├── coordinator.py     # WalkCoordinator(DataUpdateCoordinator): polling windows, orchestration
├── schedule.py        # walk-schedule model (3 modes) → Walk identity + next-walk computation; PURE
├── cache.py           # bounded frame/sample cache + persistence via HA Store
├── notifier.py        # notification dispatch, material-change detection, per-walk target + mute
├── entity.py          # shared entity base: the one service device every entity sits on,
│                      #   named from strings.json -> device.service.name (translated)
├── sensor.py          # the single recommendation sensor
├── switch.py          # enable/disable switch (RestoreEntity)
├── strings.json       # + translations/ (en base, pl priority) — phases 5 and 7
├── sources/
│   ├── __init__.py    # adapter registry: builds the enabled adapter set, gates regional ones
│   ├── base.py        # SourceAdapter protocol + SourceSeries / SourceStatus dataclasses,
│   │                  #   shared source metadata tables and the Marshall-Palmer dBZ→mm/h
│   ├── librewxr.py    # weather-maps.json + tile fetch + pixel sampling (Pillow + numpy)
│   ├── chmi.py        # CHMI CZRAD composite + forecast tar + coverage gate (Pillow + numpy)
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
- `Pillow` and image-related `numpy` usage appear in `sources/librewxr.py` and
  `sources/chmi.py` only — the two image sources. The others are point JSON APIs (phase 0
  deviation; `chmi.py` joined after phase 6, recorded in `STATE.md`).

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
    source_id: str            # "librewxr" | "chmi" | "icon_eu" | "knmi" | "metno"
    issued_at: datetime       # model run / newest radar frame time (UTC)
    fetched_at: datetime      # when we obtained it (UTC)
    step_s: int               # 600 for the radar sources, 3600 for the NWP ones
    slots: tuple[tuple[datetime, float], ...]  # (slot start UTC, intensity mm/h), sorted
    cell_km: float            # effective resolution (phase 0 table)
    reliability: float        # static per-source weight, see Consensus scoring

@dataclass(frozen=True)
class SourceStatus:
    source_id: str
    # "ok" | "stale" | "failed" | "out_of_range" | "disabled" | "not_applicable"
    state: str
    age_s: int | None         # now − issued_at
    contributed: bool
```

`not_applicable` was added with `chmi`: the source cannot serve **this location** at all. It is
a permanent property of where the user lives, decided once per geometry, and is deliberately
distinct from `out_of_range` (a *slot* a fetched source does not reach) and `disabled` (a dormancy
the next cycle could end). A `not_applicable` source is never polled.

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

**CHMI (full-domain composites).** Regional, and gated before anything else happens: the adapter
projects the disc's five sample points into the CZRAD data rectangle (E 11.267–19.624,
N 48.047–51.458, inset by 0.3°) and, unless **all** of them fall inside, reports `not_applicable`
and never makes a request. Requiring the whole disc rather than just its centre is what stops a
half-covered disc reading its missing half as "no echo" — outside the data rectangle every pixel is
transparent.

Inside the box, one cycle is:

1. Compute the newest run stamp from the clock. Runs land on a fixed 5-minute grid and publish
   within about a minute, so there is nothing to ask: `now − 2 min`, floored to 5 minutes, then at
   most three runs backwards if one 404s.
2. `GET …/fct_maxz/png/pacz2gmaps3.fct_z_max.{stamp}.ft60s10.tar` — **1 request for the whole
   forecast**: six PNGs at +10…+60 min, 92 KB measured. Each member's filename carries its target
   time, so nothing depends on their order in the archive.
3. `GET …/maxz/png/pacz2gmaps3.z_max3d.{stamp}.0.png` — the observed frame, 19 KB, extending the
   series backwards by one step. It is optional: a run whose forecast arrived but whose observation
   did not is still a usable +10…+60 nowcast.
4. Project home lat/lon into frame pixels through CHMI's published whole-image extent (EPSG:3857,
   linear in longitude and in Mercator northing) at ~1 km/pixel, so a 5 km disc is about 11 × 11 px.
   **Only that rectangle is cropped and converted**, never the whole 680 × 460 composite.
5. Classify each pixel by **exact** match against CHMI's published palette (transparent → level 0 →
   no echo; the grey `#C4C4C4` domain outline and anything unrecognised → no data), take the
   **90th percentile** over the disc mask, and convert level → `4·level` dBZ → mm/h with the same
   Marshall-Palmer inversion LibreWXR uses. A disc that is mostly unrecognised fails the frame
   rather than producing a number.
6. Store the resulting float per frame URL in the shared sample cache; discard the decoded buffer
   immediately.

Peak transient memory is one decoded paletted composite (~313 KB) plus a few hundred RGBA pixels,
which is why the crop happens before the conversion rather than after.

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
| `chmi` | 0.95 **× range factor** | radar too; discounted for quantisation (4 dBZ steps against LibreWXR's 1 dBZ) and then again by distance from the Czech radars — see below |
| `icon_eu` | 0.80 | 3-hourly runs, coarser cell |
| `metno` | 0.70 | ECMWF-derived, coarsest, failover only |

**`chmi` is the one source whose `reliability` is not a constant.** CHMI has exactly two radars,
and a beam climbs and widens with range, so the same instrument is a different measurement at
40 km and at 170 km. `sources/chmi.py`'s `range_factor()` scales the static 0.95 by distance to the
nearest CZRAD radar: full to 120 km, linear decay to 0.5 at 200 km (CHMI's own stated ceiling for
intensity estimation), floored there. Over Bielsko-Biała — 167 km from Skalky, where the beam
centre is ~3.9 km up, against the Polish radar 44 km away that feeds OPERA — that gives 0.67, low
enough that a `librewxr` "wet" outvotes a `chmi` "dry". The adapter puts the adjusted value on the
`SourceSeries`, so the engine needs no special case. Rationale and the measured comparison behind
it are in [DATA_SOURCES.md](DATA_SOURCES.md) § CHMI.

`freshness_i`: 1.0 while `age ≤ nominal update interval`; linear decay to 0.5 at 3× the
interval; at > 3× the source is **stale and dropped** for the cycle (phase 0 staleness rule —
stale data is excluded, never down-weighted further). Nominal intervals: `librewxr` 10 min,
`chmi` 5 min, `knmi` 1 h, `icon_eu` 3 h, `metno` 2 h (observed publication cadence).

`age` is measured from `issued_at`, which each adapter fills with the best truth available
(phase 3): `librewxr` uses the newest past frame's timestamp, `chmi` uses the run stamp it fetched, and
`metno` uses `properties.meta.updated_at` — all real upstream publication times. **Open-Meteo publishes no
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

**`librewxr` and `chmi` are an unmeasured pair, but less alike than they look.** Both are radar
extrapolations and EUMETNET OPERA ingests the Czech radars, which was the reason to worry. Over
Bielsko-Biała, though, the two composites are dominated by *different* instruments — Ramża (PL) at
44 km for OPERA, Skalky (CZ) at 167 km for CZRAD — and a live sweep of the domain found them
differing by roughly 3× in mm/h, with 18 points where OPERA saw rain and CZRAD saw none against 2
the other way. That is not one vote counted twice. Phase 0's rule is still that independence is
established by *measurement*, and this pair has not been measured; the open item stays in
`STATE.md`, alongside the larger question of which of the two is right in absolute terms.

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
  grid at offsets 0, −10, +10, −20, +20, … bounded by `[T − E, T + L]` **and by `now`** — a
  candidate that has already begun is dropped before it is scored. First dry candidate in
  that order wins — nearest wins, and at equal distance **earlier beats later** (the dog waits
  less and nearer-term forecasts are more reliable).
- **The search is bounded by the present** (fixed after the first live test). The window a walk is
  watched in outlives the walk on purpose (see § Coordinator scheduling), so without `now` the
  search happily answers "set off at 21:20" at 22:31 — every margin is measured from `T`, and
  nothing else in the engine knows what time it is. `recommend(now=…)` is therefore not optional
  for the coordinator; omitting it searches the whole margin, past included, which only a test
  evaluating the geometry in isolation should want.
- **Nowcast coverage.** Each window verdict records whether a *radar* source reaches every one of
  its slots (`nowcast_covered`). The radars forecast 60 minutes ahead and the models 12 hours, so
  a walk moved further out than the radar's reach is answered by the models alone: sound about
  *whether* it will rain, imprecise about *when*. A recommendation whose window is not
  nowcast-covered is `provisional` — an early answer the coordinator keeps re-checking, and the
  notification says so.
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

**Actionability** (added after the first live test) gates dispatch *after* material change, and is
about the clock rather than the weather:

1. `is_actionable` — a recommendation with a `recommended_start` expires when that moment does; a
   `no_dry_window`, which names no time of its own, expires when the walk itself begins. Advice the
   user can no longer follow is never sent, however material the change that produced it.
2. `superseded_by_the_clock` — a direction that flipped to `no_dry_window` *only* because the
   previously notified start has passed is not news. Told at 04:00 to go at 04:30 and having
   declined, the user does not need to be told at 04:40 that 04:30 has gone. A window that turns
   wet while it is still ahead is a different matter and is not caught by this rule.

## Coordinator scheduling & polling windows

**Decision: `lead_time` = 30 min.** Rationale: it covers KNMI HARMONIE's hourly publication
cadence (the decisive fresh input) and gives ≥ 3 LibreWXR cycles of warm cache before the
notification decision at `T − E`; it is also exactly what the phase 0 request budget assumed.

- **Active window** per walk: `[T − E − lead_time, max(T, recommended_start) + D]` — with
  defaults and a 30-min walk that is the budgeted ~2.5 h. A "later" recommendation extends the
  window's end, bounded by `T + L + D`; overlapping windows of consecutive walks merge.
- **Why the window outlives the walk.** This is what answers the horizon problem: asked at 12:00
  about a 13:00 walk, only the hourly models can see 14:00, so a "wait until 14:00" is provisional
  when it is given. Staying awake through 14:00 is what lets the radars — which see one hour ahead
  — confirm or correct it in time to matter. The cost is bounded by `L` and counted against the
  per-source hourly budgets, which every adapter polices itself against and the sensor publishes
  as `requests_last_hour` / `requests_hourly_cap`.
- **Inside the active window:** one update cycle every **10 min** — LibreWXR's frame cadence,
  and the grid slot. Per-source fetch cadence within the cycle loop:
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
- **Sprint cadence** in the final approach: **5 min** instead of 10 for the
  `SPRINT_LEAD` = 20 minutes before the moment the user is expected to set off — the recommended
  start if there is one, otherwise `T`. A convective cell can build and arrive well inside one
  10-minute slot, so the stretch before the door is worth watching at the fastest rate any source
  actually publishes at. Two things bound the cost. It only runs where a source *does* publish
  faster than the grid — today that is CHMI's 5 minutes, inside its composite only
  (`SourceRegistry.fast_cadence`); elsewhere the extra cycles would re-score identical bytes. And
  each adapter still gates its own fetch on its own cadence, so LibreWXR (10 min) and Open-Meteo
  (30 min) are unaffected by it: a sprint cycle costs two CHMI requests and nothing else.
  `SPRINT` divides `CYCLE`, so the anchored grid is subdivided rather than replaced and the
  cycle that lands on `T − E` still lands. The sprint can run twice for one walk: once into the
  recommended start, and again into `T` if that suggestion lapses unused.
- **Publication alignment.** The cycle grid is anchored to the walk; a provider's frames are not.
  At a 10-minute cadence the two run at whatever phase they happen to run at, so a frame published
  a minute after a cycle waits nearly a full slot to be looked at. **The data is no staler for
  it** — a fetch always returns the newest frame that exists, so what is read *at* a decision
  moment is the same either way. What waits is the *alert*: a material change contained in that
  frame is announced up to a cadence later than it could be, and for a shower that builds in
  twenty minutes those minutes are the answer. So the coordinator also wakes at
  `issued_at + interval + PUBLISH_SETTLE` of the source whose own publication interval equals the
  cadence being run — LibreWXR at ten minutes, CHMI at five; hourly sources have nothing to align
  to at this timescale and a location with no fast source keeps the plain grid.
  **The alignment may only ever pull a cycle earlier** (`min(grid, aligned)`), which is what makes
  it safe to have: the grid keeps running underneath at its own rate whatever the provider does,
  so a wrong guess about when a frame lands costs one cheap extra cycle and can never cost a cycle
  that was due. `PUBLISH_SETTLE` (60 s) is an estimate, not a measurement — see phase 8.
  The cost is up to **two cycles per cadence** instead of one, and no extra requests: every
  adapter gates its own fetch on its own publication interval (`librewxr` 10 min, `chmi` 5 min,
  Open-Meteo 30 min, `metno` 10 min), so the extra cycle re-scores what is already held.
- **Notification dispatch** (`notifier.py`): evaluated on the cycle that lands on `T − E`, fires
  with the freshest coordinator data **only if** the scheduled window is not dry; afterwards
  every cycle until `walk end` re-checks material change **and actionability** — a window still
  worth watching is not the same as advice still worth sending. Every alert about one walk
  carries the same companion-app `tag`, so a revision replaces its predecessor on the phone
  instead of stacking a second, contradictory message underneath it. Suppressed entirely by: switch off,
  the walk's own mute switch, the away entity that applies to that walk not being `home`, or 0 contributing sources. A muted alert is suppressed, not
  queued — the decision state advances either way, so coming home does not release a stale
  message. The module is `notifier.py`, not `notify.py`: a file named after a platform inside an
  integration *is* that platform to Home Assistant, and this one is not a notify platform.
- **Per-walk targets** (added after the first live test): each walk carries its own list of
  companion-app devices, its own mute switch and its own away entity, so the morning walk and the
  evening walk can belong to different people. The coordinator resolves the walk to a `Walk` — the
  UTC instant **plus** the `(slot key, configured time)` pair that identifies it — and looks the
  settings up by that pair, not by the instant, so a daylight-saving change cannot detach a walk
  from its devices. The two settings compose in opposite directions, which is deliberate: devices
  **add** to the entry-wide `notify_service` (that device always hears, and the union is
  de-duplicated so one phone gets one push), while a walk's away entity **replaces** the entry-wide
  `auto_mute_entity` for that walk. Adding recipients is what a per-walk list is for; two people
  whose absence both silence the same walk is not. The mute switch remains the only way to silence
  a walk outright. Details in [CONFIG.md](CONFIG.md) § Per-walk alerts.
- **Confirmation before setting off** (optional, `confirm_margin_min`, default off): one short
  message `confirm_margin` before the departure moment, and only if something was already said
  about this walk. It has two shapes — *the plan still stands*, and *the rain has gone, walk at
  the normal time*. The second is the one that earns the feature: `later` relaxing to `none` is
  not an alert direction, so silence would leave the user waiting for a window that stopped being
  necessary. Sent once per walk; an alert dispatched at or after the moment counts as it.
- **Closing a walk** (`walk_the_dog.walked`, and the *Already went* button on the push): the
  coordinator records the occurrence in `_dismissed`, `_resolve_walk` skips it, and the watch
  window ends there — no more advice and no more requests for a decision nobody will make. The
  button carries the walk's UTC start inside its action identifier, because that is the one field
  both companion apps hand back, and a leftover notification from yesterday must not close today's
  walk. Tapping it also pushes `clear_notification` to the walk's other devices, which would
  otherwise go on showing advice about a walk that is over. In memory only: a restart inside the
  window resurrects the walk, which is the safe way round to be wrong.
- **Provider failover** (phase 0 rule, owned by the adapter registry): Open-Meteo failed on 2
  consecutive cycles → enable `metno`; Open-Meteo healthy twice in a row → disable it again.

## Resource budget

Estimates to be replaced by measurements in phase 8; these are the ceilings tuning must meet.

| Quantity | Budget | Basis |
|---|---|---|
| Transient RAM per update cycle | **< 1 MB typical, 5 MB hard cap** | dominated by one decoded image: a 256×256 LibreWXR tile (64 KB) or one paletted CHMI composite (680×460 = 313 KB), plus the 92 KB forecast archive held while it is expanded, plus Pillow/numpy overhead; JSON bodies < 100 KB parsed |
| Steady-state RAM (cache + series) | **< 100 KB** | ~50 floats of sampled data + last Open-Meteo response (6.5 KB raw) |
| Persisted storage | **≤ 20 KB** | one HA Store JSON, see Frame cache |
| CPU per cycle (single-core ARM ~1 GHz) | **< 250 ms** | PNG decode of a ≤ 2 KB tile is ms-scale; masking/percentile over 64 KB uint8 is trivial; engine is arithmetic over ≤ ~90 slots |
| CPU outside active windows | **0** (one armed timer) | no polling design |
| Update cycles, active hour | **6 typical, ≤ 24 worst** | one per 10-minute slot, doubled where publication alignment applies, and doubled again over the two 20-minute sprints. A cycle without a fetch is arithmetic over ≤ ~90 slots — the image decode, which is what the CPU budget above is about, only happens when an adapter actually fetches |
| HTTP requests, active hour | **≤ 22 typical / ≤ 28 worst** outside CHMI's box; **≤ 58** inside it | `librewxr` 6 metadata + ≤ 12 tiles (warm cache: 1–2 new frames/cycle; worst = cold start 7 tiles in cycle 1) ≤ 20/h self-cap; Open-Meteo 2/h; `metno` ≤ 2/h failover-only; `chmi` ≤ 12 fetches/h at its own 5-minute rate = ≤ 24 requests/h, ≤ 30/h capped, and only where it is applicable. **Requests follow the sources' publication rates, not the cycle count**: each adapter gates its own fetch, so neither the sprint nor the publication alignment adds one |
| HTTP requests, daily (4 walks) | **≤ 200**, or **≤ 380** inside CHMI's box | matches the phase 0 budget table plus the CHMI deviation recorded in `STATE.md`; ≤ 3 % of Open-Meteo's daily allowance under conservative call counting |
| HTTP requests while idle / switch off | **0** | hard requirement |

All numbers stay consistent with the per-provider limits established in
[DATA_SOURCES.md](DATA_SOURCES.md); the Open-Meteo refinement only lowers usage.

## Frame cache

Purpose: never refetch or re-sample an already-processed frame; survive a HA restart inside an
active window without a cold refetch. The cache stores **sampled results, never raw tiles or
responses** — that is what keeps it tiny.

- **Frame sample cache** (the persisted part): map `frame path` (string — the identity that
  changes when a nowcast frame is re-issued) → `{slot_utc, mm_per_h}`. Shared by both image
  sources — a path for LibreWXR, the full URL for CHMI — so their entries cannot collide. Bound:
  **48 entries, LRU** (LibreWXR 12 past + 6 nowcast = 18, CHMI one observed frame per 5-minute run;
  48 gives slack across runs). Persisted with `homeassistant.helpers.storage.Store` (version 1, key
  `walk_the_dog.frame_cache`), written at most once per cycle via delayed save; ≤ 20 KB.
  **It cannot help CHMI's forecast**: a new run publishes every 5 minutes, so every cycle's archive
  is genuinely new data rather than a cache miss worth avoiding — which is why that archive being a
  single request matters more than caching it would.
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
