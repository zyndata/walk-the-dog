# Walk the dog 🐕🌧️

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

Everything is set up from the UI: a three-step wizard (where you walk, when you walk, how you
want to be told) and an options flow for later changes. Requires Home Assistant 2026.8 or newer
and a location in Poland.

`1.0.0` is feature-complete and measured, but not yet field-proven — the advice has been tested
against recorded data, not against a season of real weather. Reports of a wrong call are
welcome on the [issue tracker](https://github.com/zyndata/walk-the-dog/issues).

Weather data: LibreWXR (EUMETNET OPERA composite), Open-Meteo (DWD ICON-EU, KNMI HARMONIE
AROME), MET Norway, and — regionally — the Czech Hydrometeorological Institute's CZRAD composite.
All CC-BY-4.0, processed and reclassified by this integration.
