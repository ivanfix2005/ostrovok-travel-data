import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from travel_data import Settings, TravelDataClient
from travel_data.demo import FIXED_NOW
from travel_data.validation import assert_response

UID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def hotel_request(request_for):
    return request_for(
        "search_hotels",
        meal=None,
        pets_required=False,
        free_cancellation_required=False,
        max_total_price_minor=None,
    )


@pytest.fixture
def bnovo_settings(tmp_path):
    path = tmp_path / "hotels.json"
    path.write_text(
        json.dumps(
            [{"uid": UID, "city": "Сочи", "country_code": "RU", "official_url": "https://hotel.example/"}]
        ),
        encoding="utf-8",
    )
    return Settings(hotel_provider="bnovo_public", bnovo_hotels_file=str(path))


def page(uid=UID, *, closed=False, price="35000", caption="за 7 ночей", city="Сочи", page_city=None):
    rooms = {"100": {"name": "Тестовый семейный номер", "city": city, "address": "Тестовая улица, 1"}}
    head = f'''<html><script>
      const ADULTS = 2; const CHILDREN = [7];
      var dfrom = '10-11-2026'; var dto = '17-11-2026';
      var account_currency_iso4217 = 'RUB'; var current_currency_iso4217 = '';
      const $availableRoomTypes = {json.dumps(rooms)};
      </script><input id="hotel_uid" value="{uid}">
      <input id="hotel_name" value="Вымышленный отель для тестов">'''
    if page_city:
        head += f'<div class="header__infoItem_name">{page_city}, Тестовая улица</div>'
    if closed:
        return head + '<div class="tariff__nodates">Здесь нет свободных мест на эти даты</div></html>'
    return (
        head
        + f'''
      <div class="tariff plan_row" data-plan-id="20">
       <div class="tariff__descriptionFood">Без питания</div>
       <div class="tariff__descriptionCancel" data-popup-content="#cancel20">Бесплатная отмена</div>
       <div id="cancel20">Бесплатная отмена за 7 дней до заезда.</div>
       <table><tbody data-real-room-id="100"><tr data-plan-id="20" data-available="1">
        <td><div class="guest-list">
         <div class="guest-list__item"><i class="bnovobook-icon--adult"></i></div>
         <div class="guest-list__item"><i class="bnovobook-icon--adult"></i></div>
         <div class="guest-list__item"><i class="bnovobook-icon--child"></i></div>
        </div></td>
        <td class="tariff__price"><span data-tariff-price="{price}">Цена</span>
          <div class="tariff__priceCaption">{caption}</div></td>
        <td><select data-room-id="100_child7" data-real-room-id="100" data-plan-id="20"
          data-accommodation-type="0" data-minprice="{price}" data-maxprice="{price}">
          <option value="0">Выбрать</option><option value="1">1</option></select></td>
       </tr></tbody></table></div></html>'''
    )


def invoke(run, request, settings, handler):
    async def go():
        async with TravelDataClient(
            settings, transport=httpx.MockTransport(handler), clock=lambda: FIXED_NOW
        ) as client:
            return await client.execute(request)

    return run(go())


def test_public_hotel_needs_no_credentials_and_returns_full_stay_quote(run, hotel_request, bnovo_settings):
    def handle(request):
        assert request.method == "GET"
        assert request.url.params["dfrom"] == "10-11-2026"
        assert request.url.params["dto"] == "17-11-2026"
        assert request.url.params["adults"] == "2"
        assert json.loads(request.url.params["children"]) == [7]
        assert "authorization" not in request.headers
        return httpx.Response(200, text=page())

    result = invoke(run, hotel_request, bnovo_settings, handle)
    assert result["status"] == "ok" and result["meta"]["is_fixture"] is False
    assert result["meta"]["source_name"] == "bnovo_public"
    item = result["data"]["items"][0]
    assert item["total_price"]["amount_minor"] == 3_500_000
    assert item["total_price"]["basis"] == "requested_guests_total"
    assert item["total_price"]["includes_mandatory_fees"] is None
    assert item["match_status"] == "requires_confirmation"
    assert "dfrom=10-11-2026" in item["source_url"]
    assert_response(hotel_request, result)


@pytest.mark.parametrize(
    "old,new",
    [
        ("'10-11-2026'", "'11-11-2026'"),
        ("ADULTS = 2", "ADULTS = 3"),
        ("CHILDREN = [7]", "CHILDREN = [8]"),
        (f'value="{UID}"', 'value="wrong-property"'),
        ("ADULTS = 2", "ADULTS = true"),
    ],
)
def test_source_cannot_silently_change_requested_dates_guests_or_property(
    old, new, run, hotel_request, bnovo_settings
):
    result = invoke(
        run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=page().replace(old, new))
    )
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


def test_explicit_no_availability_is_empty(run, hotel_request, bnovo_settings):
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=page(closed=True)))
    assert result["status"] == "empty" and result["error"] is None
    assert_response(hotel_request, result)


def test_requiring_multiple_rooms_is_not_an_offer_for_one_room(run, hotel_request, bnovo_settings):
    html = page(closed=True).replace(
        "Здесь нет свободных мест на эти даты",
        "Для размещения выбранного числа гостей необходимо выбрать несколько номеров.",
    )
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=html))
    assert result["status"] == "empty"


@pytest.mark.parametrize("city,expected", [("Сочи", "ok"), ("Москва", "empty")])
def test_live_page_city_can_fill_missing_room_city_without_using_requested_city(
    city, expected, run, hotel_request, bnovo_settings
):
    html = page(city="", page_city=city)
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=html))
    assert result["status"] == expected


def test_captcha_or_changed_page_is_not_empty(run, hotel_request, bnovo_settings):
    result = invoke(
        run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text="<html>CAPTCHA</html>")
    )
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


@pytest.mark.parametrize(
    "attribute", ['class="room d-none"', "hidden", 'aria-hidden="true"', 'style="display: none"']
)
def test_hidden_room_is_not_an_available_offer(attribute, run, hotel_request, bnovo_settings):
    html = page().replace(
        '<div class="tariff plan_row"', f'<section {attribute}><div class="tariff plan_row"'
    )
    html = html.replace("</html>", "</section></html>")
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=html))
    assert result["status"] == "empty"


def test_hidden_cheaper_row_cannot_replace_visible_quote(run, hotel_request, bnovo_settings):
    html = page()
    row = html[html.index("<tr data-plan-id") : html.index("</tr>") + 5]
    hidden = row.replace("<tr data-plan-id", '<tr class="d-none" data-plan-id').replace("35000", "1000")
    html = html.replace("</tbody>", hidden + "</tbody>")
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=html))
    assert result["status"] == "ok" and result["meta"]["returned_count"] == 1
    assert result["data"]["items"][0]["total_price"]["amount_minor"] == 3_500_000


def test_nightly_price_is_never_multiplied_into_total(run, hotel_request, bnovo_settings):
    result = invoke(
        run,
        hotel_request,
        bnovo_settings,
        lambda _: httpx.Response(200, text=page(price="5000", caption="за 1 ночь")),
    )
    item = result["data"]["items"][0]
    assert item["total_price"] is None and item["match_status"] == "requires_confirmation"
    assert any(w["code"] == "PRICE_NOT_CONFIRMED" for w in result["warnings"])


def test_known_wrong_meal_excludes_offer(run, hotel_request, bnovo_settings):
    hotel_request["arguments"]["meal"] = "BB"
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=page()))
    assert result["status"] == "empty"


def test_relative_cancellation_and_conditional_pets_are_unknown(run, hotel_request, bnovo_settings):
    hotel_request["arguments"].update(pets_required=True, free_cancellation_required=True)
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=page()))
    item = result["data"]["items"][0]
    checks = {check["field"]: check["status"] for check in item["checks"]}
    assert checks["pets_required"] == checks["free_cancellation_required"] == "unknown"
    assert item["cancellation"]["free_until"] is None
    assert_response(hotel_request, result)


def test_quote_for_different_guest_count_is_excluded(run, hotel_request, bnovo_settings):
    html = page().replace('<div class="guest-list__item"><i class="bnovobook-icon--child"></i></div>', "")
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=html))
    assert result["status"] == "empty"


def test_one_broken_rate_does_not_destroy_valid_rate(run, hotel_request, bnovo_settings):
    html = page()
    row = html[html.index("<tr data-plan-id") : html.index("</tr>") + 5]
    html = html.replace(
        "</tbody>", row.replace('data-tariff-price="35000"', 'data-tariff-price="-1"') + "</tbody>"
    )
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=html))
    assert result["status"] == "partial" and result["meta"]["returned_count"] == 1


def test_unconfigured_city_has_explicit_coverage_error(run, hotel_request, bnovo_settings):
    hotel_request["arguments"]["location"]["city"] = "Москва"

    def handle(_):
        raise AssertionError("Unconfigured cities must not cause arbitrary HTTP requests")

    result = invoke(run, hotel_request, bnovo_settings, handle)
    assert result["error"]["code"] == "OUT_OF_COVERAGE"


def test_parallel_hotel_failures_have_one_shared_retry(run, hotel_request, bnovo_settings):
    path = Path(bnovo_settings.bnovo_hotels_file)
    catalogue = json.loads(path.read_text(encoding="utf-8"))
    rows = [dict(catalogue[0], uid=f"00000000-0000-0000-0000-{i:012d}") for i in (1, 2, 3)]
    path.write_text(json.dumps(rows), encoding="utf-8")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(503)

    result = invoke(run, hotel_request, bnovo_settings, handle)
    assert result["error"]["code"] == "SOURCE_UNAVAILABLE" and len(calls) == 4


def test_one_unavailable_property_returns_partial_not_fake_success(run, hotel_request, bnovo_settings):
    path = Path(bnovo_settings.bnovo_hotels_file)
    rows = json.loads(path.read_text(encoding="utf-8"))
    second = deepcopy(rows[0])
    second["uid"] = "00000000-0000-0000-0000-000000000002"
    path.write_text(json.dumps(rows + [second]), encoding="utf-8")

    def handle(request):
        if request.url.path.endswith(UID):
            return httpx.Response(200, text=page())
        return httpx.Response(403)

    result = invoke(run, hotel_request, bnovo_settings, handle)
    assert result["status"] == "partial" and result["meta"]["returned_count"] == 1


def test_source_json_is_parsed_without_executing_javascript(run, hotel_request, bnovo_settings):
    html = page().replace("const $availableRoomTypes = {", "const $availableRoomTypes = dangerousFunction({")
    result = invoke(run, hotel_request, bnovo_settings, lambda _: httpx.Response(200, text=html))
    assert result["error"]["code"] == "SOURCE_SCHEMA_CHANGED"


def test_public_hotel_provider_is_default_without_partner_keys():
    assert Settings().selected_hotel_provider == "bnovo_public"
    assert Settings(ostrovok_key_id="id", ostrovok_api_key="key").selected_hotel_provider == "ostrovok"
    assert (
        Settings(hotel_provider="bnovo_public", ostrovok_key_id="id").selected_hotel_provider
        == "bnovo_public"
    )
