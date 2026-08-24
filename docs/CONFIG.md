# Configuration

> **Stub — finalized during phases 1 and 5** (see [PLAN.md](../PLAN.md)). The option list below
> comes from the product specification and bootstrap decisions; semantics, validation, and
> storage shape get pinned down when the config flow is designed and implemented.

## Config flow wizard

- **Step 1 — Location:** map picker (HA `LocationSelector`), pre-filled with HA home coordinates.
- **Step 2 — Walk schedule:** mode selector — one of *same times daily* / *weekday+weekend
  split* / *per-day* — with the form adapting to the chosen mode; editable list(s) of walk times.
- **Step 3 — Parameters:** table below.

All of steps 2–3 (including schedule mode) editable later via the options flow.

## Options

| Option | Default | Notes |
|---|---|---|
| Alert radius around home | TODO (phase 1) | Default & minimum derived from source resolutions; see [ARCHITECTURE.md](ARCHITECTURE.md) |
| Intensity threshold | TODO (phase 1) | light / moderate / heavy on the common scale |
| Earlier margin | 1 h | How far back to search for a dry window |
| Later margin | 30 min | How far forward to search |
| Average walk duration | **required, no default** | Warn when > 30 min (nowcast reliability) |
| Notification device | — | Picked from `notify.mobile_app_*` services |
| Fire custom event | off | Emits `walk_the_dog_alert`; payload documented below |
| Auto-mute entity | none | Optional `person`/`device_tracker`; alerts suppressed while not `home` |

## Notification behavior

Fires at `T − earlier_margin`; re-notified only on material change (definition in
[ARCHITECTURE.md](ARCHITECTURE.md)). Suppressed by the enable switch and by auto-mute.

## Event payload

TODO (phase 6): documented `walk_the_dog_alert` payload schema.

## Config entry data shape

TODO (phase 5): exact stored keys and types for config entry data and options.
