# Candidate source analysis — Android apps *meteor* and *wetterapp*

**Status:** research note, not yet wired in. Produced 2026-08-26 by static analysis of two
decompiled Android apps supplied by the maintainer:

- `meteo/meteor` — **Meteor** by Androworks (`org.androworks.meteor`, v4.9.0).
- `meteo/wetterapp` — **WetterOnline / Weather & Radar** (`de.wetteronline.wetterapp`, v2026.16.1),
  the German edition of the app sold in Poland as *Pogoda i Radar*.

Everything below was read out of the decompiled bytecode, not from any official API
documentation, and **not** by calling the live services. Treat every value as "observed in the
client, verify against the live endpoint before relying on it."

> **Scope decision up front.** Exactly one of the two apps yields a source worth adding:
> **Meteor**, whose radar frames come from an open national‑met‑service composite (Czech CHMI) and
> are reachable with **no API key and no secret credential**. It is documented in full below and is
> a good fit for the maintainer's stated use — a *regional* extra source that lights up around
> Bielsko‑Biała and stays dark elsewhere.
>
> **WetterOnline is not a usable source for this project**, for the same reason phase 0 already
> recorded in [DATA_SOURCES.md](DATA_SOURCES.md): it has no public API. Its data endpoints are
> gated by an HTTP Basic credential that the app ships **deliberately obfuscated** in its binary.
> Reusing that credential from a *different, and soon public,* open‑source integration would be
> unauthorised access to a proprietary service, and the maintainer's explicit worry — "so the
> server doesn't block walk‑the‑dog" — describes evading that service's own access controls. I have
> therefore documented WetterOnline's API *shape* (so the research is not repeated) but I have not
> extracted its credentials or written an impersonation/anti‑blocking recipe. See
> [§4](#4-wetteronline-wetterapp--analysed-and-rejected).

---

## 1. Meteor (Androworks) — recommended as a regional radar nowcast

### 1.1 What it actually is

Meteor renders an animated precipitation radar with a short **extrapolation forecast** ("future"
frames). The frames are pre‑rendered PNG composites the app only displays and colour‑decodes — it
never computes cloud movement itself, which is exactly the property this project requires of a
source.

The underlying data is the **Czech Hydrometeorological Institute (CHMI) `czrad` composite** — the
product names are visible verbatim in the frame paths (`pacz2gmaps3.z_max3d`, `fct_z_max`). This
means:

- **Product:** `z_max3d` — column‑maximum radar reflectivity (the strongest echo in the vertical
  column over each pixel), the standard CHMI CZRAD maximum‑reflectivity mosaic.
- **Forecast:** `fct_z_max` — an extrapolation nowcast of that field, in **10‑minute steps**.
- **Coverage:** the CHMI composite domain — Czechia and a margin around it. **It does not cover
  all of Poland.** See the geographic box in [§1.7](#17-geographic-coverage-and-projection); it
  reaches south‑western Poland (including **Bielsko‑Biała**) but stops well short of Warsaw, the
  coast, and the north‑east. This matches the intended role: an optional booster that is *active
  only inside its box and unavailable outside it.*

### 1.2 Hosts

| Purpose | URL |
|---|---|
| Feed / metadata + newest frame | `http://meteor.androworks.org/v2/feed` |
| Frame servers (numbered, load‑balanced) | `http://{n}.meteor.androworks.org/v2/{framePath}` |
| Frame servers (static fallbacks) | `http://1.fbck.meteor.androworks.org/v2/…`, `http://11.fbck…`, `http://111.fbck…` |
| Base map tiles (not weather) | `http://map.androworks.org/{layer}/{style}/{z}/{x}/{y}.png` |
| Base map fallback (not weather) | `https://a.tile.opentopomap.org/{z}/{x}/{y}.png` |

**Load balancing.** The client resolves `meteor.balancer.androworks.org` by DNS, takes the last
octet `n` of each returned A record, and rewrites the frame request's host to
`{n}.meteor.androworks.org`, falling back to the `*.fbck` hosts if DNS fails
(`org.androworks.meteor.B`, an OkHttp interceptor). **An integration does not need to replicate
this.** The plain host `meteor.androworks.org` serves the feed directly and is the sensible single
host to use; the balancer scheme exists to spread the app's own load and can be ignored for a
low‑volume poller.

> **Plain HTTP (port 80), not HTTPS.** Every weather endpoint is `http://`. On Home Assistant this
> means the adapter must allow a clear‑text host (`aiohttp` does by default; there is nothing to
> disable). Prefer the CHMI upstream over androworks if TLS matters to you —
> see [§1.7](#17-attribution-terms-and-the-legitimate-upstream).

### 1.3 Authentication — this is the important part

**There is no API key, token, OAuth, cookie, or signature.** The client adds exactly two request
headers, both non‑secret and both optional to the server's willingness to answer (they are for the
operator's own analytics / abuse handling, not access control):

| Header | Value in the app | Purpose |
|---|---|---|
| `X-App-Version` | the app's `versionName` (e.g. `4.9.0`), or the literal `X` if it can't be read | client version telemetry |
| `X-Inst-Id` | a random `UUID` generated once and cached in the `meteor` SharedPreferences under `install-id` | a stable per‑install id |

Source: `org.androworks.meteor.A` (request builder) and `org.androworks.meteor.F` (install‑id
generation/caching).

**Implications for not getting blocked — done the honest way.** Because access is not
credential‑gated, "not getting blocked" here is simply *being a well‑behaved client*, not evading a
control. Concretely:

- **Send an honest, identifying `User-Agent`** naming this integration and a contact URL — the same
  discipline the project already applies to MET Norway and LibreWXR. Do **not** impersonate the
  Meteor app or spoof its version; there is no benefit to it and it is the wrong thing to do.
- **Set `X-Inst-Id` to your own stable random UUID** (generate once, persist in the config entry).
  This is what the field is for. Do not reuse a UUID harvested from the app.
- **Poll politely.** Frames publish on a fixed cadence (`X-Step-Min`, typically 5–10 min); polling
  faster than that only wastes their bandwidth and yours. Honour `X-Next-Query`
  ([§1.4](#14-the-feed-endpoint)) as the "don't ask before" time. Keep to the project's existing
  rule: **zero requests outside a walk window.**
- **Cache aggressively.** Frame files are immutable once published (their timestamp is in the
  filename), so a fetched frame never needs re‑fetching — reuse the project's LibreWXR frame‑cache
  pattern.
- **This is an undocumented private endpoint with no SLA.** It can change, rate‑limit, or disappear
  without notice. It must therefore be a *strictly optional* contributor: when it is missing the
  consensus has to remain valid without it (the same rule already applied to LibreWXR).

### 1.4 The feed endpoint

```
GET http://meteor.androworks.org/v2/feed
```

The response **body is the newest radar frame image itself** (PNG bytes), and the useful metadata
is carried in **response headers**:

| Header | Type | Meaning |
|---|---|---|
| `X-Frame-Date` | `yyyyMMdd.HHmm` (UTC) | timestamp of the newest *observed* frame |
| `X-Step-Min` | integer minutes | spacing between successive **past** frames |
| `X-Future-Step-Min` | integer minutes | spacing between **future** (forecast) frames (10 in practice) |
| `X-Future-Date` | `yyyyMMdd.HHmm` (UTC) | base time the forecast frames are issued from |
| `X-Future-Levels` | integer | how many forecast frames are currently available |
| `X-Next-Query` | epoch millis | earliest time the client should poll again — **honour this** |

Date format is `yyyyMMdd.HHmm` in **UTC** (`org.androworks.meteor.AbstractC1182w.a`). All frame
handling in the app runs off these headers, so a good adapter fetches the feed once per cycle, then
derives the frame URLs it wants from the two base timestamps.

### 1.5 Frame URL construction

Given the feed's `X-Frame-Date` (call it `F`) and `X-Future-Date` (call it `B`), both formatted
`yyyyMMdd.HHmm`:

**Past / current frames** — one every `X-Step-Min` going backwards from `F`:

```
{server}/v2/czrad-z_max3d_masked/pacz2gmaps3.z_max3d.{F}.0.png
```
(the trailing `.0` is the level; older frames substitute their own timestamp for `{F}`.)

**Future / forecast frames** — indexed `k = 1 … X-Future-Levels`, each `k·10` minutes after `B`:

```
{server}/v2/czrad-z_max3d_fct_masked/{B}/pacz2gmaps3.fct_z_max.{B}_{Tk}.{k·10}.png
```
where `{Tk}` is `B + k·(X-Future-Step-Min)` formatted the same way, and `{k·10}` is the forecast
lead time in minutes as an integer. `{server}` is `http://meteor.androworks.org` (or a balancer
host — see §1.2). Source: `com.google.android.exoplayer2.extractor.ts.D.apply`.

For this project, the **forecast frames are the valuable part** — they give a radar‑based nowcast
in 10‑minute steps out to `X-Future-Levels·10` minutes, i.e. the same kind of "when" precision the
project currently gets only from LibreWXR, but from an independent radar network over the
Bielsko‑Biała area.

### 1.6 Frame format and intensity decoding

- **Container:** PNG, decoded with `BitmapFactory`. Each frame is a fixed **597 × 377 px**
  composite (the app reads pixels at stride 597 for 377 rows, offset 82 px down / 1 px right into
  the decoded bitmap — i.e. there is a small header/legend margin the app skips). Verify exact crop
  against a live frame.
- **Not XYZ tiles.** Unlike LibreWXR/RainViewer, one frame is the whole domain in a single image,
  in the projection described in §1.7's anchors below. Sampling a disc around a point means
  projecting lat/lon → pixel, not fetching a tile.
- **Intensity is palette‑encoded**, exactly like LibreWXR: each pixel is one of a small fixed set
  of colours representing a reflectivity band. The app carries **two** palettes — a bright "day"
  ramp and a muted "night" ramp — and classifies a pixel by matching its colour to the ramp index
  (`AbstractC1182w.h` = day, `AbstractC1182w.i` = night; `AbstractC1182w.j/k` are the reverse
  lookup maps). **Request/behaviour note:** the frame PNGs are rendered with the day ramp; the
  night ramp is only an alternate client‑side colouring the app can match. Decode against the day
  ramp.

**Day palette (index → ARGB), 15 echo levels + transparent:**

| idx | ARGB | idx | ARGB | idx | ARGB | idx | ARGB |
|---|---|---|---|---|---|---|---|
| 0 | `#00000000` (none) | 4 | `#FF006CC0` | 8 | `#FF9CDC00` | 12 | `#FFFC5800` |
| 1 | `#FF380070` | 5 | `#FF00A000` | 9 | `#FFE0DC00` | 13 | `#FFFC0000` |
| 2 | `#FF3000A8` | 6 | `#FF00BC00` | 10 | `#FFFCB000` | 14 | `#FFA00000` |
| 3 | `#FF0000FC` | 7 | `#FF34D800` | 11 | `#FFFC8400` | 15 | `#FFFCFCFC` |

This is a standard reflectivity ramp (violet/blue = drizzle‑to‑light, green = moderate,
yellow/orange = heavy, red = very heavy, white = extreme/hail). **Index 0 is fully transparent and
means "no echo or no data,"** exactly like LibreWXR grey 0 — treat it as *no rain*, never as
missing‑that‑counts.

> **dBZ / mm‑h calibration is not hard‑coded in the client** the way LibreWXR's ramp turned out to
> be. The 15 levels are an ordinal intensity scale; to put them on the project's common
> `none/light/moderate/heavy` mm/h scale you must pin a level→dBZ mapping. Two options, in order of
> preference:
> 1. **Read it from CHMI.** The `czrad` `z_max` product publishes its colour scale; adopt CHMI's
>    own dBZ boundaries for these 15 steps, then apply the project's existing Marshall–Palmer
>    dBZ→mm/h conversion (`R = (10^(dBZ/10)/200)^(1/1.6)`), and lock it in a fixture test — the same
>    method used for LibreWXR in phase 3.
> 2. **Bracket it conservatively.** As a placeholder, map index ≥1 → *light*, a mid band → *moderate*,
>    the top few → *heavy*, and flag the source `degraded` until the real calibration lands. Do not
>    ship the placeholder as if calibrated.

### 1.7 Geographic coverage and projection

The composite is anchored by three `GeoPoint`s in the client
(`AbstractC1182w.b/c/d`):

| Anchor | Latitude | Longitude | Role |
|---|---|---|---|
| centre | 49.741344 | 15.336227 | map centre of the domain |
| corner c | 51.452389 | 11.289632 | north‑west extent |
| corner d | 48.062307 | 19.613042 | south‑east extent |

So the usable box is roughly **lat 48.06 → 51.45 N, lon 11.29 → 19.61 E**, in a Google‑Maps /
Web‑Mercator projection (the `gmaps3` in the product name). Sanity check against the maintainer's
requirement:

- **Bielsko‑Biała (≈49.82 N, 19.05 E): inside the box** (comfortably in latitude; ~0.56° inside the
  eastern edge). ✔ Meteor is usable there.
- **Kraków (≈50.06 N, 19.94 E): just east of 19.61 E — on or past the edge.** Treat the eastern
  ~0.3–0.5° as an unreliable margin.
- **Warsaw (≈52.23 N): north of 51.45 N — outside.** ✘ As expected, Meteor is unavailable for
  central/northern Poland.

**Coverage gating the integration must apply:** compute whether the configured location lies inside
the box (with a safety inset from the edges), and if not, mark this source *not‑applicable* and
never poll it — the "inactive on unsupported regions" behaviour the maintainer asked for. Because
the near‑edge margin degrades, inset the usable box by ~0.3° before declaring a point covered.

### 1.8 Attribution, terms, and the legitimate upstream

- **The data originates from CHMI** (Czech Hydrometeorological Institute), served here through
  Androworks' infrastructure. If the integration ships CHMI radar, credit CHMI as the data source
  and Androworks/Meteor as the delivery path, and state the data was modified (resampled and
  reclassified) — consistent with the project's other attributions.
- **Prefer the CHMI open‑data upstream where it exists.** CHMI publishes CZRAD radar products on its
  own open‑data portal. Sourcing `z_max`/`fct_z_max` directly from CHMI (rather than through a
  third‑party app's private endpoint) would be TLS‑secured, properly documented, and free of the
  "undocumented private endpoint" caveat — a strictly better footing than androworks for a public
  HACS integration. **Recommended follow‑up:** verify CHMI's open‑data URLs and licence for these
  products and, if they check out, source from CHMI directly and cite Meteor only as the discovery
  path. Use androworks only if CHMI's direct feed proves impractical, and then as a low‑volume,
  clearly‑optional, well‑identified client.

### 1.9 Fit against what the project already has

| | LibreWXR (current radar) | **Meteor / CHMI (proposed)** |
|---|---|---|
| Kind | OPERA composite, extrapolated | CHMI CZRAD composite, extrapolated |
| Radar network | EUMETNET OPERA (POLRAD etc.) | Czech CZRAD — **genuinely different radars** |
| Coverage | all of Poland | SW Poland only (incl. Bielsko‑Biała) |
| Forecast horizon / step | +60 min / 10 min | ~`Future‑Levels`·10 min / 10 min |
| Wire format | XYZ PNG tiles (z=8) | single 597×377 PNG composite |
| Auth | none (identifying UA) | none (identifying UA; `X-Inst-Id`) |
| Transport | HTTPS | **HTTP** (unless sourced from CHMI directly) |

**Verdict: add it, as an optional regional radar contributor.** It is genuinely new information —
an independent radar network with its own forecast — over precisely the area the maintainer cares
about, at no key cost. It slots into the existing "radar supplies the *when*" role alongside
LibreWXR, and its box‑gating gives the maintainer's requested "active near Bielsko‑Biała, dark
elsewhere" behaviour for free. Treat it with the same optionality and staleness discipline as
LibreWXR, and calibrate its palette to mm/h before trusting its intensity.

### 1.10 Open items before wiring it in

1. Confirm the live feed's headers and a real frame against §1.4–§1.6 (one manual `curl` with an
   honest `User-Agent`).
2. Pin the level→dBZ calibration from CHMI's published scale; lock it in a fixture test.
3. Verify the exact image crop (the 82 px / 1 px offsets) and the lat/lon→pixel transform against
   the three anchors.
4. Check whether CHMI open‑data serves `z_max`/`fct_z_max` directly, and if so, prefer it.
5. Confirm licence/attribution wording for CHMI radar.

---

## 2. Base‑map endpoint (not a weather source)

`http://map.androworks.org/{layer}/{style}/{z}/{x}/{y}.png` and the OpenTopoMap fallback are
ordinary background‑map tiles the app draws the radar over. They carry **no precipitation data** and
are irrelevant to this project. Noted only so they are not mistaken for a data source.

---

## 3. Analytics endpoints seen in the apps (ignore)

For completeness, so they are not misread as data sources: Meteor posts telemetry to
`https://1.events.androworks.org/api/v1/events`, `meteor.udplogger.androworks.org`, and Google
Analytics; WetterOnline posts to an Elastic APM RUM endpoint, `tiles.wo-cloud.com/analytics`, and
the usual ad/consent SDKs. None return weather data.

---

## 4. WetterOnline (`wetterapp`) — analysed and rejected

Recorded so the research is not repeated, and consistent with the existing rejection of WetterOnline
in [DATA_SOURCES.md](DATA_SOURCES.md).

### 4.1 What is there

WetterOnline's app talks to a proper JSON weather backend (Retrofit interfaces under
`de.wetteronline.api.*`). The endpoints that carry actual weather values are real and would, on
paper, be attractive:

| Interface | Path (relative to an `api*.wo-cloud.com` host) | What it returns |
|---|---|---|
| `ForecastApi` | `blending/forecast/{version}` | hourly/daily forecast series (`grid_latitude`, `grid_longitude`, `location_id`, `timezone`) |
| `ShortcastApi` | `/blending/shortcast/{subPathAndVersion}` | short‑range / nowcast‑style series — the interesting one |
| `CurrentApi` | `/blending/current/{version}`, `/blending/current/days/{version}` | current conditions (+ days) |
| `UvIndexApi`, `AstroApi`, `WarningsApi`, `WarningsMapsApi`, `TextApi` | `blending/…`, `warnings/…` | UV, astro, weather warnings, warning maps, text summaries |

Host selection (`ep5` / `ufa`): production data hosts are `api.wo-cloud.com` and
`api-app.wo-cloud.com` (legacy `api-app.wetteronline.de`); search is `search.prod.geo.wo-cloud.com`;
map snapshot tiles are `tiles.wo-cloud.com`; the in‑app radar is a **WebView**, not an API —
`https://radar.wo-cloud.com/android/index.html`.

There is also a rendered **map‑snapshot** endpoint, `GET tiles.wo-cloud.com/snippet-tiles`
(`SnippetApi`, params `width, height, latitude, longitude, layergroup, zoom, period, format,
alltimesteps, …`). It appears **not** to sit behind the Basic‑auth interceptor, but it returns a
**pre‑rendered map image**, not numeric precipitation values, so it is not a data source in the
sense this project needs, and it is still proprietary WetterOnline output.

### 4.2 Why it is rejected

1. **No public API.** These are the app's private endpoints. Phase 0 already established WetterOnline
   has no developer API and that calling its internal endpoints is out of scope.
2. **Access is credential‑gated, and the credential is a deliberately hidden secret.** An OkHttp
   interceptor (`ek0`) attaches an HTTP Basic `Authorization` header to requests whose host matches
   the data hosts. The credential is not entered by the user or fetched at runtime — it is **baked
   into the binary and obfuscated** (Base64 over an XOR‑masked blob, `gk0`/`fk0`), precisely so it
   is not trivially lifted. That is a clear signal the operator does not intend third‑party reuse.
3. **The maintainer's own requirement makes it worse, not better.** "Authenticate so the server
   doesn't block walk‑the‑dog" means, for WetterOnline specifically, *replicate the app's hidden
   credential and impersonate the official app to slip past its access controls.* Doing that from a
   separate, soon‑to‑be‑public open‑source integration is unauthorised access to a proprietary
   service and the kind of detection‑evasion this project should not build. It would also be
   self‑defeating: a shared secret committed to a public repo gets revoked, and every user blocked,
   almost immediately.
4. **Licensing.** WetterOnline data is proprietary; it cannot be redistributed/processed under the
   CC‑BY terms the project relies on for its other sources.

I therefore did **not** decode the embedded credential or document an auth/anti‑blocking procedure
for it. If a WetterOnline‑quality nowcast is wanted, the correct path is a commercial/B2B data
agreement with WetterOnline (their public offering is sales‑contract only), not credential reuse.

### 4.3 If you still want a legitimate nowcast beyond LibreWXR

The project already reached this conclusion in phase 0. The clean options remain: Meteor/CHMI above
for the Bielsko‑Biała region (open data, no key), and — if broader Polish radar is ever needed —
re‑evaluating IMGW‑PIB or an approved ICM/meteo.pl access, both already catalogued in
[DATA_SOURCES.md](DATA_SOURCES.md).

---

## 5. One‑line summary

- **Meteor → yes, as an optional regional radar‑nowcast source.** CHMI CZRAD `z_max` + 10‑minute
  `fct_z_max` forecast, keyless, covers Bielsko‑Biała, gated to its box, HTTP‑only (prefer CHMI
  upstream). Needs palette→mm/h calibration and coverage gating before use.
- **WetterOnline → no.** Proprietary, no public API, access gated by an obfuscated embedded
  credential; using it would be unauthorised and duplicates a rejection already on record.
