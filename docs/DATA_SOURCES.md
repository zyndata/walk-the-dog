# Data sources

> **Reading this document:** [What is actually wired in](#what-is-actually-wired-in) is the
> current state — the four sources the integration ships with. Everything after it is the phase 0
> research that chose them.

**Phase 0 research — completed 2026-08-24.** Every claim below was verified against current
official documentation or against the live API on that date; the per-claim date is noted as
*checked 2026-08-24* unless stated otherwise. Where a fact could be established by querying the
live API, that was done in preference to trusting documentation, and the observation is marked
**(measured)**.

Hard requirement applied to every candidate: a publicly documented or officially open API that
publishes **ready-made forecast frames ≥ 30 minutes ahead** covering **all of Poland**. Radar-only
past frames disqualify — this integration never computes cloud movement itself.

---

## Headline findings

1. **RainViewer no longer qualifies.** Its public Weather Maps API is now past-frames-only. The
   documented root object contains a single `past` array ("the past 2 hours … with 10-minute
   intervals"), the word *nowcast* does not appear in the documentation at all, and the live
   `radar.nowcast` array is present but **empty** on every request. This was the obvious source at
   bootstrap; it is now rejected. **(measured 2026-08-24)**
2. **IMGW-PIB does not publish a usable nowcast.** Its public data API exposes past radar
   composites and COSMO GRIB files only, both with disqualifying problems (below).
3. **No free source gives Poland native sub-hourly NWP precipitation.** DWD ICON-D2 — the only
   model on Open-Meteo with native 15-minutely output — stops at roughly **19° E**, i.e. it does
   not even reach Warsaw. **(measured)** Everything else covering Poland is hourly, and
   Open-Meteo's `minutely_15` for those models is interpolated from the hourly series.
4. **Exactly one free, publicly documented, radar-based nowcast covers Poland: LibreWXR**, an
   open-source EUMETNET OPERA-based service. It provides +10…+60 min frames at a 10-minute step.
   It is young and carries no SLA, so the architecture must degrade gracefully without it.
5. Consequence for the architecture: the source mix is **one tile source plus two point/grid JSON
   sources**, not the all-tiles design assumed at bootstrap. Pixel sampling and `Pillow` are needed
   for LibreWXR only. This deviation is recorded in `STATE.md`.

---

## What is actually wired in

The rest of this document is the phase 0 research — twenty candidates evaluated, most of them
rejected. This section is the short answer: **what the integration ships with today**, and what
each source is worth to it. Every figure here is a constant in the code, named in the last row so
the table can be checked rather than trusted.

**Four of the five sources cover all of Poland; CHMI is regional.** LibreWXR and CHMI are
radar; ICON-EU, KNMI HARMONIE and MET Norway are numerical models. That is why they carry
different roles rather than being averaged.

CHMI was added after phase 6. The trail started from the app analysis in
[SOURCE_meteor_androworks.md](SOURCE_meteor_androworks.md), but the source that
shipped is CHMI's own open-data service — see
[its own section](#chmi--czrad-regional-radar) below.

### Time windows and intervals

| | **LibreWXR** | **CHMI** | **ICON-EU** | **KNMI HARMONIE** | **MET Norway** |
|---|---|---|---|---|---|
| Source id | `librewxr` | `chmi` | `icon_eu` | `knmi` | `metno` |
| What it is | OPERA radar, extrapolated | **CZRAD radar, extrapolated** | DWD model | KNMI AROME model | ECMWF-driven model |
| Role | timing precision | **second radar, SW Poland only** | reliability baseline | independent model, freshest run | provider-level failover |
| Covers | all of Poland | **only its own box** | all of Poland | all of Poland | all of Poland |
| **Forecast horizon** | now … **+60 min** | now … **+60 min** | **+12 h** | **+12 h** | **+12 h** |
| **Step of the series** | **10 min** (7 frames) | **10 min** (7 frames) | 60 min | 60 min | 60 min |
| Publisher's cadence | every 10 min | **every 5 min** (measured) | every 3 h | **every 1 h** | ~every 2 h |
| **How often we fetch** | every cycle (10 min) | every cycle (10 min) | every 30 min | every 30 min (same request) | ≥ 10 min, **only while Open-Meteo is failing** |
| Dropped as stale after | 30 min | 15 min | 9 h | 3 h | 6 h |

Fetch cadences and horizons live in the adapters (`sources/librewxr.py`, `sources/chmi.py`,
`sources/open_meteo.py`, `sources/met_norway.py`); the publisher cadences and the 3× staleness rule
live in `UPDATE_INTERVAL_S` and `STALE_FACTOR` in `sources/base.py`.

### Resolution, weight and cost

| | **LibreWXR** | **CHMI** | **ICON-EU** | **KNMI HARMONIE** | **MET Norway** |
|---|---|---|---|---|---|
| Effective cell at 52° N | **~2 km** | **1 km** (published, and confirmed by the frame extent) | 6.95 km N-S | 5.5 km | ~10 km |
| Consensus reliability weight | **1.00** | 0.95 **× range factor** (0.67 at Bielsko-Biała) | 0.80 | 0.90 | 0.70 |
| How the disc is sampled | every pixel inside the disc, p90 | every pixel inside the disc, p90 | 5 points (centre + N/E/S/W) | 5 points, **one shared request** | 1 point (centre) |
| Wire format | PNG tiles, z=8, ~376 m/pixel | one 680 × 460 PNG composite + one tar of 6 | JSON | JSON | JSON |
| Self-imposed request ceiling | 20 /h | 18 /h | 6 /h (shared with KNMI) | — | 2 /h |
| API key | none | none | none | none | none (identifying `User-Agent` mandatory) |
| Transport | HTTPS | HTTPS | HTTPS | HTTPS | HTTPS |
| Licence | CC BY 4.0 (OPERA) | CC BY 4.0 (CHMI) | CC BY 4.0 (Open-Meteo + DWD) | CC BY 4.0 (Open-Meteo + KNMI) | CC BY 4.0 / NLOD |

Cell sizes and weights are `CELL_KM` and `RELIABILITY` in `sources/base.py`; the attribution
strings the sensor must show are `ATTRIBUTION` in the same module.

### What that means in practice

- **The radar only reaches an hour ahead.** The search window is `earlier_margin` (60 min by
  default) + the walk (30 min) + `later_margin` (30 min), so up to ~2 h. Beyond +60 min only the
  hourly models score the window, and the sensor raises `horizon_limited` to say so.
- **MET Norway is silent by default.** It wakes only after two consecutive Open-Meteo failures,
  because it correlates 0.61 with KNMI — polling it alongside would add a dependent vote dressed
  up as an independent one.
- **ICON-EU and KNMI cost one HTTP request between them**, covering both models and all five
  sample points.
- **CHMI is silent for most of Poland.** It answers only when the whole sampled disc lies inside
  the CZRAD composite; anywhere else it reports `not_applicable`, makes no request and is simply
  absent from the vote. For a user in Warszawa the integration behaves exactly as it did before it
  existed.
- **CHMI's whole forecast costs one request.** The +10…+60 min frames ship as a single tar per
  run, so a cycle is 2 requests for a 7-slot radar series.
- **Zero requests outside a walk window**, and zero while the alerting switch is off. The whole
  budget is ≤ 28 requests/hour inside a window outside CHMI's box, ≤ 58 inside it — the
  difference being CHMI's own 5-minute publication rate. Every adapter gates its own fetch on its
  own publication interval, so the request count follows the providers' rates and not how often
  the coordinator happens to wake (ARCHITECTURE.md § Coordinator scheduling).
- **No Polish radar is in the set.** IMGW-PIB publishes observations only (11 h stale when
  measured) and RainViewer stopped serving forecast frames publicly. Both are in
  [Rejected candidates](#rejected-candidates) with the evidence. CHMI's radars are Czech, not
  Polish; they reach south-western Poland because the CZRAD composite has a margin around
  Czechia.

---

## CHMI — CZRAD, regional radar

Added after phase 6, outside the phase plan, at the maintainer's request. It is the only source
here that was **not** part of the phase 0 evaluation.

### How this ended up at CHMI and not at Meteor

The request was to wire in the Meteor app's radar, analysed in
[SOURCE_meteor_androworks.md](SOURCE_meteor_androworks.md). Probing the live service settled it
differently **(measured 2026-08-26)**:

- `http://meteor.androworks.org/v2/feed` answers, but returns **`Content-Length: 0`** — the source
  note's "the response body *is* the newest frame" does not hold. Retried with a stale
  `X-Frame-Date`, with a `?date=` parameter, and on the `11.` and `111.` frame hosts: always empty.
- Every documented frame path — `/v2/czrad-z_max3d_masked/…` and `/v2/czrad-z_max3d_fct_masked/…` —
  answers **404**, on `meteor.androworks.org` and on all three `*.fbck` hosts.
- `X-Next-Query` is **milliseconds until the next poll** (observed 45 935 and ~240 000 against a
  5-minute publication cadence), not epoch milliseconds. `X-Future-Levels` is never sent at all.

What the note *did* get right is the product identity: `pacz2gmaps3.z_max3d` is CHMI's own file
naming. CHMI publishes these products itself, on **`opendata.chmi.cz`**, over HTTPS, with a
specification document and a published colour scale. That is the better upstream the note itself
recommended, so the adapter went straight there and Meteor is credited only as the discovery path.

### What it is

`MAX_Z` — column-maximum reflectivity from the CZRAD network (radars Brdy-Praha and Skalky) — plus
`FCT_MAX_Z`, an extrapolation nowcast of the same field at +10…+60 min in 10-minute steps. Both are
pre-rendered PNGs this project only decodes and samples. It is a **second radar network with its own
extrapolation** over the area the maintainer actually walks in, at no key cost.

Endpoints (5-minute run cadence, stamps in UTC):

| Purpose | URL |
|---|---|
| Observed composite | `…/composite/maxz/png/pacz2gmaps3.z_max3d.{stamp}.0.png` |
| Forecast, all 6 frames | `…/composite/fct_maxz/png/pacz2gmaps3.fct_z_max.{stamp}.ft60s10.tar` |
| Colour scale | `…/radar/scl/scl-dbz-mmh.png` |
| Specification | `…/radar/radar_description_en.pdf` |

There is no feed and none is needed: runs land on a fixed 5-minute grid, so the adapter computes the
newest stamp from the clock (allowing ~2 min for publication) and steps back at most three runs.
**One cycle is two requests** — the tar carries the whole forecast.

### Intensity calibration — verified, not assumed

CHMI's published legend (`scl-dbz-mmh.png`) prints **4, 8, 12 … 60 dBZ** beside the 15 colours: a
4 dBZ ladder, exactly 15 steps. Its mm/h gridlines for 0.1, 1, 10 and 100 sit one whole dBZ below
the 8, 24, 40 and 56 labels — which is where `Z = 200·R^1.6` puts them (7.01, 23.01, 39.01, 55.01
dBZ). So **CHMI's own conversion is the Marshall-Palmer relation this project already uses**, and
both radar sources land on one scale by construction rather than by coincidence.

The provisional calibration this source shipped with was therefore correct, and the discount it
carried is gone. What remains is **quantisation**: CHMI publishes 15 steps of 4 dBZ where
LibreWXR's grey ramp carries 1 dBZ, and at the light end one step separates 0.065 from 0.115 mm/h —
i.e. voting dry from voting wet against the default `light` threshold. Hence weight 0.95, just
below LibreWXR.

### Range weighting — the one source whose weight is not a constant

The CZRAD *grid* is a rectangle; the radars' *sight* is not. A beam climbs and widens with range,
so the same instrument is a different measurement at 40 km and at 170 km. CHMI has exactly two
radars, and it states its own ceiling for precipitation-intensity estimation as **"approximately
150–200 km from the radar"**.

| Radar | Position | Antenna |
|---|---|---|
| Skalky u Protivanova | 49.501 N, 16.790 E | 767 m |
| Brdy-Praha | 49.658 N, 13.818 E | 916 m |

Over south-western Poland only Skalky is in range at all — Brdy-Praha is 377 km from
Bielsko-Biała. And this is the uncomfortable part:

| Over Bielsko-Biała | Range | 0.5° beam centre | Beam width |
|---|---|---|---|
| **Skalky (CZ)** — the only CZRAD radar in range | **167 km** | **3.87 km** | 2.9 km |
| **Ramża (PL)** — feeds OPERA, and therefore LibreWXR | **44 km** | **0.85 km** | 0.76 km |

So around Bielsko-Biała **CHMI is the weaker-sighted of the two radar sources, not the stronger
one.** At ~3.9 km the beam is above the layer that produces drizzle and shallow orographic rain in
the Beskids — which is much of the weather this integration exists to catch.

`range_factor()` in `sources/chmi.py` therefore scales the source's weight: full to 120 km (beam
centre ~2.7 km), then linear decay to **0.5 at 200 km**, floored there. Bielsko-Biała lands at
0.705, so `chmi` votes at **0.95 × 0.705 = 0.67** — below both NWP models, and low enough that when
`librewxr` says wet and `chmi` says dry, the slot still comes out wet (1.00 / 1.67 = 0.60).
Brno, Praha and Ostrava keep the full 0.95.

**What the weighting is and is not based on.** It is based on beam geometry and CHMI's own stated
limit. It is **not** fitted to a measured error curve — see the comparison below, which establishes
that the two sources differ a great deal but does not pin the shape of the curve.

### Measured: CZRAD against OPERA across the whole domain

Both composites sampled on the same 5 km discs, same p90, at 08:20–08:25 UTC on 2026-08-26; 286
grid points inside the box, 64 of which had echo in at least one **(measured)**:

| Distance from a CZ radar | n | CZRAD mean | OPERA mean | only OPERA sees rain | only CZRAD |
|---|---|---|---|---|---|
| 0–80 km | 13 | 0.53 mm/h | 1.43 mm/h | 4 | 0 |
| 80–120 km | 10 | 0.33 | 0.90 | 1 | 0 |
| 120–150 km | 16 | 0.27 | 0.67 | 5 | 0 |
| 150+ km | 25 | 0.67 | 1.89 | **8** | 2 |

Two things follow, and only two:

1. **They are not interchangeable.** CZRAD reads roughly 3× lower in mm/h (≈7 dBZ), and the
   detection asymmetry is stark: **18 points where OPERA saw rain and CZRAD saw none, against 2 the
   other way.** Swapping one source for the other by region would make the integration reach
   different verdicts on the same weather depending on which side of an invisible line the user
   lives — worse than either source alone. Both vote, everywhere both are available.
2. **Which one is right in absolute terms is still unknown**, and this comparison cannot settle it.
   The gap persists close to the Czech radars too (0.53 vs 1.43 at 0–80 km), where overshoot does
   not explain it, and the miss rate does not rise cleanly with range. The other candidate is on
   the LibreWXR side: it is a standing open question in `STATE.md` that **LibreWXR fuses NWP model
   layers into its tiles outside radar coverage**, which would inflate it. Settling this needs
   ground truth — IMGW rain gauges — and belongs in phase 8.

### Geometry — from CHMI's published extent

The frame is **680 × 460 px**, and CHMI publishes two extents for it:

- whole image: E 11.267–20.770, N 48.047–52.167
- data: E 11.267–19.624, N 48.047–51.458, EPSG:3857, 1 × 1 km

Applying the first to the real frame puts the data rectangle at exactly **(0, 82)–(598, 460)** and
gives 1.005 km/pixel — which is the check that the projection is being read correctly, since the
second extent only lands on whole-pixel boundaries if the first is applied right. (The Meteor app's
"597 × 377 at offset +1,+82" was the same rectangle, inset by a pixel.)

**Coverage gating:** the adapter requires the *whole sampled disc* to sit inside the data
rectangle, inset by 0.3°. Outside the data rectangle every pixel is transparent, so a disc hanging
over the edge would read its missing half as "no echo" and quietly drag the percentile down.
Bielsko-Biała is comfortably inside; Kraków is past the eastern edge; Warszawa is outside entirely.

### Two traps the live check caught

1. **Use the unmasked products.** CHMI also publishes `png_masked` variants ("displayed considering
   precipitation on the earth's surface"), which is what the Meteor app used and which sounds like
   the better product for this project. They are rendered **with blending**, so their pixels are not
   palette colours: sampling one over Bielsko-Biała gave `#B1B1D0`, whose nearest palette neighbour
   is the white top of the ramp — **205 mm/h reported for light drizzle**. The unmasked frames carry
   exact palette colours.
2. **Match colours exactly, never by nearest neighbour.** Following from the above, the adapter
   treats an unrecognised colour as *no data* and fails the frame when the disc is mostly
   unrecognised. The grey `#C4C4C4` domain outline drawn into the composite is excluded the same
   way — under nearest-colour it too resolves to the top of the ramp.

### Cross-check against LibreWXR over Bielsko-Biała

Both adapters, same 5 km disc, same p90, at 07:40–08:50 UTC on 2026-08-26 **(measured)**:

| Slot (UTC) | LibreWXR / OPERA | CHMI / CZRAD |
|---|---|---|
| 07:40 observed | grey 44 → **12 dBZ** → 0.205 mm/h | level 3 → **12 dBZ** → 0.205 mm/h |
| 07:50 → 08:20 | 15, 16, 14, 12 dBZ → 0.32 … 0.21 mm/h | 12 dBZ → 0.205 mm/h |
| 08:30 → 08:50 | 11 dBZ → 0.18 mm/h, then no echo | 8 dBZ → 0.115 mm/h |

Two independent radar networks, two unrelated colour encodings, and the observed frame agrees to
the dBZ. Both call it light drizzle easing over the hour. That is the strongest evidence available
that the calibration and the projection are both right.

### Attribution

CC BY 4.0, and the data is the institute's own: credit the **Czech Hydrometeorological Institute**
and `opendata.chmi.cz`, and state that the data was modified — which it is, since we resample and
reclassify it.

### Open items

Carried into `STATE.md` rather than left here:

1. **Establish which of the two radars is right in absolute terms**, against IMGW rain gauges.
   The ~3× systematic gap above is the largest unexplained thing about this source set, and it is
   not obviously CHMI's fault: LibreWXR's NWP fusion is an equally good suspect. Phase 8.
2. **Measure the `librewxr` / `chmi` correlation.** Phase 0's rule is that source independence is
   established by measurement, and this pair has not been measured. The concern is smaller than it
   first looked — over Bielsko-Biała the two composites are dominated by *different* radars (Ramża
   at 44 km for OPERA, Skalky at 167 km for CZRAD), so they are closer to independent there than
   the shared-OPERA-ingest worry implied. Confirm it rather than assume it.
3. Confirm the exact CC BY 4.0 attribution wording CHMI prefers before 1.0.0.
4. Watch the `png_masked` question: it is the meteorologically better product, and decoding it
   would need un-blending or the HDF5 variant instead. Revisit only with a way to get exact values.

---

## Comparison table

Every candidate evaluated. "≥30 min frames?" is the pass/fail gate.

| # | Candidate | ≥30 min ready-made forecast frames? | Horizon & step | Coverage of Poland | Effective resolution | Update freq. & latency | Format / API shape | Intensity encoding | Licence | Attribution | Rate limits | API key | Cost | Stability | Checked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **LibreWXR** (`api.librewxr.net`) | **YES** — 6 future frames observed | +10…+60 min, 10-min step | Full — EUMETNET OPERA, 24 countries incl. POLRAD **(measured over Warszawa, Suwałki, Rzeszów)** | ~2 km (OPERA composite); tiles resolve finer than the data | Frames every 10 min; newest past frame 5 min old **(measured)** | RainViewer-compatible XYZ PNG tiles: `/v2/radar/{ts}/{size}/{z}/{x}/{y}/{color}/{smooth}_{snow}.png` + `/public/weather-maps.json` | Palette PNG; colour scheme `0` is a linear grayscale intensity ramp **(measured)** → dBZ → mm/h | Data CC-BY-4.0 (Italian tiles CC-BY-SA-4.0); software AGPL-3.0 | "Weather data via LibreWXR (librewxr.net)" plus each upstream source | No numeric limit published; fair-use terms forbid bulk download / high-volume automation | No | Free; self-hostable | **Low–medium.** OpenAPI reports version `0.1.0`; forecast is labelled "experimental"; terms state no uptime guarantee, no SLA, may go offline at any time | 2026-08-24 |
| 2 | **Open-Meteo — DWD ICON-EU** | **YES** — NWP forecast series | 5 days; hourly (3-hourly after 78 h) | Full — verified at all four corners of Poland **(measured)** | 0.0625° regular lat/lon = **4.3 km E-W × 6.95 km N-S at 52° N**; measured plateau width 0.0667° **(measured)** | Model run every 3 h; Open-Meteo flags models delayed > 20 min | JSON, `GET /v1/forecast`, `models=icon_eu`; **multiple coordinates per request** | `precipitation` in mm per step, quantised to 0.1 mm **(measured)** | CC-BY-4.0 | CC-BY-4.0 credit to Open-Meteo + DWD | 600/min, 5 000/h, 10 000/day (monthly 300 000, not enforced) | No | Free for non-commercial | **High.** Long-running, widely used, is the basis of a Home Assistant core integration | 2026-08-24 |
| 3 | **Open-Meteo — KNMI HARMONIE AROME Europe** | **YES** | 2.5 days native (blended to 15 d with IFS); hourly | Full — verified at all four corners **(measured)** | 5.5 km | **Every hour** — the freshest NWP available for Poland | as above, `models=knmi_harmonie_arome_europe` | as above | CC-BY-4.0 | Open-Meteo + KNMI | as above (shared quota) | No | Free for non-commercial | High (same platform as #2) | 2026-08-24 |
| 4 | **MET Norway Locationforecast 2.0** | **YES** | 9 days; **63 hourly steps then 6-hourly** **(measured)** | Full (global service) | ECMWF-derived outside the Nordics; treat as ~9–10 km | `updated_at` was 2 h 23 min old when fetched; `Expires` = now + 30 min **(measured)** | JSON, `GET /weatherapi/locationforecast/2.0/compact?lat=&lon=`; **one coordinate per request**; 40 kB raw / 2.7 kB gzipped **(measured)** | `next_1_hours.details.precipitation_amount` in mm | CC-BY-4.0 / NLOD | Credit MET Norway + link to licence | 20 req/s per application; must honour `Expires` / `If-Modified-Since`; ≥ 10 min between polls | No — but an identifying `User-Agent` is mandatory (403 otherwise; `Java`, `okhttp`, `Dalvik` are banned) | Free | High. National met service, long-standing public API | 2026-08-24 |
| 5 | Open-Meteo — DMI HARMONIE AROME DINI | YES | 2.5 days; hourly | Full **(measured)** | 2 km nominal; measured cell 0.025° lon = **1.70 km E-W** **(measured)** | Every 3 h | as #2, `models=dmi_harmonie_arome_europe` | as #2 | CC-BY-4.0 | Open-Meteo + DMI | shared quota | No | Free | High | 2026-08-24 |
| 6 | Open-Meteo — ECMWF IFS 0.25° | YES | 15 days; 3-hourly precipitation, served interpolated to hourly | Full **(measured)** | 0.25° = ~17 km E-W × 27.8 km N-S at 52° N | Every 6 h | as #2, `models=ecmwf_ifs025` | as #2 | CC-BY-4.0 | Open-Meteo + ECMWF | shared quota | No | Free | High | 2026-08-24 |
| 7 | Open-Meteo — DWD ICON-D2 | YES, and the **only native 15-minutely** model | 2 days; 15-min | **FAILS** — eastern limit measured at **19.59° E @ 54.5° N, 19.09° E @ 52.2° N, 18.69° E @ 50.0° N**; Warszawa, Białystok, Suwałki, Hrubieszów all return `"No data is available for this location"` **(measured)** | 0.02° ≈ 2 km | Every 3 h | as #2 | as #2 | CC-BY-4.0 | — | shared quota | No | Free | High | 2026-08-24 |
| 8 | RainViewer | **NO** | past 2 h only, 10-min step | Global radar mosaic | max zoom 7 | every 5 min | XYZ PNG tiles + `weather-maps.json` | dBZ colour schemes | Free for personal/educational | Link to rainviewer.com | Not published | No | Free | Medium — "we do not guarantee the availability of radar data" | 2026-08-24 |
| 9 | IMGW-PIB public data | **NO** | radar composites are observations; COSMO is 4×/day NWP | Poland | Radar 1 km; COSMO 2.8 km | **Radar newest file 04:25 UTC when checked at 15:39 UTC — 11 h 14 min stale**; COSMO listing showed only the previous day's 00 run **(measured)** | JSON product index → file downloads (HDF5/PNG/GRIB) | SRI dBR / GRIB2 | Free for private use; commercial needs an agreement | "The source of data is IMGW-PIB" | Not published | No | Free | **Broken for this purpose**: a listed COSMO GRIB file returned HTTP 404 from the datastore **(measured)** | 2026-08-24 |
| 10 | MET Norway Nowcast 2.0 | YES (2 h, 5-min updates) | 2 h | **FAILS** — Norway, Sweden, Finland, Denmark only; outside the Nordic area returns 404 | radar | 5 min | JSON/XML | mm/h | CC-BY-4.0 | MET Norway | 20 req/s | No | Free | High | 2026-08-24 |
| 11 | Rainbow Weather (`api.rainbow.ai`) | YES — 4 h, 1-min step | 4 h, 1 min (tiles: 0–4 h in 10-min steps, z 0–12) | Claimed global; **unverifiable** — unauthenticated requests return HTTP 401 for both `precip` and `precip-global` **(measured)**, and the non-global endpoint 404s outside supported regions | Not documented | Not documented | JSON point + XYZ tiles | `precipRate` in **mm/h**, `precipType` | Proprietary; ToS permits distributing apps built on it, forbids reselling access | "Powered by Rainbow.ai" mandatory | Nowcast 5 000 req/month free, then $0.10/1 000; tiles 30 000/month free | **Yes, required** | Free tier then paid | Unknown — no track record found | 2026-08-24 |
| 12 | Tomorrow.io | YES (sub-hourly timesteps) | up to +14 days | Global | Not published | Not published | JSON timelines | `precipitationIntensity` mm/h | Proprietary | Required | Free plan **500/day, 25/hour, 3/sec** | Yes | Free tier then paid | Medium | 2026-08-24 |
| 13 | OpenWeatherMap One Call | YES — minutely precipitation, 60 min | 60 min, 1-min step | Global | Not published | Not published | JSON | mm/h | Proprietary | Required | Free allowance 1 000 calls/day, 60/min | Yes | Free allowance then pay-as-you-call | High | 2026-08-24 |
| 14 | Pirate Weather | **NO (effectively)** | minutely block exists but **outside the HRRR domain it is derived from GFS hourly forecasts** — its own docs say this is "really not adding much value" | Global | GFS ~25 km outside HRRR | — | JSON (Dark Sky-compatible) | mm/h | Open source | Required | ~10 000 calls/month free (20 000 with a $2/month donation) | Yes | Free | Medium | 2026-08-24 |
| 15 | ICM UW (`meteo.pl` / `api.meteo.pl`) | n/a | UM model | Poland | 4 km | — | Private API | — | Non-commercial, no advertising, source must be credited | Required | — | **Yes — granted only after emailing a scanned official letter to meteo@icm.edu.pl** | Free after approval | High | 2026-08-24 |
| 16 | Precipiteau | YES — PySTEPS on OPERA, 1 h | 1 h, 15-min updates | Europe incl. Poland | 1 km | 15 min | **No documented JSON API** — interactive Leaflet map only | — | Not stated | — | — | — | Free | Launched April 2026, single operator | 2026-08-24 |
| 17 | **Pogoda i Radar** (`pogodairadar.pl`) / WetterOnline | Yes, in the consumer product — its "Radar opadów" animates into the future | ~90 min (consumer UI) | Poland | Not published | Not published | **No public developer API.** The site exposes only the consumer web app; `/api` returns 404 and `robots.txt` advertises sitemaps only **(measured)** | Not published | Proprietary | — | — | B2B contract | Commercial, sales contact only | High as a consumer product | 2026-08-24 |
| 18 | DWD direct (`opendata.dwd.de`) | YES (ICON-D2, RADVOR) | — | **FAILS** — RADOLAN/RADVOR are Germany-only; ICON-D2 as row 7 | — | — | GRIB2 / binary | — | GeoNutzV | Required | — | No | Free | High | 2026-08-24 |
| 19 | KNMI direct, Buienradar, GeoSphere Austria INCA, ARPAE, SHMÚ, Met Office DataHub | — | — | **FAIL** — each is a national/regional product not covering Poland | — | — | — | — | — | — | — | — | — | 2026-08-24 |
| 20 | Azure Maps Minute Forecast, Google Weather API, AccuWeather MinuteCast, Meteoblue, Meteomatics, Foreca, Weatherbit, Meteosource, Visual Crossing | YES for several | — | Global | — | — | JSON | mm/h | Proprietary | Required | Restrictive free tiers or none | Yes | Paid / trial-only | Varies | 2026-08-24 |

---

## Ranked recommendation

Four sources are recommended, with explicit roles. They are ordered by how much the design should
lean on them, not by data quality alone.

### 1. LibreWXR — OPERA radar nowcast *(timing precision)*

The only free, publicly documented, radar-extrapolation nowcast covering Poland. It is the sole
source with a time step (10 min) fine enough to place a 30-minute walk window precisely, so it
carries the *when* of the recommendation.

- Endpoint: `GET https://api.librewxr.net/public/weather-maps.json` → `radar.past` (12 frames) and
  `radar.nowcast` (6 frames, +10…+60 min). Tiles at
  `{host}{path}/{size}/{z}/{x}/{y}/{color}/{smooth}_{snow}.png`.
- Use `color=0` (grayscale intensity ramp) and `smooth=0`, `snow=0`: unsmoothed tiles come back as
  paletted PNGs with only a handful of distinct indices, so intensity survives decoding exactly.
  A z=8 tile over Poland measured **317–1 631 bytes** **(measured)**.
- Send an identifying `User-Agent`. Python's default `Python-urllib/*` was rejected with HTTP 403
  while `curl` and a normal browser string succeeded **(measured)**.
- **Because it is version 0.1.0 with no SLA, consensus must remain valid without it.** When it is
  absent the integration keeps working on sources 2 and 3 at reduced temporal precision, and says
  so through the confidence value.

### 2. Open-Meteo — DWD ICON-EU *(reliability baseline)*

The dependable backbone: an established provider, a national met service model, full coverage,
no key, generous limits. Hourly steps.

### 3. Open-Meteo — KNMI HARMONIE AROME Europe *(independent model, freshest run)*

A genuinely different model family from ICON, and the only model covering Poland that is
**re-run every hour** — the freshness that matters most for a walk one hour away.

**Verified independence.** Pearson correlation of hourly precipitation over the past 5 days at
Warszawa **(measured)**:

| | icon_eu | icon_global | ecmwf_ifs025 | gfs | knmi_harm | dmi_harm | ukmo_global | metno |
|---|---|---|---|---|---|---|---|---|
| **icon_eu** | 1.00 | 0.89 | 0.61 | 0.51 | **0.36** | 0.18 | 0.57 | **0.45** |
| **knmi_harm** | 0.36 | 0.40 | 0.64 | 0.48 | 1.00 | **0.76** | 0.46 | **0.61** |
| **metno** | 0.45 | 0.42 | 0.71 | 0.47 | 0.61 | 0.57 | 0.58 | 1.00 |

This measurement drives two rules for the consensus engine in phase 1:

- **Never count both members of a correlated pair as independent votes:** ICON-EU/ICON-Global
  (0.89), KNMI/DMI HARMONIE (0.76, both UWC-West DINI HARMONIE AROME), UKMO/GEM (0.77), and
  ECMWF-IFS/MET-Norway (0.71, MET Norway is ECMWF-driven over Poland).
- The recommended trio has a maximum pairwise correlation of **0.61**, the lowest achievable from
  the qualifying set.

### 4. MET Norway Locationforecast 2.0 *(provider-level failover)*

Sources 2 and 3 share one provider. If Open-Meteo is unreachable, MET Norway keeps the integration
alive on completely separate infrastructure. It is **not** polled while Open-Meteo is healthy —
that would add a correlated vote (0.61 with KNMI, 0.71 with IFS) for no gain, and burn a request
budget that its terms ask us to conserve.

### Substitutions permitted without re-doing this research

- Swap **DMI HARMONIE DINI** (2 km, measured 1.70 km E-W) for KNMI HARMONIE if higher spatial
  resolution proves to matter more than hourly refresh. They are the same family — use one, never
  both as independent votes.
- Add **ECMWF IFS 0.25°** only if MET Norway is dropped, never alongside it.

---

## Rejected candidates

Recorded so the research is not repeated.

| Candidate | Reason for rejection |
|---|---|
| **RainViewer** | **Fails the hard requirement.** Public API documents `past` frames only; `radar.nowcast` is empty on every live request **(measured 2026-08-24)**. Was the leading bootstrap candidate — it has since stopped serving nowcast frames publicly. |
| **IMGW-PIB** | Radar composites (SRI/CAPPI/CMAX) are observations, not forecasts. Its only forecast product, COSMO 2.8 km, is GRIB2 — undecodable with the allowed dependency set (`aiohttp`/`numpy`/`Pillow`), updated only 4×/day, and a listed file returned **HTTP 404** from the datastore. The public radar archive was **11 h stale** when checked. Not usable even as a "current conditions" input. |
| **DWD ICON-D2** (direct or via Open-Meteo) | Coverage ends at ~18.7–19.6° E — it excludes most of Poland including Warszawa **(measured)**. Painful, because it is the only native 15-minutely model available. |
| **MET Norway Nowcast 2.0** | Norway, Sweden, Finland, Denmark only; 404 outside the Nordic area. Properly confirmed rather than assumed. |
| **Pirate Weather** | Outside the HRRR domain its minutely block is interpolated from GFS hourly output; its own documentation calls this "really not adding much value". No genuine nowcast for Poland. |
| **ICM UW / meteo.pl** | Not an openly documented API — access to `api.meteo.pl` requires emailing a scanned official letter for approval. Fails "publicly documented or officially open". |
| **Precipiteau** | No documented JSON API (interactive map only), single operator, launched April 2026. Re-evaluate if it ever publishes an API. |
| **Rainbow Weather** | Technically the best fit on paper (1-min step, 4 h horizon, tiles and JSON). Rejected for now because it **requires a per-user API key** — unacceptable friction for a HACS integration — and its coverage over Poland **cannot be verified without one** (401 on both endpoints). Revisit only if the trio above proves insufficient. |
| **Pogoda i Radar / WetterOnline** (`pogodairadar.pl`) | The Polish edition of WetterOnline's "Weather & Radar" consumer app. Its precipitation radar does animate ~90 minutes into the future, so the meteorology is a good fit — but there is **no public developer API**: `/api` returns 404, `robots.txt` advertises only sitemaps **(measured 2026-08-24)**, and WetterOnline's data offering is a B2B contract arranged through sales. Reaching the forecast frames would mean calling the app's internal endpoints, which phase 0 explicitly forbids. |
| **Tomorrow.io** | Requires a key; free plan is 500/day and **25/hour**, which a 10-minute polling cadence across several walks would strain; sub-hourly layers are flagged premium. |
| **OpenWeatherMap One Call** | Requires a key and a billing subscription; the 60-minute minutely block is shorter than our search window (earlier margin 1 h + walk + later margin 30 min). |
| **DWD direct, KNMI, Buienradar, GeoSphere INCA, ARPAE, SHMÚ, Met Office DataHub** | National/regional products that do not cover Poland. |
| **Azure Maps, Google Weather, AccuWeather, Meteoblue, Meteomatics, Foreca, Weatherbit, Meteosource, Visual Crossing** | Commercial: mandatory keys, billing accounts, or free tiers too small for recurring polling. Redistribution terms are also unsuited to an open-source integration. |
| **EUMETNET OPERA direct** | Not freely licensed to end users; reachable for us only through LibreWXR, which is exactly what LibreWXR does. |

---

## Intensity mapping

Common scale used throughout the integration, expressed in **mm/h at ground level** (WMO/NWS
convention, with the *light* band starting low enough to catch drizzle a dog walker cares about):

| Class | mm/h |
|---|---|
| `none` | < 0.1 |
| `light` | 0.1 – 2.5 |
| `moderate` | 2.5 – 7.6 |
| `heavy` | ≥ 7.6 |

Per-source conversion onto that scale:

**Open-Meteo (ICON-EU, KNMI HARMONIE, DMI, IFS).** `precipitation` is millimetres accumulated over
the step, quantised to 0.1 mm **(measured)**.
- hourly series: `mm/h = value`
- `minutely_15` series: `mm/h = value × 4`

> **Derive intensity from the hourly series, use `minutely_15` only to align timestamps.** For
> Poland the 15-minutely series carries no extra information — Open-Meteo documents that only
> ICON-D2 has native 15-minutely data and that elsewhere it is interpolated from hourly, and the
> interpolated slices were observed not to conserve the hourly total **(measured)**. The 0.1 mm
> quantisation also costs precision: over a 15-minute step one count is 0.4 mm/h, versus 0.1 mm/h
> on the hourly series.

**MET Norway.** `next_1_hours.details.precipitation_amount` is millimetres over the coming hour →
`mm/h = value` directly.

**LibreWXR.** Tiles encode radar reflectivity. With colour scheme `0` the pixel is a grayscale
intensity ramp **(measured)**; convert grey → dBZ with **`dBZ = grey − 32`** (pinned in phase 3,
see the note below), then dBZ → rain rate with Marshall–Palmer
(`Z = 200·R^1.6`, i.e. `R = (10^(dBZ/10) / 200)^(1/1.6)`). The class boundaries become:

| Class | mm/h | dBZ |
|---|---|---|
| `none` | < 0.1 | < 7.0 |
| `light` | 0.1 – 2.5 | 7.0 – 29.4 |
| `moderate` | 2.5 – 7.6 | 29.4 – 37.1 |
| `heavy` | ≥ 7.6 | ≥ 37.1 |

> **Resolved in phase 3 (2026-08-25).** The calibration was read out of the AGPL-3.0 LibreWXR
> source rather than guessed, and is locked into a fixture test
> (`tests/test_librewxr.py::test_grey_level_calibration`):
>
> - `librewxr.sources._helpers._dbz_float_to_uint8` encodes reflectivity as
>   `pixel = clamp((dBZ + 32) * 2, 0, 255)`, mapping NODATA (`dBZ ≤ −32`) to 0.
> - `librewxr.colors.schemes` renders colour scheme `0` ("Black and White") by looking up row
>   `pixel // 2` of `librewxr/colors/color_table.csv`, whose row `i` holds grey `#iiiiii`
>   at `dBZ = i − 32`.
>
> Therefore **the rendered grey level equals `dBZ + 32`** — a 1 dBZ ramp — and grey `0` is fully
> transparent and means *no echo or no data*, indistinguishably. Confirmed against live tiles over
> six Polish cities: every pixel satisfies `R = G = B`, alpha is only ever 0 or 255, and the lowest
> non-transparent level observed is grey 42 (10 dBZ ≈ 0.15 mm/h), i.e. the OPERA composite's own
> noise floor sits inside the *light* band. **(measured 2026-08-25)**
>
> Rows 128–255 of the table are the snow ramp; requesting `snow=0` keeps every value in 0–127, so
> snow greys can never be mistaken for extreme rain.

---

## Effective resolution (input to the phase 1 radius decision)

Cell sizes in kilometres at 52° N, where 1° longitude = 68.5 km and 1° latitude = 111.3 km.

| Source | Nominal | Effective cell at 52° N | How established |
|---|---|---|---|
| LibreWXR (OPERA composite) | 2 km | ~2 km | Documented; tiles resolve finer than the underlying data |
| Open-Meteo DMI HARMONIE DINI | 2 km | **1.70 km E-W** (0.025° lon) | **Measured** — precipitation transect, 9 plateaus over 0.20° |
| Open-Meteo KNMI HARMONIE Europe | 5.5 km | ~5.5 km | Documented |
| Open-Meteo DWD ICON-EU | 0.0625° | **4.3 km E-W × 6.95 km N-S** | **Measured** — transect plateau width 0.0667° lon, matching the documented 0.0625° grid |
| MET Norway (over Poland) | ECMWF-derived | ~9–10 km | Documented model resolution |
| Open-Meteo ECMWF IFS 0.25° | 0.25° | 17 km E-W × 27.8 km N-S | Derived from the documented grid |

**The coarsest recommended source is ICON-EU at 6.95 km north–south.** If MET Norway is active as
failover, the coarsest becomes ~10 km. Phase 1 must set the minimum alert radius so that sampling
always covers at least one full cell of the coarsest source that is actually contributing.

---

## Fallback strategy

**Roles.** LibreWXR supplies temporal precision; ICON-EU and KNMI HARMONIE supply reliability;
MET Norway supplies provider-level redundancy and is polled *only* when Open-Meteo has failed;
CHMI supplies a second radar opinion, but only inside its own box.

**Regional availability.** A source that cannot serve the configured location reports
`not_applicable` and is never polled. This is a property of *where the user lives*, decided once,
and is deliberately distinct from `out_of_range` (a slot a fetched source does not reach) and from
`disabled` (a dormancy the next cycle could end). Only CHMI uses it today.

**Staleness.** A source is stale when its newest usable frame is older than 3× its nominal update
interval — LibreWXR > 30 min, CHMI > 15 min (it publishes every 5), ICON-EU > 9 h since the model
run, KNMI HARMONIE > 3 h, MET Norway `updated_at` > 6 h. Stale data is dropped from consensus for that cycle, not
used at reduced weight.

**Transient failures.** Per source, per cycle: at most 3 attempts with exponential backoff
(1, 2, 4 min, capped at 15 min). After that the source is marked unavailable for the cycle. The
previous cycle's data is never re-presented as fresh.

**Provider failover.** If Open-Meteo fails on 2 consecutive cycles, start polling MET Norway; stop
again after Open-Meteo succeeds twice in a row. Never poll both routinely.

**Degraded consensus.** The minimum viable source count is **1**.

| Contributing sources | Behaviour |
|---|---|
| 3+ | Full confidence range available |
| 2 | Confidence capped at 0.8 |
| 1 | Confidence capped at 0.5, result flagged `degraded` |
| 0 | Sensor `unavailable`, **no notification** — never guess |

Per-source status, freshness, and whether each contributed must be exposed in the sensor
attributes so a user can see *why* a recommendation looks the way it does.

---

## Request budget

**Assumptions.** Up to 4 walks/day; an active window of ~2.5 h per walk (earlier margin 1 h +
lead time 30 min + 30-minute walk + later margin 30 min); polling every 10 min inside the active
window, matching LibreWXR's frame cadence; **zero polling outside active windows and while the
enable switch is off**. That is 10 h/day active, 60 polling cycles/day.

| Source | Per cycle | Per active hour | Per day | Provider limit | Headroom used |
|---|---|---|---|---|---|
| LibreWXR | 1 metadata + 1–2 newly published frames × 1 tile (the frame cache holds the rest; a z=8 tile spans ~96 km at 52° N, so one tile covers any permitted radius except at a tile boundary, where it is 2, or 4 at a corner) | ≤ 25 requests per tile-count-1 disc, ≤ 44 for a two-tile one (self-imposed cap, **scaled by geometry since phase 8**) | ≤ 136 measured, four walks | No published numeric limit; fair use forbids bulk/high-volume automation | Well inside "normal interactive use" |
| Open-Meteo (ICON-EU + KNMI in **one** request) | 1 HTTP request | 6 requests | ≤ 60 | 600/min, 5 000/h, 10 000/day | ≤ 0.12 % of the minutely limit |
| Open-Meteo, counted as *calls* (worst case: each of 5 sample points counts separately) | 5 calls | 30 calls | ≤ 300 | 10 000/day | **3 % of the daily limit** |
| MET Norway (failover only, 1 point, honouring `Expires`) | ≤ 0.5 request | ≤ 2 requests | ≤ 20 | 20 req/s; ≥ 10 min between polls | negligible |
| **CHMI** (only inside its box; 1 forecast tar + 1 observed frame) | 2 requests | ≤ 30 (self-imposed cap; ≤ 24 in practice, one fetch per published 5-minute run) | ≤ 200 | none published; a national met service's open-data host | ~110 KB/fetch |
| **Total, outside CHMI's box** | | **≤ 28 HTTP requests/hour while a walk window is near; 0 otherwise** — **22 measured** | **≤ 200/day** — **156 measured** | | |
| **Total, inside CHMI's box** | | **≤ 58 HTTP requests/hour while a walk window is near; 0 otherwise** — **47 measured** | **≤ 380/day** — **372 measured** | | |

**Measured, phase 8** (`tests/test_performance.py`, a simulated day of four walks driven through
the real coordinator and the real adapters): **156 requests and 365 KiB** outside CHMI's box,
**372 requests and 7.9 MiB** inside it, 0 while alerting is off, and nothing at all outside a
walk window. The in-box hourly and daily figures are the ones `docs/ARCHITECTURE.md` § Resource
budget states; the earlier 46/h and 320/day in this table predated the sprint cadence and the
per-geometry LibreWXR ceiling, and are corrected above.

Two ceilings changed in phase 8, both because measurement showed the old ones were wrong:

- **LibreWXR's hourly cap now scales with the disc's tile count** (`hourly_cap()`): six cycles of
  one index poll plus up to two new frames, plus one cold start's back-fill. A flat 20/h was
  binding for any disc that straddles a tile boundary — which is a *common* geometry, not a rare
  one — and the adapter's response to a binding cap is to stop sampling frames, so a limit meant
  to be polite to the provider was quietly shortening the forecast.
- **CHMI's run-stamp offset dropped from 2 minutes to 90 s**, matching the measured publication
  lag (18 s typical, 68 s worst). It costs nothing and makes every cycle's radar up to a minute
  fresher.

Notes behind the numbers:

- One Open-Meteo request returns **all sample points × all models at once**. A live request for
  5 points × 3 models × 24 fifteen-minute steps returned **508 bytes gzipped / 6 487 bytes raw**
  **(measured)** — the whole spatial sample for a cycle in a single sub-kilobyte response.
- Open-Meteo's documented weighting counts extra calls for > 10 variables or > 2 weeks of data;
  neither applies (1 variable × 3 models, hours of data). Whether each coordinate counts as a call
  is not documented, so the table budgets the **conservative** interpretation. A live burst of
  60-coordinate requests did draw **HTTP 429** **(measured)**, so coordinates evidently carry
  weight — 5 sample points at a 10-minute cadence stays far below any threshold.
- MET Norway's `Expires` header was exactly 30 minutes ahead of `Date` **(measured)**; the budget
  honours it and the terms' 10-minute minimum poll interval, and requests must use
  `If-Modified-Since`.
- LibreWXR's cost is dominated by the frame cache working: frames shift by one per 10-minute cycle,
  so a warm cache fetches only newly published frames.
- **CHMI's forecast is one tar per run**, holding all six +10…+60 min frames (`ft60s10`), so its
  whole nowcast costs a single request — 92 KB measured — plus one for the observed frame. The
  frame cache cannot spare it (a new run every 5 minutes means new content every cycle), but at two
  requests a cycle it does not need to. This raises the in-window budget beyond the phase 0 figure,
  a deviation recorded in `STATE.md`.
- **The bandwidth phase 8 was asked to look at: 7.9 MiB a day inside CHMI's box** against 365 KiB
  outside it, measured over a simulated day of four walks. It is all composites — 111 KB per
  fetch, twice an hour per active hour plus the sprints. That is small against any home
  connection and large against a metered one, so it is stated here and in
  [CONFIG.md](CONFIG.md) § What it costs to run rather than
  reduced: the alternative is fetching fewer runs, which is exactly the freshness the second
  radar was added for.

Every recommended source fits inside its documented limits with at least an order of magnitude to
spare, and the integration is silent outside active windows.

---

## Attribution obligations (carry into the UI and README)

- **Open-Meteo** — CC-BY-4.0: credit Open-Meteo and the model owners (DWD for ICON-EU, KNMI for
  HARMONIE AROME), with a link to the licence, and indicate that data was processed.
- **MET Norway** — CC-BY-4.0/NLOD: credit MET Norway, link the licence. An identifying `User-Agent`
  with contact information is mandatory on every request.
- **LibreWXR** — CC-BY-4.0: "Weather data via LibreWXR (librewxr.net)", preserving the upstream
  attributions (EUMETNET OPERA for Poland).
- **CHMI** — CC-BY-4.0: credit the **Czech Hydrometeorological Institute** and `opendata.chmi.cz`.
  ČHMÚ publishes its open data free of charge under CC BY 4.0; confirm the institute's preferred
  attribution wording before 1.0.0.

All of them require stating that the data was modified — which it is, since we resample and
reclassify it.

## Sources

- [RainViewer Weather Maps API](https://www.rainviewer.com/api/weather-maps-api.html) · [RainViewer API overview](https://www.rainviewer.com/api.html)
- [Open-Meteo forecast docs](https://open-meteo.com/en/docs) · [DWD models](https://open-meteo.com/en/docs/dwd-api) · [KNMI models](https://open-meteo.com/en/docs/knmi-api) · [DMI models](https://open-meteo.com/en/docs/dmi-api) · [Terms](https://open-meteo.com/en/terms) · [Pricing & call counting](https://open-meteo.com/en/pricing)
- [MET Norway Terms of Service](https://api.met.no/doc/TermsOfService) · [Locationforecast 2.0](https://api.met.no/weatherapi/locationforecast/2.0/documentation) · [Nowcast 2.0](https://api.met.no/weatherapi/nowcast/2.0/documentation)
- [LibreWXR](https://librewxr.net/) · [Terms](https://librewxr.net/terms) · [OpenAPI](https://api.librewxr.net/openapi.json)
- [IMGW-PIB public data API](https://danepubliczne.imgw.pl/pl/apiinfo) · [product index](https://danepubliczne.imgw.pl/api/data/product)
- [Rainbow Weather](https://developer.rainbow.ai/) · [nowcast endpoint](https://doc.rainbow.ai/api-ref/nowcast/) · [tiles](https://doc.rainbow.ai/api-ref/tiles/)
- [Tomorrow.io free plan rate limits](https://support.tomorrow.io/hc/en-us/articles/20273728362644-Free-API-Plan-Rate-Limits) · [data layers](https://docs.tomorrow.io/reference/data-layers-core)
- [Pirate Weather data sources](https://docs.pirateweather.net/en/latest/DataSources/)
- [meteo.pl usage rules](https://www.meteo.pl/faq-category/rules-for-using-forecasts?lang=en)
- [Precipiteau](https://www.precipiteau.com/)
- [Pogoda i Radar (WetterOnline)](https://www.pogodairadar.pl)
