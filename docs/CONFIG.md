# Configuration

> Option semantics and defaults were finalized in phase 1; **phase 5 implemented the flows and
> pinned the storage shape** — this document now describes what the code actually does. The
> `walk_the_dog_alert` payload is still owned by phase 6 (see [PLAN.md](../PLAN.md)).

Only **one** Walk the dog entry can exist per Home Assistant (`single_config_entry` in
`manifest.json`): one home, one schedule, one recommendation sensor. A second attempt aborts
with *"Already configured"*.

## Config flow wizard

| # | Step id | What it asks |
|---|---|---|
| 1 | `user` | **Location** — map picker (HA `LocationSelector`), pre-filled with the HA home coordinates. |
| 2a | `schedule_mode` | **Schedule type** — *same times daily* / *weekday + weekend* / *per-day*. |
| 2b | `schedule_times` | **Walk times** — one editable list of times per slot of the chosen mode. |
| 3 | `params` | **Parameters** — the table below. |
| — | `long_walk` | Shown **only** when the average walk duration exceeds 30 min: a warning that must be confirmed. Declining returns to step 3 with the entered values still in the form. |

Step 2 is two forms rather than one: Home Assistant renders a form from a fixed schema, so the
mode has to be submitted before the form can adapt to it. The wizard's step 2b therefore shows
exactly the fields the chosen mode uses — one list, two lists, or seven.

Walk times are entered as `HH:MM` in the Home Assistant local timezone. They are deduplicated and
sorted on save. A slot may be left empty (no weekend walks, say) as long as the week contains at
least one walk.

All of steps 2–3 (including the schedule mode) are editable later via the options flow, which
reuses the same steps and therefore validates identically. Changing an option **reloads** the
config entry. The **location is entry data, not an option**: it is set once in the wizard.

## Options

| Option | Key | Type | Default | Notes |
|---|---|---|---|---|
| Alert radius around home | `radius_km` | float, 4–15, 0.5 steps | **5.0 km** | Derived from measured source resolutions — the minimum guarantees the sampled disc spans ≥ 1 full ICON-EU cell; see [ARCHITECTURE.md](ARCHITECTURE.md) § Alert radius decision |
| Intensity threshold | `intensity_threshold` | `light` \| `moderate` \| `heavy` | **`light`** (≥ 0.1 mm/h) | Scale in [DATA_SOURCES.md](DATA_SOURCES.md); a slot counts as rainy when the consensus intensity reaches the chosen class |
| Earlier margin | `earlier_margin_min` | int minutes, 0–180, 10-min steps | 60 | How far back to search for a dry window |
| Later margin | `later_margin_min` | int minutes, 0–180, 10-min steps | 30 | How far forward to search |
| Average walk duration | `walk_duration_min` | int minutes, 5–240, 5-min steps | **required, no default** | Values over 30 min require confirming the `long_walk` warning (nowcast reliability) |
| Notification device | `notify_service` | string | *(unset)* | A `notify.mobile_app_*` service, stored **without** the `notify.` prefix. Registered services are offered in a dropdown; a custom value is accepted so a device that has not registered yet can be configured ahead of time. Optional — unset means no push notification. |
| Fire custom event | `fire_event` | bool | `false` | Emits `walk_the_dog_alert`; payload documented below |
| Auto-mute entity | `auto_mute_entity` | entity id | *(unset)* | Optional `person`/`device_tracker`; alerts suppressed while it is not `home` |

Optional options that are left empty are **absent** from the stored options, never stored as
`null` — clearing a field in the options flow really removes it.

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

`entry.data` — set once by the wizard:

```json
{
  "location": { "latitude": 52.2297, "longitude": 21.0122 }
}
```

`entry.options` — everything the options flow can change:

```json
{
  "schedule_mode": "weekday_weekend",
  "schedule": { "weekday": ["07:00", "18:30"], "weekend": ["09:00"] },
  "radius_km": 5.0,
  "intensity_threshold": "light",
  "earlier_margin_min": 60,
  "later_margin_min": 30,
  "walk_duration_min": 30,
  "fire_event": false,
  "notify_service": "mobile_app_phone",
  "auto_mute_entity": "person.owner"
}
```

The keys inside `schedule` depend on `schedule_mode` — nothing is stored that the chosen mode
does not mean:

| `schedule_mode` | `schedule` keys |
|---|---|
| `daily` | `all` |
| `weekday_weekend` | `weekday`, `weekend` |
| `per_day` | `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` |

`schedule.py` owns this shape: `normalize_schedule()` produces it and `expand()` is the single
place that turns any mode into per-weekday walk times (Monday = 0, matching
`datetime.weekday()`). Config entry version: **1**.
