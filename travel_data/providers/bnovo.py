"""Read public guest booking pages. No PMS credentials, booking or browser automation."""

import asyncio
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
from bs4 import BeautifulSoup

from ..errors import DataError
from ..models import Candidate, ProviderResult, warning
from ..utils import ensure_finite, minor_units, safe_url, stable_id, text

# Public widget identifiers published by each hotel's own website, not API secrets.
# Only these configured properties are searched; this is not a Russia-wide catalogue.
DEFAULT_HOTELS = (
    {
        "uid": "75f5942a-3b17-4c7d-88dc-70f3425c6d3b",
        "city": "Сочи",
        "country_code": "RU",
        "official_url": "https://serenity-premium.ru/",
    },
    {
        "uid": "0569a7c5-ca7d-44b6-adf2-2f25dff6e643",
        "city": "Сочи",
        "country_code": "RU",
        "official_url": "https://sochi-midgard.ru/",
    },
    {
        "uid": "854168d8-eaac-47d2-8972-11d1478f5515",
        "city": "Сочи",
        "country_code": "RU",
        "official_url": "https://solnechnyedali.ru/",
    },
)


def normalized_city(value):
    return value.strip().casefold().replace("ё", "е")


def hotel_catalog(settings):
    try:
        if settings.bnovo_hotels_file:
            path = Path(settings.bnovo_hotels_file)
            if path.stat().st_size > 1_000_000:
                raise ValueError()
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            rows = list(DEFAULT_HOTELS)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            raise ValueError()
        identities = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"uid", "city", "country_code", "official_url"}
                or not all(isinstance(v, str) for v in row.values())
                or not 1 <= len(row["city"].strip()) <= 100
                or not re.fullmatch(r"[A-Z]{2}", row["country_code"])
                or str(UUID(row["uid"])) != row["uid"]
                or row["uid"] in identities
                or safe_url(row["official_url"]) != row["official_url"]
            ):
                raise ValueError()
            identities.add(row["uid"])
        return rows
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        raise DataError("ACCESS_NOT_CONFIGURED", "Проверьте каталог BNOVO_HOTELS_FILE") from None


def source_json(html, name):
    match = re.search(r"\b(?:const|var|let)\s+" + re.escape(name) + r"\s*=\s*", html)
    if not match:
        raise ValueError()
    value, _ = json.JSONDecoder().raw_decode(html[match.end() :])
    ensure_finite(value)
    return value


def source_string(html, name):
    match = re.search(r"\bvar\s+" + re.escape(name) + r"\s*=\s*'([^']*)'\s*;", html)
    if not match:
        raise ValueError()
    return match[1]


class BnovoHotels:
    name = "bnovo_public"

    def __init__(self, settings, network):
        self.settings, self.network = settings, network

    async def fetch(self, args, deadline):
        hotels = [
            row
            for row in hotel_catalog(self.settings)
            if row["country_code"] == args["location"]["country_code"]
            and normalized_city(row["city"]) == normalized_city(args["location"]["city"])
        ]
        if not hotels:
            raise DataError(
                "OUT_OF_COVERAGE", "В каталоге Bnovo нет подключённых отелей этого города", field="location"
            )
        start = date.fromisoformat(args["check_in"])
        end = date.fromisoformat(args["check_out"])
        params = {
            "dfrom": start.strftime("%d-%m-%Y"),
            "dto": end.strftime("%d-%m-%Y"),
            "adults": args["guests"]["adults"],
            "children": json.dumps(args["guests"]["children_ages"], separators=(",", ":")),
            "lang": "ru",
            "currency": "RUB",
        }

        async def load(hotel):
            url = self.settings.bnovo_base_url.rstrip("/") + "/" + hotel["uid"]
            try:
                html = await self.network.text(
                    "GET",
                    url,
                    deadline=deadline,
                    params=params,
                    headers={"User-Agent": "OdinZaprosHackathon-TravelData/1.0"},
                )
                if len(html.encode("utf-8")) > 2_000_000:
                    raise DataError("SOURCE_SCHEMA_CHANGED", "Страница Bnovo превышает ограничение размера")
                return await asyncio.to_thread(
                    self.parse, html, args, hotel, str(httpx.URL(url, params=params))
                )
            except DataError as error:
                return error

        # One fixed catalogue page, host concurrency <= 2, same 15s function deadline.
        pages = await asyncio.gather(*(load(hotel) for hotel in hotels[:3]))
        errors = [page for page in pages if isinstance(page, DataError)]
        if len(errors) == len(pages):
            raise errors[0]
        result = ProviderResult(
            has_more=len(hotels) > 3,
            incomplete=bool(errors),
            warnings=[
                warning(
                    "LIMITED_CATALOG", "Поиск только по подключённым отелям Bnovo, не по всем отелям города"
                ),
                warning("SEARCH_QUOTE", "Цену и доступность необходимо перепроверить перед бронированием"),
                warning("FEES_NOT_CONFIRMED", "Полнота обязательных сборов не подтверждена"),
            ],
        )
        for page in pages:
            if isinstance(page, DataError):
                result.warnings.append(warning(page.code, "Не удалось получить данные части отелей Bnovo"))
            else:
                result.candidates.extend(page.candidates)
                result.warnings.extend(page.warnings)
                result.has_more |= page.has_more
                result.incomplete |= page.incomplete
        return result

    def parse(self, html, args, hotel, source_url):
        try:
            start, end = date.fromisoformat(args["check_in"]), date.fromisoformat(args["check_out"])
            if source_string(html, "dfrom") != start.strftime("%d-%m-%Y") or source_string(
                html, "dto"
            ) != end.strftime("%d-%m-%Y"):
                raise ValueError()
            adults, children = source_json(html, "ADULTS"), source_json(html, "CHILDREN")
            if (
                type(adults) is not int
                or not isinstance(children, list)
                or any(type(age) is not int for age in children)
            ):
                raise ValueError()
            if adults != args["guests"]["adults"] or sorted(children) != sorted(
                args["guests"]["children_ages"]
            ):
                raise ValueError()
            if source_string(html, "account_currency_iso4217") != "RUB" or source_string(
                html, "current_currency_iso4217"
            ) not in ("", "RUB"):
                raise DataError("UNSUPPORTED_REQUEST", "Bnovo не подтвердил исходный тариф в RUB")
            rooms = source_json(html, "$availableRoomTypes")
            if rooms == []:
                rooms = {}
            if not isinstance(rooms, dict):
                raise ValueError()
            soup = BeautifulSoup(html, "html.parser")
            identity = soup.find("input", id="hotel_uid")
            name = soup.find("input", id="hotel_name")
            if (
                not identity
                or identity.get("value") != hotel["uid"]
                or not name
                or not name.get("value", "").strip()
            ):
                raise ValueError()
            rows = soup.select('tbody[data-real-room-id] > tr[data-plan-id][data-available="1"]')
            if not rows:
                closed = soup.select(".tariff__nodates")
                if closed and all(
                    any(
                        marker in " ".join(el.get_text(" ", strip=True).lower().split())
                        for marker in ("нет свободных мест", "необходимо выбрать несколько номеров")
                    )
                    for el in closed
                ):
                    return ProviderResult()
                raise ValueError()
            location = soup.select_one(".header__infoItem_name")
            page_city = location.get_text(" ", strip=True).split(",", 1)[0] if location else None
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            raise DataError(
                "SOURCE_SCHEMA_CHANGED", "Bnovo не подтвердил даты, гостей или структуру страницы"
            ) from None

        result, bad, excluded = ProviderResult(has_more=len(rows) > 100), 0, 0
        for row in rows[:100]:
            try:
                candidate = self.offer(
                    row, rooms, args, name["value"], source_url, (end - start).days, hotel["uid"], page_city
                )
                if candidate is None:
                    excluded += 1
                else:
                    result.candidates.append(candidate)
                    if candidate.item["total_price"] is None:
                        result.warnings.append(
                            warning("PRICE_NOT_CONFIRMED", "Нет подтверждённой итоговой цены за весь срок")
                        )
            except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
                bad += 1
        if bad:
            result.incomplete = True
            result.warnings.append(warning("INVALID_RECORDS", f"Исключены повреждённые тарифы Bnovo: {bad}"))
            if not result.candidates and not excluded:
                raise DataError("SOURCE_SCHEMA_CHANGED", "Не удалось разобрать тарифы Bnovo")
        return result

    @staticmethod
    def offer(row, rooms, args, hotel_name, source_url, nights, uid, page_city):
        guest_list = row.select_one(".guest-list")
        selected = row.select_one("select[data-room-id][data-real-room-id]")
        plan = row.find_parent("div", class_="plan_row")
        if guest_list is None or selected is None or plan is None:
            raise ValueError()
        icons = [icon.get("class", []) for icon in guest_list.select(".guest-list__item > i")]
        adults = sum("bnovobook-icon--adult" in classes for classes in icons)
        children = sum(
            any(value.startswith("bnovobook-icon--child") for value in classes) for classes in icons
        )
        if adults != args["guests"]["adults"] or children != len(args["guests"]["children_ages"]):
            return None
        if selected.get("data-accommodation-type") != "0" or not selected.find("option", value="1"):
            return None
        base_id, room_id, plan_id = (
            selected["data-real-room-id"],
            selected["data-room-id"],
            row["data-plan-id"],
        )
        if (
            selected.get("data-plan-id") != plan_id
            or plan.get("data-plan-id") != plan_id
            or row.parent.get("data-real-room-id") != base_id
        ):
            raise ValueError()
        room = rooms[base_id]
        city = room.get("city") or page_city
        if not city or normalized_city(city) != normalized_city(args["location"]["city"]):
            return None
        if not isinstance(room.get("name"), str) or not room["name"].strip():
            raise ValueError()
        price = row.select_one("[data-tariff-price]")
        if price is None:
            raise ValueError()
        amount = minor_units(price["data-tariff-price"])
        if amount is None:
            raise ValueError()
        caption = row.select_one(".tariff__priceCaption")
        count = re.search(r"за\s+(\d+)\s+(?:ноч|сут)", caption.get_text(" ", strip=True)) if caption else None
        total = None
        if (
            count
            and int(count[1]) == nights
            and minor_units(selected.get("data-minprice")) == amount
            and minor_units(selected.get("data-maxprice")) == amount
        ):
            total = {
                "amount_minor": amount,
                "currency": "RUB",
                "basis": "requested_guests_total",
                "includes_mandatory_fees": None,
            }
        meal_tag = plan.select_one(".tariff__descriptionFood")
        meal_text = meal_tag.get_text(" ", strip=True).casefold() if meal_tag else ""
        meal = "RO" if meal_text == "без питания" else None
        cancel_tag = plan.select_one(".tariff__descriptionCancel")
        cancel_summary = None
        if cancel_tag:
            popup_id = cancel_tag.get("data-popup-content", "").removeprefix("#")
            popup = plan.find(id=popup_id) if popup_id else None
            if popup:
                cancel_summary = text(popup.get_text(" ", strip=True), 500)
        item = {
            "id": stable_id("bnovo_offer", uid, room_id, plan_id, args),
            "hotel_name": text(hotel_name.strip(), 300),
            "offer_id": text(f"{uid}:{room_id}:{plan_id}", 300),
            "room_name": text(room["name"], 300),
            "address": text(f"{city}, {room['address']}") if room.get("address") else None,
            "coordinates": None,
            "check_in": args["check_in"],
            "check_out": args["check_out"],
            "guests": args["guests"],
            "meal": meal,
            "total_price": total,
            "cancellation": {"free_until": None, "summary": cancel_summary} if cancel_summary else None,
            "pets_allowed": None,
            "match_status": "requires_confirmation",
            "checks": [],
            "source_url": source_url,
        }
        # Relative/free-text cancellation and conditional pet policies don't prove exact eligibility.
        return Candidate(item, {"free_cancellation_available": None})
