# Walk the dog 🐕🌧️

*Polish: „Idź już z psem"*

> [!WARNING]
> **Work in progress — not ready for use.** There has been no release yet (version `0.1.0`);
> the integration is being built phase by phase in the open.
>
> - **Works today:** installation, the full setup wizard (location, walk schedule, alert
>   settings), the options flow, and the whole prediction loop — the recommendation sensor,
>   the alerting switch, push notifications and the `walk_the_dog_alert` event.
> - **Not yet:** Polish localization and the integration icon (phase 7), and the performance
>   pass on low-end hardware (phase 8). Real-world accuracy is untested.
>
> Breaking changes land without warning and there is no upgrade path between development
> versions. See [CHANGELOG.md](CHANGELOG.md) for what has landed and [STATE.md](STATE.md)
> for the phase currently in progress.

A [Home Assistant](https://www.home-assistant.io/) custom integration that predicts whether it
will rain during your recurring dog walks — and, when a walk is at risk, proactively suggests
going out **earlier or later** so the walk stays dry.

Instead of trusting a single weather app, it combines ready-made precipitation nowcasts from
multiple independent sources covering Poland, scores how much they agree, and only alerts you
when the consensus says your walk window is wet.

## Features

- **Consensus, not a guess** — a radar nowcast (EUMETNET OPERA via LibreWXR) plus two
  independent NWP models (DWD ICON-EU and KNMI HARMONIE AROME via Open-Meteo), with MET Norway
  as automatic failover. Sources are weighted by reliability and freshness; the sensor reports
  a confidence value and a per-source breakdown.
- **Actionable advice** — "go 20 minutes earlier" or "wait half an hour", searched on a
  10-minute grid within your configured margins; not just "rain expected".
- **One notification at the right moment** — pushed at the last actionable time
  (`walk − earlier margin`), re-sent only when the recommendation materially changes. Optional
  auto-mute while you are away from home, and an optional `walk_the_dog_alert` event for your
  own automations.
- **Kind to small hardware** — polls only around your walk times (zero requests otherwise),
  samples only the pixels around your location, and stays within strict memory and request
  budgets. Runs comfortably on single-core ARM boxes.

## Requirements

- Home Assistant 2026.8 or newer.
- A location in Poland (the selected data sources cover Poland; most also cover wider Europe,
  but coverage is only verified for Poland).
- A mobile app notification target (`notify.mobile_app_*`) if you want push notifications.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a custom repository (category: *Integration*), or install
   it directly from the HACS store once it is included there.
2. Install **Walk the dog** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and search for **Walk the dog**.

### Manual

Copy `custom_components/walk_the_dog/` into the `custom_components/` folder of your Home
Assistant configuration directory and restart Home Assistant.

### Development period: manual install

While developing, deploy your working copy into a local Home Assistant instance either by
copying the folder as above, by symlinking it
(`ln -s /path/to/repo/custom_components/walk_the_dog /path/to/ha-config/custom_components/walk_the_dog`),
or with the repo's deploy script (`python scripts/install.py`, target path configured in
`.env` — see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)).

## Configuration

Everything is configured from the UI — a three-step wizard (location, walk schedule,
parameters) plus an options flow for later changes. Three schedule modes are supported: same
times every day, weekday/weekend split, or a full per-day schedule.

See [docs/CONFIG.md](docs/CONFIG.md) for every option and its semantics.

## How it works

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design and
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) for the source research. In short: within a
window around each scheduled walk, the integration samples each source's precipitation
forecast over a disc around your location, normalizes everything to a common mm/h scale,
computes a weighted-vote risk and confidence per 10-minute slot, evaluates your walk window,
and searches for the nearest dry window of the full walk duration.

## Data sources & attribution

Weather data, modified (resampled and reclassified) by this integration:

- **LibreWXR** ([librewxr.net](https://librewxr.net/)) — weather data via LibreWXR, radar
  composite © [EUMETNET OPERA](https://www.observations.eu/) members, licensed
  [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).
- **Open-Meteo** ([open-meteo.com](https://open-meteo.com/)) — forecast data by Open-Meteo,
  based on DWD ICON-EU and KNMI HARMONIE AROME model output, licensed
  [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).
- **MET Norway** ([api.met.no](https://api.met.no/)) — weather data from the Norwegian
  Meteorological Institute, licensed
  [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) /
  [NLOD](https://data.norge.no/nlod/en/2.0).

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). One command sets up the environment on
Windows or Linux: `python scripts/setup.py`.

## License

[MIT](LICENSE)
