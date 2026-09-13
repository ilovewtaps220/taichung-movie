from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_PATHS = [ROOT / "data/showtimes.json", ROOT / "docs/data/showtimes.json"]
TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ).date().isoformat()
UA = "Mozilla/5.0 (compatible; taichung-movie/1.0; +https://github.com/ilovewtaps220/taichung-movie)"

CINEMAS = [
    {"id": "top-city", "name": "台中大遠百威秀影城", "display_name": "大遠百威秀", "source": "https://www.vscinemas.com.tw/theater/detail.aspx?id=11", "fallback": "https://www.atmovies.com.tw/showtime/t04407/a04/", "source_type": "vieshow-official-fallback"},
    {"id": "tiger-city", "name": "台中老虎城威秀影城", "display_name": "老虎城威秀／MUVIE CINEMAS", "source": "https://www.vscinemas.com.tw/ShowTimes/", "fallback": "https://www.atmovies.com.tw/showtime/t04402/a04/", "source_type": "vieshow-official-fallback", "note": "官方頁面現以 MUVIE CINEMAS 台中TIGER CITY 顯示"},
    {"id": "sunrise", "name": "台中日日新影城", "display_name": "日日新影城", "source": "https://srm.com.tw/product.php?_path=product_showtimes", "source_type": "official"},
    {"id": "chin-chin", "name": "台中親親影城", "display_name": "親親影城", "source": "https://www.ccmovie.com.tw/product.php?_path=product_showtimes", "source_type": "official"},
    {"id": "station-showtime", "name": "台中站前秀泰影城", "display_name": "站前秀泰", "source": "https://www.showtimes.com.tw/", "source_type": "showtimes-api"},
    {"id": "ifg", "name": "台中大魯閣威秀影城", "display_name": "大魯閣威秀（現 iFG 遠雄廣場威秀）", "source": "https://www.vscinemas.com.tw/theater/detail.aspx?id=26", "fallback": "https://www.atmovies.com.tw/showtime/t04409/a04/", "source_type": "vieshow-official-fallback", "note": "官方頁面目前顯示為台中iFG 遠雄廣場威秀影城"},
]


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json"})
    with urlopen(req, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_time(text: str) -> str:
    text = text.replace("：", ":")
    h, m = text.split(":")
    return f"{int(h):02d}:{m}"


def make_record(cinema_id: str, movie: str, times: list[str], version: str = "一般場次") -> dict | None:
    cleaned = sorted({parse_time(t) for t in times}, key=lambda x: (int(x[:2]), int(x[3:])))
    if not cleaned:
        return None
    return {"cinema_id": cinema_id, "date": TODAY, "movie": movie.strip(), "version": version.strip(), "times": cleaned}


def parse_official_php(url: str, cinema_id: str) -> list[dict]:
    soup = BeautifulSoup(fetch(url), "html.parser")
    records = []
    for box in soup.select("div.theater-box"):
        heading = box.find("h3")
        if not heading:
            continue
        movie = re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
        movie = re.sub(r"\s+片長：.*$", "", movie).strip()
        for date_tag in box.find_all(["h4", "h5"]):
            if date_tag.get_text(strip=True) != TODAY:
                continue
            text = date_tag.parent.get_text(" ", strip=True) if date_tag.parent else ""
            times = re.findall(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)", text)
            rec = make_record(cinema_id, movie, times)
            if rec:
                records.append(rec)
    return records


def parse_atmovies(url: str, cinema_id: str) -> list[dict]:
    soup = BeautifulSoup(fetch(url), "html.parser")
    records = []
    for title in soup.select("li.filmTitle"):
        movie = title.get_text(" ", strip=True)
        detail = title.find_parent("ul")
        if not detail:
            continue
        text = detail.get_text(" ", strip=True)
        times = re.findall(r"(?<!\d)(\d{1,2}：\d{2})(?!\d)", text)
        version = re.sub(r"^片長：\d+分\s*", "", detail.find_next_sibling().get_text(" ", strip=True) if detail.find_next_sibling() else "一般場次")
        rec = make_record(cinema_id, movie, times, version or "一般場次")
        if rec:
            records.append(rec)
    return records


def parse_showtimes_api(cinema_id: str) -> list[dict]:
    payload = json.loads(fetch("https://capi.showtimes.com.tw/4/app/bootstrap"))["payload"]
    corporation_id = 53  # 台中站前秀泰影城
    programs = {p["id"]: p.get("name", "未命名電影") for p in payload["programs"]}
    events = payload.get("eventsForCorporations", {}).get(str(corporation_id), {}).get("events", [])
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event in events:
        started = event.get("startedAt", "")
        if not started:
            continue
        local = datetime.fromisoformat(started.replace("Z", "+00:00")).astimezone(TZ)
        if local.date().isoformat() != TODAY or event.get("status") != "active":
            continue
        fmt = event.get("meta", {}).get("format", "一般場次")
        groups[(programs.get(event.get("programId"), "未命名電影"), fmt)].add(local.strftime("%H:%M"))
    return [make_record(cinema_id, movie, sorted(times), version) for (movie, version), times in groups.items() if make_record(cinema_id, movie, sorted(times), version)]


def collect() -> tuple[list[dict], dict[str, str]]:
    all_records: list[dict] = []
    sources: dict[str, str] = {}
    for c in CINEMAS:
        try:
            if c["id"] == "sunrise":
                records = parse_official_php(c["source"], c["id"])
            elif c["id"] == "chin-chin":
                records = parse_official_php(c["source"], c["id"])
            elif c["id"] == "station-showtime":
                records = parse_showtimes_api(c["id"])
            else:
                try:
                    fetch(c["source"])
                    records = []
                except Exception:
                    records = parse_atmovies(c["fallback"], c["id"])
                    c["source_type"] = "public-showtime-fallback"
            all_records.extend(records)
            sources[c["id"]] = c["source_type"]
            print(f"{c['name']}: {len(records)} groups ({sources[c['id']]})")
        except Exception as exc:
            sources[c["id"]] = f"error: {type(exc).__name__}"
            print(f"{c['name']}: ERROR {exc}", file=sys.stderr)
    return all_records, sources


def main() -> int:
    records, sources = collect()
    data = {
        "project": "taichung-movie",
        "city": "台中市",
        "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "data_date": TODAY,
        "notice": "只顯示當日場次；資料由公開影城頁面/API自動更新，實際座位與異動以官方頁面為準。",
        "sources": sources,
        "cinemas": CINEMAS,
        "showtimes": records,
    }
    for path in DATA_PATHS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(records)} groups for {TODAY}")
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
