from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional


ARTICLE_TOTAL_DETAIL_ENDPOINT = "https://api.weixin.qq.com/datacube/getarticletotaldetail"
ARTICLE_TOTAL_DETAIL_DOC = "https://developers.weixin.qq.com/doc/subscription/api/wedata/news/api_getarticletotaldetail.html"


def validate_date_range(begin_date: str, end_date: str) -> tuple[date, date]:
    begin = datetime.strptime(begin_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if begin > end:
        raise ValueError("begin_date cannot be later than end_date")
    if (end - begin).days > 29:
        raise ValueError("getarticletotaldetail supports at most a 30-day range")
    return begin, end


def each_day(begin: date, end: date) -> Iterable[date]:
    current = begin
    while current <= end:
        yield current
        current += timedelta(days=1)


def iter_metric_items(raw: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for key in ("list", "item", "items", "article_data", "data"):
        value = raw.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
            return

    data = raw.get("data")
    if isinstance(data, dict):
        for key in ("list", "item", "items", "article_data"):
            value = data.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return


def pick_int(item: dict[str, Any], keys: list[str]) -> Optional[int]:
    for key in keys:
        value = item.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def normalize_title(title: str) -> str:
    return "".join(str(title or "").split()).strip("｜|-")


def normalize_metric_item(item: dict[str, Any]) -> dict[str, Any]:
    title = item.get("title") or item.get("article_title") or item.get("msg_title") or ""
    publish_date = item.get("ref_date") or item.get("publish_date") or item.get("stat_date") or ""
    details = item.get("details") or item.get("detail") or item.get("daily_details") or item.get("detail_list") or []
    if not isinstance(details, list):
        details = []

    def sum_details(keys: list[str]) -> Optional[int]:
        values: list[int] = []
        for detail in details:
            if isinstance(detail, dict):
                value = pick_int(detail, keys)
                if value is not None:
                    values.append(value)
        return sum(values) if values else None

    reads = sum_details(["read_user", "int_page_read_count", "read_count", "page_read_count", "total_read_count"])
    shares = sum_details(["share_user", "share_count", "share_user_count"])
    favorites = sum_details(["collection_user", "add_to_fav_count", "fav_count", "favorite_count"])
    likes = sum_details(["like_user", "like_count", "old_like_count", "ori_like_count"])
    zaikan = sum_details(["zaikan_user", "zaikan_count"])
    comments = sum_details(["comment_count"])

    finish_rates: list[float] = []
    avg_active_times: list[float] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if detail.get("read_finish_rate") is not None:
            try:
                finish_rates.append(float(detail["read_finish_rate"]))
            except (TypeError, ValueError):
                pass
        if detail.get("read_avg_activetime") is not None:
            try:
                avg_active_times.append(float(detail["read_avg_activetime"]))
            except (TypeError, ValueError):
                pass

    if reads is None:
        reads = pick_int(item, ["read_user", "int_page_read_count", "read_count", "page_read_count", "total_read_count"])
    if shares is None:
        shares = pick_int(item, ["share_user", "share_count", "share_user_count"])
    if favorites is None:
        favorites = pick_int(item, ["collection_user", "add_to_fav_count", "fav_count", "favorite_count"])
    if likes is None:
        likes = pick_int(item, ["like_user", "like_count", "old_like_count", "ori_like_count"])
    if zaikan is None:
        zaikan = pick_int(item, ["zaikan_user", "zaikan_count"])
    if comments is None:
        comments = pick_int(item, ["comment_count"])

    return {
        "title": title,
        "title_key": normalize_title(title),
        "publish_date": publish_date,
        "msgid": item.get("msgid") or item.get("msg_id") or item.get("article_id") or "",
        "reads": reads,
        "shares": shares,
        "favorites": favorites,
        "likes": likes,
        "zaikan": zaikan,
        "comments": comments,
        "read_finish_rate": round(sum(finish_rates) / len(finish_rates), 6) if finish_rates else None,
        "read_avg_activetime": round(sum(avg_active_times) / len(avg_active_times), 6) if avg_active_times else None,
        "details_count": len(details),
        "raw": item,
    }


def build_article_total_detail_result(
    begin_date: str,
    end_date: str,
    articles: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    raw_daily: list[dict[str, Any]],
) -> dict[str, Any]:
    dedup: dict[str, dict[str, Any]] = {}
    for item in articles:
        key = f"{item.get('publish_date')}::{item.get('msgid') or item.get('title_key')}"
        dedup[key] = item

    merged_articles = list(dedup.values())
    return {
        "ok": len(failures) == 0,
        "partial_ok": bool(merged_articles) and bool(failures),
        "endpoint": ARTICLE_TOTAL_DETAIL_ENDPOINT,
        "doc": ARTICLE_TOTAL_DETAIL_DOC,
        "begin_date": begin_date,
        "end_date": end_date,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "errcode": 0 if not failures else failures[0].get("errcode"),
        "errmsg": "" if not failures else failures[0].get("errmsg", ""),
        "article_count": len(merged_articles),
        "articles": merged_articles,
        "failures": failures,
        "raw_daily": raw_daily,
    }
