# Walk the dog 🐕🌧️

> ### ⚠️ Work in progress — pre-1.0
>
> Every release below `1.0.0` is a development release. **Works today:** installation, the
> setup wizard, the options flow, the whole prediction loop — recommendation sensor, alerting
> switch, push notifications and the custom event — full English and Polish localization, and
> the measured performance budget. **Not yet:** the brands submission, until which Home
> Assistant shows a placeholder icon. Real-world accuracy is untested.
>
> Breaking changes land without warning, and there is no upgrade path between development
> versions.

**Will it rain on your dog walk?** This integration watches precipitation nowcasts from
multiple independent weather sources covering Poland, scores how much they agree, and — when
one of your recurring walk times is at risk — proactively suggests going out **earlier or
later** so the walk stays dry.

- Consensus of independent sources (radar nowcast + two NWP models, with automatic failover),
  never a single provider's guess. Around south-western Poland a second radar — the Czech CHMI
  composite — joins in automatically; elsewhere it stays silent.
- One recommendation sensor for the next upcoming walk: risk, confidence, suggested time,
  per-source breakdown.
- Push notification at the last actionable moment, re-sent only when the advice materially
  changes. Optional auto-mute while you are away and an optional event for automations.
- Designed for low-end hardware: polls only around your walk times, samples only the pixels
  around your location, stays within strict request and memory budgets.

Fully localized in Polish — the integration calls itself "Idź już z psem" there.

Weather data: LibreWXR (EUMETNET OPERA composite), Open-Meteo (DWD ICON-EU, KNMI HARMONIE
AROME), MET Norway, and — regionally — the Czech Hydrometeorological Institute's CZRAD composite.
All CC-BY-4.0, processed and reclassified by this integration.
