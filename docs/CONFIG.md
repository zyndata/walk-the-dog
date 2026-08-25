# Configuration

> Option semantics and defaults were finalized in phase 1; phase 5 implemented the flows and
> pinned the storage shape; phase 6 added the entities, the notification and the
> `walk_the_dog_alert` payload below; **per-walk notification targets** were added after the
> first live test (see `STATE.md`). This document describes what the code actually does.

Only **one** Walk the dog entry can exist per Home Assistant (`single_config_entry` in
`manifest.json`): one home, one schedule, one recommendation sensor. A second attempt aborts
with *"Already configured"*.

## Config flow wizard

| # | Step id | What it asks |
|---|---|---|
| 1 | `user` | **Location** — map picker (HA `LocationSelector`), pre-filled with the HA home coordinates. |
| 2a | `schedule_mode` | **Schedule type** — *same times daily* / *weekday + weekend* / *per-day*. |
| 2b | `schedule_times` | **Walk times** — one editable list of times per slot of the chosen mode. |
| 2c | `walk_target` | **Who gets this walk's alert** — repeated once per configured walk: its notification devices and its own mute switch. See [Per-walk alerts](#per-walk-alerts). |
| 3 | `params` | **Parameters** — the table below. |
| — | `long_walk` | Shown **only** when the average walk duration exceeds 30 min: a warning that must be confirmed. Declining returns to step 3 with the entered values still in the form. |

Step 2 is two forms rather than one: Home Assistant renders a form from a fixed schema, so the
mode has to be submitted before the form can adapt to it. The wizard's step 2b therefore shows
exactly the fields the chosen mode uses — one list, two lists, or seven.

Walk times are entered as `HH:MM` in the Home Assistant local timezone. They are deduplicated and
sorted on save. A slot may be left empty (no weekend walks, say) as long as the week contains at
least one walk.

Step 2c repeats: Home Assistant renders a form from a schema fixed before the form is shown, and
how many walks there are is not known until step 2b is submitted. One form per walk also keeps
every label translatable — a single form with one field per walk could only fall back to raw
storage keys. Each form's description names the walk it is asking about (`Walk 2 of 3: Monday to
Friday at 18:30`), so the sequence stays legible.

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
| Default notification device | `notify_service` | string | *(unset)* | A `notify.mobile_app_*` service, stored **without** the `notify.` prefix. Used for any walk that has no devices of its own. Registered services are offered in a dropdown; a custom value is accepted so a device that has not registered yet can be configured ahead of time. Optional — unset means no push notification. |
| Per-walk alerts | `walk_targets` | map | *(unset)* | One entry per walk the user configured something for. See [Per-walk alerts](#per-walk-alerts). |
| Fire custom event | `fire_event` | bool | `false` | Emits `walk_the_dog_alert`; payload documented below |
| Auto-mute entity | `auto_mute_entity` | entity id | *(unset)* | Optional `person`/`device_tracker`; alerts suppressed while it is not `home` |

Optional options that are left empty are **absent** from the stored options, never stored as
`null` — clearing a field in the options flow really removes it.

Internal, not user-facing: `lead_time` = 30 min — how long before `T − earlier_margin` polling
starts so the decision moment has fresh data (decision in
[ARCHITECTURE.md](ARCHITECTURE.md) § Coordinator scheduling & polling windows).

## Per-walk alerts

The morning walk and the evening walk are often not the same person's job, so **who is
interrupted is decided per walk, not per integration**. Step 2c asks about every configured walk
in turn.

| Field | Key | Type | Default | Notes |
|---|---|---|---|---|
| Notification devices | `notify_services` | list of `mobile_app_*` service names | *(empty)* | Every device in the list receives the same message. **Empty means "use `notify_service`"**, never "notify nobody" — silencing a walk is what the mute switch is for. Validated exactly like `notify_service`, custom values included. |
| Never alert about this walk | `mute` | bool | `false` | No push is ever sent for this walk. The sensor and the `walk_the_dog_alert` event still update (with `muted: true`), so automations keep working. |

A walk left at both defaults stores **nothing at all**, so `walk_targets` only ever holds walks
the user actually said something about, and an entry configured before this feature existed keeps
behaving exactly as it did.

### Storage key

A walk is identified by the **schedule slot key and the configured local time**, joined by `|`:

```json
"walk_targets": {
  "weekday|07:00": { "notify_services": ["mobile_app_anna"] },
  "weekday|18:30": { "notify_services": ["mobile_app_piotr"] },
  "weekend|09:00": { "mute": true }
}
```

The pair is what the user typed, so it survives a daylight-saving change that moves the walk's
UTC instant, and it distinguishes a 07:00 weekday walk from a 07:00 weekend walk. `schedule.py`
owns it: `target_key()` builds it and `Walk.target_key` reads it back.

**Deleting a walk time deletes its entry.** Step 2c prunes `walk_targets` down to the walks that
still exist, so a device list can never be inherited by a later walk that happens to land on the
same time.

## Entities

Exactly two, both on one service device named after the config entry.

| Entity | Id | What it is |
|---|---|---|
| Walk recommendation | `sensor.walk_the_dog_walk_recommendation` | What to do about the **next upcoming walk** — the walk the coordinator is currently watching |
| Alerting | `switch.walk_the_dog_alerting` | Master switch. Off means no timers, no requests, no cycles and no notifications. Default on; the position survives a restart |

### Sensor states

| State | Meaning |
|---|---|
| `ok` | The scheduled walk window looks dry — go as planned |
| `earlier` | Rain during the walk; a dry window of the full duration exists earlier |
| `later` | Same, but the dry window is later |
| `no_dry_window` | Rain during the walk and no dry window anywhere within the margins |
| `unknown` | No source reaches the walk, or there is no walk to reach. **Never good news** |

`unavailable` means the coordinator itself has no data — a bug or a failed setup, not a
forecast outcome.

### Sensor attributes

Every key of the event payload below, plus four about the integration's own state:
`alerting` (the switch), `polling` (inside a walk window right now), `failover` (MET Norway is
standing in for Open-Meteo), `last_fetch` (ISO-8601 UTC), and `attribution` — the licence
credits of the sources that actually contributed, which [DATA_SOURCES.md](DATA_SOURCES.md)
obliges the integration to show.

## Notification behavior

Fires at `T − earlier_margin` — the last moment at which "go earlier" is still actionable. The
update cycle grid is anchored to the polling-window start, and `lead_time` is a whole number of
10-minute slots, so a cycle lands exactly on that moment whatever minute the walk is scheduled
at.

After the first message, every later cycle re-checks material change (defined precisely in
[ARCHITECTURE.md](ARCHITECTURE.md) § Material change) and stays silent unless something really
changed. Nothing is ever sent about a walk that looks dry: silence means "go as planned".

The message goes to the walk's own devices, or to `notify_service` when it has none. Every
device in the list gets the same message.

Suppressed entirely by: the enable switch being off, the walk's own mute switch, the auto-mute
entity not being `home`, or zero contributing sources. A muted alert is **suppressed, not
queued** — coming home does not release a message about a decision that has since moved on.

If a configured `notify.mobile_app_*` service is not registered (a phone configured before its
companion-app service existed), a warning is logged, the other devices are still notified, and
the cycle continues.

## Event payload

`walk_the_dog_alert` fires whenever a notification **would** fire — including when auto-mute
suppresses the push, because an automation may well want to know while nobody is home. It is
opt-in via the `fire_event` option. Times are ISO-8601 UTC.

```json
{
  "direction": "earlier",
  "scheduled_start": "2026-08-25T05:00:00+00:00",
  "recommended_start": "2026-08-25T04:30:00+00:00",
  "shift_min": -30,
  "duration_min": 30,
  "risk": 1.0,
  "confidence": 0.8,
  "expected_intensity": "moderate",
  "degraded": false,
  "horizon_limited": false,
  "data_age_s": 0,
  "muted": false,
  "sources": [
    {
      "source_id": "icon_eu",
      "state": "ok",
      "verdict": "wet",
      "contributed": true,
      "weight": 0.8,
      "age_s": 0,
      "peak_mm_h": 3.0,
      "peak_intensity": "moderate"
    }
  ]
}
```

| Key | Type | Meaning |
|---|---|---|
| `direction` | `none` \| `earlier` \| `later` \| `no_dry_window` \| `unknown` | The engine's word; the sensor renders `none` as `ok` |
| `scheduled_start` | ISO-8601 UTC \| `null` | The walk as configured |
| `recommended_start` | ISO-8601 UTC \| `null` | Where to move it; equals `scheduled_start` when the walk is already dry, `null` when there is nowhere to move it |
| `shift_min` | int \| `null` | Signed minutes; negative means earlier |
| `duration_min` | int \| `null` | `average_walk_duration` |
| `risk` | 0.0–1.0 \| `null` | Weighted fraction of sources predicting rain in the worst slot of the scheduled window. **Not a probability of rain** |
| `confidence` | 0.0–1.0 \| `null` | Agreement between sources, capped by how many actually voted |
| `expected_intensity` | `none` \| `light` \| `moderate` \| `heavy` \| `null` | Heaviest expected rain over the scheduled window |
| `degraded` | bool | Some slot rested on a single source |
| `horizon_limited` | bool | The walk reaches past what the sources forecast |
| `data_age_s` | int \| `null` | Age of the freshest source that voted |
| `muted` | bool | The push was suppressed for this alert — by the walk's own mute switch or by auto-mute |
| `sources` | list | One entry per source: its own verdict over the scheduled window, its status, its weight and its peak |

`risk`, `confidence` and `expected_intensity` are `null` — never `0` — when no source reaches
the scheduled window, so "we do not know" can never be read as "no rain".

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
  "auto_mute_entity": "person.owner",
  "walk_targets": {
    "weekday|07:00": { "notify_services": ["mobile_app_anna"] },
    "weekend|09:00": { "mute": true }
  }
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
