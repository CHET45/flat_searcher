"""SS.com listing parsers."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from flat_searcher.models import ListingDetail, ListingPayload, ListingSummary
from flat_searcher.scraper.html_tree import HtmlNode, parse_html
from flat_searcher.scraper.parsing import (
    clean_text,
    parse_float,
    parse_floor,
    parse_int,
    parse_price_eur,
    parse_price_per_m2,
    split_address,
)


class SSListParser:
    def parse(self, html: str, base_url: str) -> list[ListingSummary]:
        root = parse_html(html)
        summaries = []
        for row in root.find_all(_is_listing_row):
            summary = self._parse_row(row, base_url)
            if summary is not None:
                summaries.append(summary)
        return summaries

    def next_page_url(self, html: str, base_url: str) -> str | None:
        root = parse_html(html)
        current_page = _active_navigation_page(root) or _page_number_from_url(base_url)
        numeric_candidates: list[tuple[int, str]] = []
        next_label_candidate: str | None = None

        for link in root.find_all(_is_navigation_link):
            href = link.attr("href")
            if not href:
                continue
            absolute_url = urljoin(base_url, href)
            target_page = _page_number_from_url(absolute_url)
            text = clean_text(link.text_content()) or ""
            normalized = text.lower()

            if any(word in normalized for word in _PREVIOUS_PAGE_WORDS):
                continue
            if any(word in normalized for word in _NEXT_PAGE_WORDS) or (link.attr("rel") or "").lower() == "next":
                if target_page > current_page:
                    next_label_candidate = absolute_url
                    continue
            if target_page > current_page:
                numeric_candidates.append((target_page, absolute_url))

        if next_label_candidate is not None:
            return next_label_candidate
        if numeric_candidates:
            return min(numeric_candidates, key=lambda item: item[0])[1]
        return None

    def max_navigation_page(self, html: str, base_url: str) -> int:
        root = parse_html(html)
        pages = [_active_navigation_page(root) or _page_number_from_url(base_url)]
        for link in root.find_all(_is_navigation_link):
            href = link.attr("href")
            if not href:
                continue
            pages.append(_page_number_from_url(urljoin(base_url, href)))
        return max(pages)

    def _parse_row(self, row: HtmlNode, base_url: str) -> ListingSummary | None:
        ss_id = row.attr("id")
        if not ss_id:
            return None
        ss_id = ss_id.removeprefix("tr_")

        cells = row.child_nodes("td")
        if len(cells) < 8:
            return None

        link = _first_listing_link(row)
        if link is None:
            return None

        location_lines = cells[3].text_content().splitlines()
        district = clean_text(location_lines[0]) if location_lines else None
        street = clean_text(location_lines[1]) if len(location_lines) > 1 else None
        floor, total_floors = parse_floor(cells[6].text_content())

        return ListingSummary(
            ss_id=ss_id,
            ss_url=urljoin(base_url, link.attr("href") or ""),
            title=clean_text(link.text_content()),
            district=district,
            street=street,
            declared_rooms_ss=parse_int(cells[4].text_content()),
            area_m2=parse_float(cells[5].text_content()),
            floor=floor,
            total_floors=total_floors,
            building_series=clean_text(cells[7].text_content()),
            price_eur=parse_price_eur(cells[-1].text_content()),
            table_metadata=_table_metadata_from_row(cells),
        )


class SSDetailParser:
    def parse(self, html: str, url: str) -> ListingDetail:
        root = parse_html(html)
        fields = _extract_detail_fields(root)
        description = _extract_description(root)
        raw_text = root.text_content()
        street, house_number = split_address(fields.get("Iela"))
        floor, total_floors = parse_floor(fields.get("Stāvs"))
        price_text = fields.get("Cena")

        return ListingDetail(
            ss_url=url,
            description_text=description,
            address_raw=fields.get("Iela"),
            district=fields.get("Rajons"),
            street=street,
            house_number=house_number,
            price_eur=parse_price_eur(price_text),
            price_per_m2=parse_price_per_m2(price_text),
            area_m2=parse_float(fields.get("Platība")),
            declared_rooms_ss=parse_int(fields.get("Istabas")),
            floor=floor,
            total_floors=total_floors,
            building_series=fields.get("Sērija"),
            building_type=fields.get("Mājas tips"),
            listing_date_text=_extract_listing_date(raw_text),
            unique_visits=_extract_unique_visits(raw_text),
            image_urls=tuple(_extract_image_urls(root)),
            detail_fields=fields,
            raw_text_snapshot=raw_text,
            raw_html=html,
        )


def merge_listing(summary: ListingSummary, detail: ListingDetail | None = None) -> ListingPayload:
    if detail is None:
        return ListingPayload(
            ss_id=summary.ss_id,
            ss_url=summary.ss_url,
            listing_title=summary.title,
            listing_summary_text=summary.title,
            listing_table_metadata=summary.table_metadata,
            district=summary.district,
            street=summary.street,
            price_eur=summary.price_eur,
            price_per_m2=summary.price_per_m2,
            area_m2=summary.area_m2,
            declared_rooms_ss=summary.declared_rooms_ss,
            floor=summary.floor,
            total_floors=summary.total_floors,
            building_series=summary.building_series,
        )

    return ListingPayload(
        ss_id=summary.ss_id,
        ss_url=summary.ss_url,
        listing_title=summary.title,
        listing_summary_text=summary.title,
        listing_table_metadata=summary.table_metadata,
        detail_fields=detail.detail_fields,
        address_raw=detail.address_raw,
        district=detail.district or summary.district,
        street=detail.street or summary.street,
        house_number=detail.house_number,
        price_eur=detail.price_eur if detail.price_eur is not None else summary.price_eur,
        price_per_m2=detail.price_per_m2
        if detail.price_per_m2 is not None
        else summary.price_per_m2,
        area_m2=detail.area_m2 if detail.area_m2 is not None else summary.area_m2,
        declared_rooms_ss=detail.declared_rooms_ss
        if detail.declared_rooms_ss is not None
        else summary.declared_rooms_ss,
        floor=detail.floor if detail.floor is not None else summary.floor,
        total_floors=detail.total_floors
        if detail.total_floors is not None
        else summary.total_floors,
        building_series=detail.building_series or summary.building_series,
        building_type=detail.building_type,
        listing_date_text=detail.listing_date_text,
        unique_visits=detail.unique_visits,
        description_text=detail.description_text,
        image_urls=detail.image_urls,
        raw_text_snapshot=detail.raw_text_snapshot,
        raw_html=detail.raw_html,
    )


def _is_listing_row(node: HtmlNode) -> bool:
    row_id = node.attr("id")
    return node.tag == "tr" and bool(row_id and row_id.startswith("tr_"))


_NEXT_PAGE_WORDS = frozenset(
    (
        "next",
        "nākam",
        "talak",
        "tālāk",
        "след",
        "dalje",
    )
)
_PREVIOUS_PAGE_WORDS = frozenset(
    (
        "prev",
        "iepriekš",
        "previous",
        "назад",
        "пред",
    )
)


def _is_navigation_link(node: HtmlNode) -> bool:
    return (
        node.tag == "a"
        and node.attr("href") is not None
        and (
            node.attr("name") == "nav_id"
            or node.has_class("navi")
            or (node.attr("rel") or "").lower() in {"next", "prev"}
            or "/page" in (node.attr("href") or "")
        )
    )


def _active_navigation_page(root: HtmlNode) -> int | None:
    node = root.first(lambda child: child.tag == "button" and child.has_class("navia"))
    if node is None:
        return None
    text = clean_text(node.text_content()) or ""
    if not text.isdigit():
        return None
    return int(text)


def _page_number_from_url(url: str) -> int:
    match = re.search(r"/page(\d+)\.html(?:$|[?#])", url)
    if match:
        return int(match.group(1))
    return 1


def _first_listing_link(node: HtmlNode) -> HtmlNode | None:
    text_link = node.first(
        lambda child: (
            child.tag == "a"
            and "/msg/" in (child.attr("href") or "")
            and (child.has_class("am") or bool(child.text_content()))
        )
    )
    if text_link is not None:
        return text_link
    return node.first(lambda child: child.tag == "a" and "/msg/" in (child.attr("href") or ""))


def _table_metadata_from_row(cells: list[HtmlNode]) -> dict[str, str]:
    names = [
        "selection",
        "thumbnail",
        "title",
        "location",
        "rooms",
        "area",
        "floor",
        "series",
        "price",
    ]
    return {
        names[index] if index < len(names) else f"cell_{index}": cells[index].text_content()
        for index in range(len(cells))
    }


def _extract_detail_fields(root: HtmlNode) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in root.find_all(lambda node: node.tag == "tr"):
        cells = row.child_nodes("td")
        if len(cells) < 2:
            continue
        label = cells[0].text_content().rstrip(":")
        if not label:
            continue
        value = _clean_detail_value(cells[1].text_content())
        if value:
            fields[label] = value
    return fields


def _clean_detail_value(value: str) -> str:
    value = re.sub(r"\s*\[\s*Karte\s*\]\s*", "", value)
    return clean_text(value) or ""


def _extract_description(root: HtmlNode) -> str | None:
    message = root.first(lambda node: node.tag == "div" and node.attr("id") == "msg_div_msg")
    if message is None:
        return None

    description_parts: list[str] = []
    for child in message.children:
        if isinstance(child, HtmlNode) and child.tag == "table" and child.has_class("options_list"):
            break
        if isinstance(child, HtmlNode):
            description_parts.append(child.text_content())
        else:
            description_parts.append(child)
    return clean_text("\n".join(part for part in description_parts if part))


def _extract_listing_date(raw_text: str) -> str | None:
    match = re.search(r"Datums:\s*([0-9.:\s]+)", raw_text)
    return clean_text(match.group(1)) if match else None


def _extract_unique_visits(raw_text: str) -> int | None:
    match = re.search(r"Unikālo apmeklējumu skaits:\s*(\d+)", raw_text)
    return int(match.group(1)) if match else None


def _extract_image_urls(root: HtmlNode) -> list[str]:
    image_urls: list[str] = []
    seen: set[str] = set()
    for link in root.find_all(lambda node: node.tag == "a"):
        href = link.attr("href") or ""
        if "i.ss.com/gallery" not in href:
            continue
        if not re.search(r"\.(?:jpg|jpeg|png|webp)(?:$|\?)", href, re.IGNORECASE):
            continue
        if href not in seen:
            seen.add(href)
            image_urls.append(href)
    return image_urls
