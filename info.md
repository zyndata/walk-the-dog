# Walk the dog 🐕🌧️

> ### ⚠️ Work in progress — not ready for use
>
> There has been no release yet (version `0.1.0`). **Works today:** installation, the setup
> wizard, the options flow, and the whole prediction loop — recommendation sensor, alerting
> switch, push notifications and the custom event. **Not yet:** Polish localization and the
> integration icon, and the performance pass on low-end hardware. Real-world accuracy is
> untested.
>
> Breaking changes land without warning, and there is no upgrade path between development
> versions.

**Will it rain on your dog walk?** This integration watches precipitation nowcasts from
multiple independent weather sources covering Poland, scores how much they agree, and — when
one of your recurring walk times is at risk — proactively suggests going out **earlier or
later** so the walk stays dry.

- Consensus of independent sources (radar nowcast + two NWP models, with automatic failover),
  never a single provider's guess.
- One recommendation sensor for the next upcoming walk: risk, confidence, suggested time,
  per-source breakdown.
- Push notification at the last actionable moment, re-sent only when the advice materially
  changes. Optional auto-mute while you are away and an optional event for automations.
- Designed for low-end hardware: polls only around your walk times, samples only the pixels
  around your location, stays within strict request and memory budgets.

Polish localization ("Idź już z psem") is a first-class goal.

Weather data: LibreWXR (EUMETNET OPERA composite), Open-Meteo (DWD ICON-EU, KNMI HARMONIE
AROME), MET Norway — all CC-BY-4.0, processed and reclassified by this integration.
