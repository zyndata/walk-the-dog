"""MET Norway adapter: failover-only behaviour, caching terms, parsing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.walk_the_dog.const import (
    INTENSITY_THRESHOLD_LIGHT,
    SOURCE_METNO,
    intensity_class,
)
from custom_components.walk_the_dog.sources.base import (
    STATE_DISABLED,
    STATE_FAILED,
    STATE_OK,
    SampleGeometry,
)
from custom_components.walk_the_dog.sources.met_norway import (
    FORECAST_HOURS,
    MIN_INTERVAL_S,
    STEP_S,
    URL,
    MetNorwayAdapter,
    parse_compact,
)

from .conftest import load_fixture

UA = "walk_the_dog/test (+https://github.com/zyndata/walk-the-dog)"
NOW = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 8, 25, 6, 28, 37, tzinfo=UTC)


def _enabled() -> MetNorwayAdapter:
    adapter = MetNorwayAdapter(UA)
    adapter.enabled = True
    return adapter


def test_parse_uses_the_real_upstream_run_time() -> None:
    """Unlike Open-Meteo, Locationforecast publishes when it was produced."""
    series = parse_compact(load_fixture("met_norway", "compact.json"), NOW)

    assert series.source_id == SOURCE_METNO
    assert series.issued_at == UPDATED_AT
    assert series.fetched_at == NOW
    assert series.age_s(NOW) == 1883
    assert not series.is_stale(NOW)


def test_parse_reads_next_one_hours_as_millimetres_per_hour() -> None:
    """`precipitation_amount` over the coming hour is already a rate."""
    series = parse_compact(load_fixture("met_norway", "compact.json"), NOW)

    assert series.step_s == STEP_S
    assert len(series.slots) == FORECAST_HOURS
    assert series.slots == tuple(sorted(series.slots))
    assert all(value >= 0.0 for _, value in series.slots)
    assert series.cell_km == 10.0
    assert series.reliability == 0.70


def test_parse_classifies_a_synthetic_light_hour() -> None:
    """A 0.4 mm hour is light rain on the common scale."""
    series = parse_compact(
        {
            "properties": {
                "meta": {"updated_at": "2026-08-25T06:00:00Z"},
                "timeseries": [
                    {
                        "time": "2026-08-25T07:00:00Z",
                        "data": {"next_1_hours": {"details": {"precipitation_amount": 0.4}}},
                    }
                ],
            }
        },
        NOW,
    )
    assert series.slots[0][1] == pytest.approx(0.4)
    assert intensity_class(series.slots[0][1]) == INTENSITY_THRESHOLD_LIGHT


def test_parse_skips_steps_without_an_hourly_block() -> None:
    """Beyond ~63 h the forecast is 6-hourly; those steps are dropped, not interpolated."""
    series = parse_compact(
        {
            "properties": {
                "meta": {"updated_at": "2026-08-25T06:00:00Z"},
                "timeseries": [
                    {
                        "time": "2026-08-25T07:00:00Z",
                        "data": {"next_1_hours": {"details": {"precipitation_amount": 0.1}}},
                    },
                    {"time": "2026-08-25T08:00:00Z", "data": {"next_6_hours": {"details": {}}}},
                    {
                        "time": "2026-08-25T09:00:00Z",
                        "data": {"next_1_hours": {"details": {"precipitation_amount": 0.2}}},
                    },
                ],
            }
        },
        NOW,
    )
    assert [t.hour for t, _ in series.slots] == [7, 9]


@pytest.mark.parametrize(
    "payload",
    ["nope", {}, {"properties": {}}, {"properties": {"timeseries": []}}],
)
def test_parse_rejects_unusable_payloads(payload: object) -> None:
    """A response with no hourly precipitation raises rather than reporting dry."""
    with pytest.raises(ValueError):
        parse_compact(payload, NOW)


# --- failover behaviour ----------------------------------------------------------


async def test_dormant_adapter_makes_no_request(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """While Open-Meteo is healthy MET Norway must cost nothing at all."""
    adapter = MetNorwayAdapter(UA)

    assert not adapter.should_fetch(now)
    result = await adapter.fetch(async_get_clientsession(hass), geometry, now)

    assert len(aioclient_mock.mock_calls) == 0
    assert result.series == ()
    assert result.statuses[0].state == STATE_DISABLED
    assert adapter.cached(now).statuses[0].state == STATE_DISABLED


async def test_fetch_samples_the_centre_point_only(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """One coordinate: its ~10 km cell already covers any permitted radius."""
    aioclient_mock.get(URL, json=load_fixture("met_norway", "compact.json"))

    result = await _enabled().fetch(async_get_clientsession(hass), geometry, now)

    assert len(aioclient_mock.mock_calls) == 1
    query = aioclient_mock.mock_calls[0][1].query
    assert query["lat"] == f"{geometry.latitude:.4f}"
    assert query["lon"] == f"{geometry.longitude:.4f}"
    assert result.ok


async def test_fetch_sends_the_mandatory_identifying_user_agent(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """MET Norway's terms require it, and reject requests without it."""
    aioclient_mock.get(URL, json=load_fixture("met_norway", "compact.json"))

    await _enabled().fetch(async_get_clientsession(hass), geometry, now)

    headers = aioclient_mock.mock_calls[0][3]
    assert headers["User-Agent"] == UA
    assert "github.com" in headers["User-Agent"]


async def test_repeat_requests_send_if_modified_since(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """Required by the terms; a 304 then costs no body at all."""
    last_modified = "Tue, 25 Aug 2026 06:59:21 GMT"
    aioclient_mock.get(
        URL,
        json=load_fixture("met_norway", "compact.json"),
        headers={"Last-Modified": last_modified},
    )
    adapter = _enabled()
    session = async_get_clientsession(hass)
    await adapter.fetch(session, geometry, now)

    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, status=304)
    result = await adapter.fetch(session, geometry, now + timedelta(minutes=30))

    assert aioclient_mock.mock_calls[0][3]["If-Modified-Since"] == last_modified
    # The cached series is still the current one, and still contributes.
    assert result.series
    assert result.statuses[0].state == STATE_OK


async def test_expires_and_the_ten_minute_floor_are_honoured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """No poll before `Expires`, and never more often than every ten minutes."""
    aioclient_mock.get(
        URL,
        json=load_fixture("met_norway", "compact.json"),
        headers={"Expires": "Tue, 25 Aug 2026 07:30:00 GMT"},
    )
    adapter = _enabled()
    await adapter.fetch(async_get_clientsession(hass), geometry, now)

    assert not adapter.should_fetch(now + timedelta(seconds=MIN_INTERVAL_S - 1))
    assert not adapter.should_fetch(now + timedelta(minutes=20))  # still before Expires
    assert adapter.should_fetch(now + timedelta(minutes=31))


async def test_stays_inside_the_two_requests_per_hour_budget(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
) -> None:
    """The terms ask us to conserve requests; the budget allows two per active hour."""
    aioclient_mock.get(URL, json=load_fixture("met_norway", "compact.json"))
    adapter = _enabled()
    session = async_get_clientsession(hass)

    for minute in range(0, 60, 10):
        moment = now + timedelta(minutes=minute)
        if adapter.should_fetch(moment):
            await adapter.fetch(session, geometry, moment)

    assert len(aioclient_mock.mock_calls) <= 2


@pytest.mark.parametrize(
    "kwargs", [{"status": 403}, {"status": 500}, {"exc": TimeoutError()}, {"text": "nope"}]
)
async def test_failures_are_reported_not_raised(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    geometry: SampleGeometry,
    now: datetime,
    kwargs: dict,
) -> None:
    """The last independent provider failing still must not break the cycle."""
    aioclient_mock.get(URL, **kwargs)

    result = await _enabled().fetch(async_get_clientsession(hass), geometry, now)

    assert not result.ok
    assert result.statuses[0].state == STATE_FAILED
