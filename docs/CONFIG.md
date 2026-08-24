# Configuration

> **Finalized in phase 1** for option semantics and defaults; validation and the exact storage
> shape get pinned down when the config flow is implemented in phase 5 (see
> [PLAN.md](../PLAN.md)).

## Config flow wizard

- **Step 1 — Location:** map picker (HA `LocationSelector`), pre-filled with HA home coordinates.
- **Step 2 — Walk schedule:** mode selector — one of *same times daily* / *weekday+weekend
  split* / *per-day* — with the form adapting to the chosen mode; editable list(s) of walk times.
- **Step 3 — Parameters:** table below.

All of steps 2–3 (including schedule mode) editable later via the options flow.

## Options

| Option | Default | Notes |
|---|---|---|
| Alert radius around home | **5 km** (min 4 km, max 15 km) | Derived from measured source resolutions — min guarantees the sampled disc spans ≥ 1 full ICON-EU cell; see [ARCHITECTURE.md](ARCHITECTURE.md) § Alert radius decision |
| Intensity threshold | **light** (≥ 0.1 mm/h) | light / moderate / heavy on the common scale ([DATA_SOURCES.md](DATA_SOURCES.md)); a slot counts as rainy when the consensus intensity reaches the chosen class |
| Earlier margin | 1 h | How far back to search for a dry window |
| Later margin | 30 min | How far forward to search |
| Average walk duration | **required, no default** | Warn when > 30 min (nowcast reliability) |
| Notification device | — | Picked from `notify.mobile_app_*` services |
| Fire custom event | off | Emits `walk_the_dog_alert`; payload documented below |
| Auto-mute entity | none | Optional `person`/`device_tracker`; alerts suppressed while not `home` |

Internal, not user-facing: `lead_time` = 30 min — how long before `T − earlier_margin` polling
starts so the decision moment has fresh data (decision in
[ARCHITECTURE.md](ARCHITECTURE.md) § Coordinator scheduling & polling windows).

## Notification behavior

Fires at `T − earlier_margin`; re-notified only on material change (defined precisely in
[ARCHITECTURE.md](ARCHITECTURE.md) § Walk-window evaluation). Suppressed by the enable switch
and by auto-mute.

## Event payload

TODO (phase 6): documented `walk_the_dog_alert` payload schema — the serialized
`Recommendation` structure from [ARCHITECTURE.md](ARCHITECTURE.md) § Outputs.

## Config entry data shape

TODO (phase 5): exact stored keys and types for config entry data and options.
