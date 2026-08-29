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
| 2c | `walk_target` | **Who is told about this walk** — repeated once per configured walk: the extra devices it notifies, its own mute switch and its own away entity. The step names the days and time it is asking about, so it is always clear which walk is being configured. See [Per-walk alerts](#per-walk-alerts). |
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
| Earlier margin | `earlier_margin_min` | int minutes, 0–180, 10-min steps | 60 | How far back to search for a dry window — **and when the notification arrives**. Values over 60 min require confirming the `beyond_radar` warning: the radars forecast 60 minutes ahead, so a message sent earlier than that can only rest on hourly models |
| Later margin | `later_margin_min` | int minutes, 0–180, 10-min steps | 30 | How far forward to search. Needs no warning: a later window is always re-checked as it comes into radar range, so a wide margin costs only a few more requests |
| Average walk duration | `walk_duration_min` | int minutes, 5–240, 5-min steps | **required, no default** | Values over 30 min require confirming the `long_walk` warning (nowcast reliability) |
| Second message shortly before you leave | `confirm_margin_min` | int minutes, 0–60, 5-min steps | **0 (off)** | Sends a second short message this many minutes before you set off: the plan still stands, or the rain has gone and the walk is back to its normal time. Goes to the same devices as the first message, and is only ever sent when something was already said about that walk |
| Always notify this device | `notify_service` | string | *(unset)* | A `notify.mobile_app_*` service, stored **without** the `notify.` prefix. **Receives every walk's alert.** Per-walk devices are notified *in addition* to it, never instead of it, and the combined list is de-duplicated so a device named in both places gets one push. Registered services are offered in a dropdown; a custom value is accepted so a device that has not registered yet can be configured ahead of time. Optional — unset means only the per-walk devices are notified. |
| Per-walk alerts | `walk_targets` | map | *(unset)* | One entry per walk the user configured something for. See [Per-walk alerts](#per-walk-alerts). |
| Fire custom event | `fire_event` | bool | `false` | Emits `walk_the_dog_alert`; payload documented below |
| Auto-mute entity | `auto_mute_entity` | entity id | *(unset)* | Optional `person`/`device_tracker`; pushes suppressed for **every** walk while it is not `home`. A walk that sets its own `away_entity` follows that one instead. |

Optional options that are left empty are **absent** from the stored options, never stored as
`null` — clearing a field in the options flow really removes it.

Internal, not user-facing: `lead_time` = 30 min — how long before `T − earlier_margin` polling
starts so the decision moment has fresh data (decision in
[ARCHITECTURE.md](ARCHITECTURE.md) § Coordinator scheduling & polling windows).

### Timings and what the radar can see

Both warning steps measure the same thing: the radar nowcasts reach **60 minutes** ahead
(`NOWCAST_HORIZON_MIN`) and nothing further, while the hourly models reach 12 hours. Past that
line the answer is still sound about *whether* it will rain, but vague about *when* — and *when*
is the whole question. So:

- `earlier_margin` ≤ 60 min gets a radar-backed answer the moment the notification is sent.
  Larger is allowed, and the `beyond_radar` step explains what it costs before storing it.
- `walk_duration` > 60 min can never be fully radar-backed at the moment you are told to go,
  because the far end of the walk is past the radar's reach whatever the margin is. The
  `long_walk` step warns from 30 min upwards.
- `later_margin` has no such limit. A window an hour ahead is one the radar will have seen long
  before you have to leave, which is exactly why the integration keeps watching instead of
  deciding once — see [ARCHITECTURE.md](ARCHITECTURE.md) § Coordinator scheduling.

A recommendation the radar has not yet reached is marked `provisional` in the payload, and the
push says so in words.

## Per-walk alerts

The morning walk and the evening walk are often not the same person's job, so **who is
interrupted is decided per walk, not per integration**. Step 2c asks about every configured walk
in turn.

| Field | Key | Type | Default | Notes |
|---|---|---|---|---|
| Also notify these devices | `notify_services` | list of `mobile_app_*` service names | *(empty)* | Extra devices for this walk, **on top of `notify_service`**, all receiving the same message. Empty means "only the always-notified device", never "notify nobody" — silencing a walk's own phones is what the mute switch is for. Validated exactly like `notify_service`, custom values included, and de-duplicated on store. |
| Do not notify this walk's phones | `mute` | bool | `false` | The devices in `notify_services` get nothing about this walk. **`notify_service` still does** — only the alerting switch silences that one. The sensor and the `walk_the_dog_alert` event keep updating, so automations keep working. |
| Skip this walk's phones while this person is away | `away_entity` | entity id | *(unset)* | Optional `person`/`device_tracker` that replaces `auto_mute_entity` **for this walk only**. It answers only for a phone that cannot answer for itself (see *Who is actually reached* below). Unset means the walk follows the entry-wide `auto_mute_entity`. |

A walk left at all three defaults stores **nothing at all**, so `walk_targets` only ever holds
walks the user actually said something about, and an entry configured before this feature existed
keeps behaving exactly as it did.

### Who is actually reached

Being addressed and being reached are two different things. `services_for` builds the list a walk
is addressed to; `recipients_for` decides, **one phone at a time**, which of them the push goes to
at this moment. Three rules, in this order:

1. **`notify_service` always hears.** Not the mute switch, not either away entity, not even its
   own tracker takes it off the list. It is the phone the user asked to be told about every walk,
   and the alerting switch — which stops the whole integration — is the only thing that silences
   it.
2. **A walk's own phones obey its `mute` switch.**
3. **Otherwise each phone answers for itself.** A companion-app device registers
   `notify.mobile_app_jan_phone` and `device_tracker.jan_phone` from the same device name, so the
   notifier reads that tracker: `home` is notified, anything else is skipped. **Only that phone is
   skipped** — one person leaving the house never takes the alert away from everybody else.

A phone that cannot answer — no tracker of that name, or a tracker reading `unknown` /
`unavailable` — falls through to the away entity that applies to it (`away_entity`, else
`auto_mute_entity`). If there is none, it is **notified**: a needless alert is a far cheaper
mistake than a missed one, so a phone is only ever skipped because a rule the user configured
said so.

`muted` in the event payload therefore means *nobody at all was reached*, not that one phone was
skipped. With an always-notified device configured it is effectively always `false`.

### One phone, one notification

The two places a device can be named — `notify_service` and a walk's `notify_services` — are
**added together**, not chosen between. Setting the same device in both is therefore easy to do
by accident, so the recipient list is de-duplicated twice over: `config_flow._collect_target`
normalizes and de-duplicates what one walk stores (a value typed as `notify.mobile_app_x`
collapses onto a `mobile_app_x` already picked from the dropdown), and
`WalkNotifier.services_for` de-duplicates the union at dispatch, which is what protects entries
stored before this rule existed. Either way one phone receives exactly one push, and the order
the user chose is preserved.

### Storage key

A walk is identified by the **schedule slot key and the configured local time**, joined by `|`:

```json
"walk_targets": {
  "weekday|07:00": { "notify_services": ["mobile_app_anna"], "away_entity": "person.anna" },
  "weekday|18:30": { "notify_services": ["mobile_app_piotr"], "away_entity": "person.piotr" },
  "weekend|09:00": { "mute": true }
}
```

The pair is what the user typed, so it survives a daylight-saving change that moves the walk's
UTC instant, and it distinguishes a 07:00 weekday walk from a 07:00 weekend walk. `schedule.py`
owns it: `target_key()` builds it and `Walk.target_key` reads it back.

**Deleting a walk time deletes its entry.** Step 2c prunes `walk_targets` down to the walks that
still exist, so a device list can never be inherited by a later walk that happens to land on the
same time.

## What it costs to run

Measured in phase 8 over a simulated day of four walks (`tests/test_performance.py`), for the
default settings — 5 km radius, 1 h earlier margin, 30 min later margin, a 30-minute walk:

| | Outside the CHMI radar area | Inside it (south-west Poland) |
|---|---|---|
| Requests per day | 156 | 372 |
| Data per day | ~0.4 MB | ~8 MB |
| Requests while a walk is not near, or while the switch is off | 0 | 0 |

The difference is the CHMI composites: the second radar is fetched as whole images, which is
what buys the extra precision in the corner of the country it covers. On a metered connection
that is the one number worth knowing before installing.

Memory and processor cost are far below what the weakest supported hardware has: under a
megabyte of extra memory per update and a few milliseconds of processor time
([ARCHITECTURE.md](ARCHITECTURE.md) § Resource budget).

## Entities

Three, on one service device named after the config entry. Each answers a different
question, which is why they are not one entity with more attributes.

| Entity | Id | What it is |
|---|---|---|
| Walk recommendation | `sensor.walk_the_dog_walk_recommendation` | What to do about the **next upcoming walk** — the walk the coordinator is currently watching |
| Walk window | `binary_sensor.walk_the_dog_walk_window` | Whether a walk window is open right now, i.e. whether cycles are running. `on` is exactly the sensor's `polling` attribute, given an entity of its own so an automation or a dashboard card can react to it without parsing a text state. Attributes: `scheduled_start`, `alerting` |
| Alerting | `switch.walk_the_dog_alerting` | Master switch. Off means no timers, no requests, no cycles and no notifications. Default on; the position survives a restart |

## Services

| Service | Fields | What it does |
|---|---|---|
| `walk_the_dog.walked` | none | Closes the walk being watched right now: no further advice about it, and no further weather requests for it. Alerting itself stays on and the next walk is picked up as usual. The *Already went* button on the notification calls the same thing. Not remembered across a restart — a Home Assistant restarted inside the window picks the walk back up |

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

### Per-source states

`sources[].state` in the attributes and the event payload:

| State | Meaning |
|---|---|
| `ok` | Fetched, fresh, and it voted on the walk |
| `stale` | Its data is older than 3× the publisher's own cadence, so it was dropped this cycle |
| `failed` | The provider could not be reached, or answered with something unusable |
| `out_of_range` | Fetched and fresh, but its forecast does not reach the walk — a radar nowcast stops at +60 min |
| `disabled` | Dormant on purpose: MET Norway while Open-Meteo is healthy |
| `not_applicable` | The source cannot serve **this location at all** and is never polled. Only the regional `chmi` radar reports it, for everywhere outside the Czech composite — which is most of Poland |

`not_applicable` is not a problem to fix: it is the normal, permanent state of a regional source
for a location it does not cover, and nothing about the recommendation is worse for it.

### Sensor attributes

Every key of the event payload below, plus these about the integration's own state:
`alerting` (the switch), `polling` (inside a walk window right now), `failover` (MET Norway is
standing in for Open-Meteo), `last_fetch` (ISO-8601 UTC), `requests_last_hour` and
`requests_hourly_cap` (see below), and `attribution` — the licence credits of the sources that
actually contributed, which [DATA_SOURCES.md](DATA_SOURCES.md) obliges the integration to show.

`requests_last_hour` / `requests_hourly_cap` total the per-source rolling budgets every adapter
polices itself against before it sends anything. A walk window can stay open for hours while a
"wait until" answer is re-checked, so what that costs the providers is published rather than
merely promised. The cap is the sum of every adapter's own hourly ceiling, dormant and
not-applicable sources included.

## Notification behavior

Fires at `T − earlier_margin` — the last moment at which "go earlier" is still actionable. The
update cycle grid is anchored to the polling-window start, and `lead_time` is a whole number of
10-minute slots, so a cycle lands exactly on that moment whatever minute the walk is scheduled
at.

After the first message, every later cycle re-checks material change (defined precisely in
[ARCHITECTURE.md](ARCHITECTURE.md) § Material change) and stays silent unless something really
changed. Nothing is ever sent about a walk that looks dry: silence means "go as planned".

**Nothing is ever sent about a moment that has passed.** The walk stays under watch after its
scheduled time — that is how a "wait until" answer given beyond the radar's reach gets confirmed
— but a suggestion expires when the time it names does, and `no_dry_window` expires when the
walk begins. A direction that flipped only because the clock moved is not re-announced either
(ARCHITECTURE.md § Material change, *Actionability*).

Every alert about one walk carries the same companion-app `tag`, keyed on the walk's UTC start,
so a revised recommendation **replaces** the message it supersedes on the phone rather than
stacking a second, contradictory one underneath it.

**Tapping the message opens the recommendation sensor**, whose attributes carry the whole
answer: both times, each source's own verdict, how far the radar reached. On Android the message
also stays in the notification shade after the tap, so the advice can be re-read rather than
disappearing into the app that was opened to look at it; it is still swipeable, and it is taken
down automatically at the end of the walk. iOS opens the same entity, and keeps its own copy in
the Notification Centre.

Every push carries one action button, **Already went**, which closes the walk the way the
`walk_the_dog.walked` service does. The walk's UTC start is encoded in the action identifier —
the one field both companion apps reliably hand back — so a leftover notification from yesterday
cannot close today's walk. Tapping it also sends `clear_notification` to the walk's other
devices; only the phone that was tapped dismisses its own copy.

With `confirm_margin_min` set, one further message goes out that many minutes before you set
off, provided something was already said about the walk. It says either that the plan still
stands, or that the rain has gone and the walk is back to its normal time. The second is the
reason the option exists: a `later` recommendation relaxing to "walk as planned" is not an alert
direction, so silence alone would leave you waiting for a window that stopped being necessary.
It is sent once per walk, and an alert that happens to land at that moment counts as it.

The message goes to `notify_service` together with the walk's own devices, each device named
once however many places it appears in, minus the phones their own trackers report as away. Every
device that is reached gets the same message.

Suppressed entirely by: the enable switch being off, or zero contributing sources. Everything
else — the mute switch, both away entities, a phone's own tracker — decides *which phones* are
reached rather than whether the walk speaks at all (§ Who is actually reached). A suppressed
alert is **suppressed, not queued** — coming home does not release a message about a decision
that has since moved on.

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
  "recommended_end": "2026-08-25T05:00:00+00:00",
  "shift_min": -30,
  "duration_min": 30,
  "risk": 1.0,
  "confidence": 0.8,
  "expected_intensity": "moderate",
  "degraded": false,
  "horizon_limited": false,
  "provisional": false,
  "data_age_s": 0,
  "muted": false,
  "confirmation": false,
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
| `recommended_start` | ISO-8601 UTC \| `null` | Where to move it; equals `scheduled_start` when the walk is already dry, `null` when there is nowhere to move it. Never a moment that has already passed |
| `recommended_end` | ISO-8601 UTC \| `null` | `recommended_start + duration` — when the suggested walk gets home |
| `shift_min` | int \| `null` | Signed minutes; negative means earlier |
| `duration_min` | int \| `null` | `average_walk_duration` |
| `risk` | 0.0–1.0 \| `null` | Weighted fraction of sources predicting rain in the worst slot of the scheduled window. **Not a probability of rain** |
| `confidence` | 0.0–1.0 \| `null` | Agreement between sources, capped by how many actually voted |
| `expected_intensity` | `none` \| `light` \| `moderate` \| `heavy` \| `null` | Heaviest expected rain over the scheduled window |
| `degraded` | bool | Some slot rested on a single source |
| `horizon_limited` | bool | The walk reaches past what the sources forecast |
| `provisional` | bool | No radar reaches the recommended window, so the timing rests on hourly models. An early answer the coordinator keeps re-checking, not a final one |
| `data_age_s` | int \| `null` | Age of the freshest source that voted |
| `muted` | bool | Nobody was reached at all. One phone being skipped does not set it; an entry with an always-notified device effectively never does |
| `confirmation` | bool | This is the pre-departure reassurance rather than a new recommendation |
| `sources` | list | One entry per source: its own verdict over the scheduled window, its status, its weight and its peak |

`weight` is the source's static reliability decayed by how old its data is — except for `chmi`,
whose static weight is *also* scaled by how far the location is from the nearest Czech radar, so
the same source is worth less near the edge of its range than in the middle of it
([DATA_SOURCES.md](DATA_SOURCES.md) § CHMI).

`risk`, `confidence` and `expected_intensity` are `null` — never `0` — when no source reaches
the scheduled window, so "we do not know" can never be read as "no rain".

## Language

Everything the integration says is translated by Home Assistant itself, from
`custom_components/walk_the_dog/strings.json` — `translations/en.json` is the base file and
`translations/pl.json` the Polish one. Home Assistant serves whichever matches the user's own
language setting, so there is nothing to configure. A newly added translation only appears after
a **full restart**: reloading the integration does not clear the frontend's translation cache.

Translated:

- every wizard and options-flow title, field label, field description, warning and error;
- the integration's name — a Polish user sees **Idź już z psem** — and with it the device the
  entities hang off, because that name is the prefix Home Assistant puts in front of every
  entity's friendly name;
- the entity names, the sensor's four states, and the label of every attribute;
- the `walk_the_dog.walked` service, and the error it raises when there is no entry to act on;
- the push notification: title, all four message shapes, the provisional sentence and the
  **Already went** button. These live under the `common` key of `strings.json` — the only
  top-level key Home Assistant allows for prose belonging to no form and no entity — and are
  read at dispatch time in the user's language.

Deliberately **not** translated, because it is an identifier rather than prose:

- the domain `walk_the_dog`, the event name `walk_the_dog_alert`, the service name
  `walk_the_dog.walked`;
- **every key and every value in the event payload and in the sensor attributes.** `direction`
  is `earlier` in every language, so an automation written against it keeps working when the
  user switches language. The sensor's *state* is the one place those values are shown to a
  person, and Home Assistant translates them for display while the stored state stays English;
- `min` and `km` on the number fields — symbols, not words;
- the brand images, which carry the untranslated name (see [BRANDING.md](BRANDING.md)).

Entity IDs are generated by Home Assistant from the device and entity names *in the language the
integration was set up in*, so a Polish install gets Polish entity IDs. They are assigned once,
never change afterwards, and can be renamed in the entity registry.

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
    "weekday|07:00": { "notify_services": ["mobile_app_anna"], "away_entity": "person.anna" },
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
