from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "sources.json"
DEFAULT_URL_CONFIG = ROOT / "config" / "target_urls.json"
DEFAULT_OUTPUT = ROOT / "data" / "bids.json"
DEFAULT_LOG = ROOT / "data" / "collector.log"


@dataclass
class Link:
    url: str
    text: str


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self._href_stack: list[str | None] = []
        self._text_stack: list[list[str]] = []
        self.title = ""
        self._in_title = False
        self._title_chunks: list[str] = []
        self.text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag.lower() == "a":
            self._href_stack.append(attr.get("href"))
            self._text_stack.append([])
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href_stack:
            href = self._href_stack.pop()
            chunks = self._text_stack.pop() if self._text_stack else []
            text = normalize_space(" ".join(chunks))
            if href and text:
                self.links.append(Link(href, text))
        if tag.lower() == "title":
            self._in_title = False
            self.title = normalize_space(" ".join(self._title_chunks))

    def handle_data(self, data: str) -> None:
        if self._text_stack:
            self._text_stack[-1].append(data)
        if self._in_title:
            self._title_chunks.append(data)
        cleaned = normalize_space(data)
        if cleaned:
            self.text_chunks.append(cleaned)


def now_jst() -> datetime:
    return datetime.now(JST)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalize_for_match(value: str) -> str:
    return normalize_space(value).lower()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def write_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = now_jst().strftime("%Y-%m-%d %H:%M:%S%z")
    with path.open("a", encoding="utf-8", newline="\n") as fp:
        fp.write(f"[{stamp}] {message}\n")


def fetch_html(url: str, timeout: int, user_agent: str) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en;q=0.7",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
    return decode_bytes(raw, content_type), content_type


def decode_bytes(raw: bytes, content_type: str) -> str:
    candidates: list[str] = []
    header_match = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if header_match:
        candidates.append(header_match.group(1))

    sample = raw[:4096].decode("ascii", errors="ignore")
    meta_match = re.search(r"<meta[^>]+charset=[\"']?([\w\-]+)", sample, re.I)
    if meta_match:
        candidates.append(meta_match.group(1))

    candidates.extend(["utf-8", "cp932", "shift_jis", "euc_jp", "iso2022_jp"])
    seen: set[str] = set()
    for encoding in candidates:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_links(source_url: str, document: str) -> tuple[list[Link], str, str]:
    parser = LinkCollector()
    parser.feed(document)
    base_url = source_url
    links: list[Link] = []
    for link in parser.links:
        absolute = urllib.parse.urljoin(base_url, link.url)
        if absolute.startswith(("http://", "https://")):
            links.append(Link(absolute, link.text))
    page_text = normalize_space(" ".join(parser.text_chunks))
    return links, parser.title, page_text


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    normalized = normalize_for_match(text)
    return any(normalize_for_match(keyword) in normalized for keyword in keywords if keyword)


def should_consider_link(text: str, url: str, include_keywords: list[str], noise_keywords: list[str]) -> bool:
    haystack = f"{text} {url}"
    if contains_any(text, noise_keywords):
        return False
    if contains_any(haystack, include_keywords):
        return True
    return urllib.parse.urlparse(url).path.lower().endswith((".pdf", ".xlsx", ".xls", ".csv"))


def extension(url: str) -> str:
    return Path(urllib.parse.urlparse(url).path.lower()).suffix


def allowed_host(url: str, source: dict) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    source_host = urllib.parse.urlparse(source["url"]).netloc.lower()
    allowed_hosts = {source_host, *[item.lower() for item in source.get("allowed_hosts", [])]}
    return host in allowed_hosts


def is_index_like(text: str, url: str) -> bool:
    title = normalize_space(text)
    if extension(url) in {".pdf", ".xlsx", ".xls", ".csv"}:
        return False
    compact = title.replace(" ", "")
    exact_titles = {
        "入札情報",
        "公共工事",
        "お知らせ",
        "資格取得",
        "入札公告の検索",
        "調達情報検索",
        "調達情報",
        "調達情報一覧",
        "入札公告等調達情報",
        "人事院調達情報",
        "警察庁調達情報",
        "SearchofProcurementInformation",
    }
    if compact in exact_titles:
        return True
    index_words = ["一覧", "検索", "システム", "入札情報", "発注情報", "電子入札", "入札・契約", "調達情報"]
    return len(title) <= 24 and any(word in title for word in index_words)


def is_case_candidate(text: str, url: str, include_keywords: list[str]) -> bool:
    title = normalize_space(text)
    if extension(url) in {".pdf", ".xlsx", ".xls", ".csv"} and contains_any(title, include_keywords):
        return True
    if parse_announcement_date(title):
        return contains_any(title, include_keywords)
    case_words = ["公告", "公募", "プロポーザル", "見積", "委託", "役務", "購入", "保守", "賃貸借", "リース"]
    return len(title) >= 14 and contains_any(title, case_words)


def is_civil_engineering(text: str, exclude_keywords: list[str]) -> bool:
    return contains_any(text, exclude_keywords)


def infer_case_type(text: str, hint: str = "") -> str:
    haystack = normalize_for_match(text)
    rules = [
        ("設計・測量・コンサル", ["設計", "測量", "コンサル", "地質調査"]),
        ("公募・プロポーザル", ["公募", "プロポーザル", "企画提案"]),
        ("物品", ["物品", "購入", "買入", "備品", "用品", "部品", "機器", "端末", "車両", "印刷", "賃貸借", "リース", "借入", "納入"]),
        ("委託", ["委託", "業務", "調査", "検討", "支援", "制作", "データ整備"]),
        ("役務", ["役務", "請負", "作業", "処理", "保守", "清掃", "警備", "点検", "整備", "修理", "運用", "管理", "研修", "講座", "契約"]),
        ("工事", ["工事", "改修", "修繕"]),
    ]
    for case_type, keywords in rules:
        if any(keyword in haystack for keyword in keywords):
            return case_type
    return hint or "その他"


def parse_announcement_date(text: str) -> str:
    for pattern in [
        r"(20\d{2})[./\-年]\s*(\d{1,2})[./\-月]\s*(\d{1,2})日?",
        r"(令和|R)\s*([0-9元]+)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
    ]:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        try:
            if match.group(1) in {"令和", "R", "r"}:
                era_year = 1 if match.group(2) == "元" else int(match.group(2))
                year = 2018 + era_year
                month = int(match.group(3))
                day = int(match.group(4))
            else:
                year = int(match.group(1))
                month = int(match.group(2))
                day = int(match.group(3))
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def build_record_title(link_text: str, page_title: str, depth: int) -> str:
    title = normalize_space(link_text)
    generic_link_text = {"入札公告", "入札公告（PDF）", "公告", "詳細", "PDF", "仕様書", "入札説明書"}
    if depth > 0 and title in generic_link_text and page_title and not is_index_like(page_title, ""):
        return page_title
    return title


def collect_generic_html_source(source: dict, config: dict, retrieved_at: str) -> tuple[list[dict], list[str]]:
    collector = config["collector"]
    include_keywords = collector["include_keywords"]
    noise_keywords = collector.get("noise_keywords", [])
    exclude_keywords = collector["civil_engineering_exclude_keywords"]
    timeout = int(collector.get("request_timeout_seconds", 30))
    user_agent = collector.get("user_agent", "PublicBidResearchBot/0.1")
    max_links = int(collector.get("max_links_per_source", 300))
    max_pages = int(collector.get("max_pages_per_source", 12))
    crawl_depth = int(collector.get("crawl_depth", 1))

    messages: list[str] = []
    source_url = source["url"]
    records: list[dict] = []
    seen_urls: set[str] = set()
    visited_pages: set[str] = set()
    queue: list[tuple[str, int]] = [(source_url, 0)]
    total_links = 0
    content_types: list[str] = []

    while queue and len(visited_pages) < max_pages and len(records) < max_links:
        page_url, depth = queue.pop(0)
        if page_url in visited_pages:
            continue
        visited_pages.add(page_url)
        try:
            document, content_type = fetch_html(page_url, timeout, user_agent)
        except urllib.error.HTTPError as exc:
            messages.append(f"{source['id']}: HTTP {exc.code} {page_url}")
            continue
        except urllib.error.URLError as exc:
            messages.append(f"{source['id']}: URL error {exc.reason} {page_url}")
            continue
        except TimeoutError:
            messages.append(f"{source['id']}: timeout {page_url}")
            continue

        links, page_title, _page_text = parse_links(page_url, document)
        total_links += len(links)
        if content_type:
            content_types.append(content_type)

        for link in links:
            if len(records) >= max_links:
                break
            if not allowed_host(link.url, source):
                continue
            title = normalize_space(link.text)
            if not title:
                continue
            if not should_consider_link(title, link.url, include_keywords, noise_keywords):
                continue
            combined = f"{title} {link.url}"
            if is_civil_engineering(combined, exclude_keywords):
                continue
            if is_index_like(title, link.url):
                if depth < crawl_depth and link.url not in visited_pages and link.url not in [item[0] for item in queue]:
                    queue.append((link.url, depth + 1))
                continue
            if not is_case_candidate(title, link.url, include_keywords):
                continue
            if link.url in seen_urls:
                continue
            seen_urls.add(link.url)

            record_title = build_record_title(title, page_title, depth)
            records.append(
                {
                    "retrieved_at": retrieved_at,
                    "prefecture": source.get("prefecture", ""),
                    "agency": source.get("agency", ""),
                    "case_type": infer_case_type(record_title, source.get("case_type_hint", "")),
                    "title": record_title,
                    "announcement_date": parse_announcement_date(record_title),
                    "url": link.url,
                    "source": source.get("source_name", source.get("id", "")),
                    "source_id": source.get("id", ""),
                    "source_url": source_url,
                }
            )

    if not visited_pages:
        messages.append(f"{source['id']}: no pages fetched")
    else:
        content_type_summary = ", ".join(sorted(set(content_types))) or "unknown content-type"
        messages.append(
            f"{source['id']}: fetched {len(visited_pages)} pages / {total_links} links, kept {len(records)} records ({content_type_summary})"
        )
    return records, messages


def collect_specified_url_source(source: dict, config: dict, retrieved_at: str) -> tuple[list[dict], list[str]]:
    collector = config["collector"]
    include_keywords = collector["include_keywords"]
    exclude_keywords = collector["civil_engineering_exclude_keywords"]
    non_case_keywords = collector.get("non_case_keywords", [])
    timeout = int(collector.get("request_timeout_seconds", 30))
    user_agent = collector.get("user_agent", "PublicBidResearchBot/0.1")
    source_url = source["url"]
    records: list[dict] = []
    messages: list[str] = []

    try:
        document, content_type = fetch_html(source_url, timeout, user_agent)
    except urllib.error.HTTPError as exc:
        return [], [f"{source['id']}: HTTP {exc.code} {source_url}"]
    except urllib.error.URLError as exc:
        return [], [f"{source['id']}: URL error {exc.reason} {source_url}"]
    except TimeoutError:
        return [], [f"{source['id']}: timeout {source_url}"]

    _links, page_title, page_text = parse_links(source_url, document)
    title = normalize_space(source.get("title") or page_title or source_url)
    haystack = f"{title} {page_text[:3000]} {source_url}"
    add_page_as_record = source.get("add_page_as_record", True)
    if (
        add_page_as_record
        and not is_index_like(title, source_url)
        and contains_any(haystack, include_keywords)
        and not contains_any(haystack, exclude_keywords)
        and not contains_any(haystack, non_case_keywords)
    ):
        records.append(
            {
                "retrieved_at": retrieved_at,
                "prefecture": source.get("prefecture", ""),
                "agency": source.get("agency", ""),
                "case_type": infer_case_type(title, source.get("case_type_hint", "")),
                "title": title,
                "announcement_date": parse_announcement_date(haystack),
                "url": source_url,
                "source": source.get("source_name", "指定URL"),
                "source_id": source.get("id", ""),
                "source_url": source_url,
            }
        )

    linked_records, linked_messages = collect_generic_html_source(source, config, retrieved_at)
    records.extend(linked_records)
    messages.extend(linked_messages)
    messages.append(f"{source['id']}: specified URL page kept {len(records)} records ({content_type or 'unknown content-type'})")
    return deduplicate_records(records), messages


def text_of(node: ET.Element, name: str) -> str:
    child = node.find(name)
    return normalize_space(child.text if child is not None and child.text else "")


def api_date_to_date(value: str) -> str:
    value = normalize_space(value)
    if not value:
        return ""
    return value[:10]


def api_date_to_datetime(value: str, fallback: str) -> str:
    value = normalize_space(value)
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value[:19].replace("T", " ") or fallback


def parse_japanese_date(value: str) -> str:
    value = normalize_space(value)
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", value)
    if not match:
        return parse_announcement_date(value)
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def first_attachment_url(node: ET.Element) -> str:
    uri = node.findtext("Attachments/Attachment/Uri")
    return normalize_space(uri or "")


def map_api_case_type(category: str, procedure_type: str, title: str) -> str:
    if category in {"物品", "工事", "役務"}:
        return category
    return infer_case_type(title)


def kkj_request_url(source: dict, params: dict[str, str]) -> str:
    endpoint = source.get("api_url", "https://www.kkj.go.jp/api/")
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def prefecture_code_from_kkj_result_url(url: str) -> str:
    encoded = urllib.parse.urlparse(url).query
    if not encoded:
        return ""
    try:
        decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    params = urllib.parse.parse_qs(decoded)
    return normalize_space((params.get("pr") or [""])[0])


def collect_kkj_result_url_source(source: dict, config: dict, retrieved_at: str) -> tuple[list[dict], list[str]]:
    lg_code = source.get("lg_code") or prefecture_code_from_kkj_result_url(source.get("url", ""))
    if not lg_code:
        return [], [f"{source.get('id', 'kkj-result-url')}: prefecture code not found in {source.get('url', '')}"]
    api_source = dict(source)
    api_source["mode"] = "kkj_api"
    api_source["api_url"] = source.get("api_url", "https://www.kkj.go.jp/api/")
    api_source["lg_code"] = lg_code
    return collect_kkj_api_source(api_source, config, retrieved_at)


def collect_kkj_api_source(source: dict, config: dict, retrieved_at: str) -> tuple[list[dict], list[str]]:
    collector = config["collector"]
    timeout = int(collector.get("request_timeout_seconds", 30))
    user_agent = collector.get("user_agent", "PublicBidResearchBot/0.1")
    query = source.get("query") or collector.get("api_query") or "入札"
    count = str(source.get("count") or collector.get("api_count", 1000))
    issue_days = int(source.get("issue_date_days_back") or collector.get("api_issue_date_days_back", 30))
    issue_since = (now_jst().date() - timedelta(days=issue_days)).strftime("%Y-%m-%d") + "/"
    pause = float(collector.get("api_pause_seconds", 0.2))
    organization_names = source.get("organization_names") or [source.get("organization_name", "")]
    exclude_categories = set(collector.get("exclude_categories", []))
    exclude_keywords = collector["civil_engineering_exclude_keywords"]
    non_case_keywords = collector.get("non_case_keywords", [])

    records: list[dict] = []
    messages: list[str] = []
    request_count = 0

    for organization_name in organization_names:
        params = {
            "Count": count,
            "CFT_Issue_Date": issue_since,
        }
        if query:
            params["Query"] = query
        if source.get("lg_code"):
            params["LG_Code"] = str(source["lg_code"])
        if organization_name:
            params["Organization_Name"] = organization_name

        request_url = kkj_request_url(source, params)
        request_count += 1
        try:
            document, _content_type = fetch_html(request_url, timeout, user_agent)
            root = ET.fromstring(document)
        except urllib.error.HTTPError as exc:
            messages.append(f"{source['id']}: HTTP {exc.code} {request_url}")
            continue
        except urllib.error.URLError as exc:
            messages.append(f"{source['id']}: URL error {exc.reason} {request_url}")
            continue
        except TimeoutError:
            messages.append(f"{source['id']}: timeout {request_url}")
            continue
        except ET.ParseError as exc:
            messages.append(f"{source['id']}: XML parse error {exc} {request_url}")
            continue

        error = root.findtext("Error")
        if error:
            messages.append(f"{source['id']}: API error {normalize_space(error)}")
            continue

        for node in root.findall(".//SearchResult"):
            title = text_of(node, "ProjectName")
            category = text_of(node, "Category")
            procedure_type = text_of(node, "ProcedureType")
            description = text_of(node, "ProjectDescription")
            url = text_of(node, "ExternalDocumentURI") or first_attachment_url(node)
            agency = text_of(node, "OrganizationName") or source.get("agency", "")
            prefecture = source.get("prefecture", "") if source.get("prefecture") == "全国" else text_of(node, "PrefectureName")
            if not prefecture:
                prefecture = source.get("prefecture", "")
            haystack = " ".join([title, category, procedure_type, description, agency, prefecture])
            if contains_any(haystack, exclude_keywords) or contains_any(haystack, non_case_keywords):
                continue
            if not title or not url:
                continue
            case_type = map_api_case_type(category, procedure_type, title)
            if category in exclude_categories or case_type in exclude_categories:
                continue
            records.append(
                {
                    "retrieved_at": api_date_to_datetime(text_of(node, "Date"), retrieved_at),
                    "prefecture": prefecture,
                    "agency": agency,
                    "case_type": case_type,
                    "title": title,
                    "announcement_date": api_date_to_date(text_of(node, "CftIssueDate")),
                    "url": url,
                    "source": source.get("source_name", "官公需情報ポータルAPI"),
                    "source_id": source.get("id", ""),
                    "source_url": source.get("api_url", "https://www.kkj.go.jp/api/"),
                }
            )
        if pause:
            time.sleep(pause)

    messages.append(f"{source['id']}: requested {request_count} API calls, kept {len(records)} records")
    return records, messages


def jetro_local_list_url(base_url: str, params: dict[str, str]) -> str:
    return f"{base_url.rstrip('/')}/gov_procurement/local/list.html?{urllib.parse.urlencode(params)}"


def jetro_local_api_url(base_url: str, block_id: str, current: int, params: dict[str, str]) -> str:
    query = {"blockId": block_id}
    if current:
        query["current"] = str(current)
    query.update(params)
    return f"{base_url.rstrip('/')}/view_interface.php?{urllib.parse.urlencode(query)}"


def fetch_jetro_json(url: str, referer: str, timeout: int, user_agent: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ja,en;q=0.7",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
    return json.loads(decode_bytes(raw, content_type))


def collect_jetro_local_source(source: dict, config: dict, retrieved_at: str) -> tuple[list[dict], list[str]]:
    collector = config["collector"]
    timeout = int(collector.get("request_timeout_seconds", 30))
    user_agent = collector.get("user_agent", "PublicBidResearchBot/0.1")
    issue_days = int(source.get("issue_date_days_back") or collector.get("api_issue_date_days_back", 30))
    date_from = (now_jst().date() - timedelta(days=issue_days)).strftime("%Y/%m/%d")
    date_to = now_jst().date().strftime("%Y/%m/%d")
    base_url = source.get("base_url", "https://www.jetro.go.jp")
    block_id = str(source.get("results_block_id", "33686978"))
    pause = float(source.get("pause_seconds", collector.get("api_pause_seconds", 0.2)))
    max_pages = int(source.get("max_pages_per_entity", 20))
    exclude_categories = set(collector.get("exclude_categories", []))
    exclude_keywords = collector["civil_engineering_exclude_keywords"]
    non_case_keywords = collector.get("non_case_keywords", [])

    records: list[dict] = []
    messages: list[str] = []
    entities = source.get("entities", [])
    for entity in entities:
        entity_code = normalize_space(str(entity.get("code", "")))
        if not entity_code:
            continue
        area_code = normalize_space(str(entity.get("area_code") or entity_code[:2]))
        prefecture = normalize_space(entity.get("prefecture", ""))
        params = {
            "local_from": date_from,
            "local_to": date_to,
            "local_area": area_code,
            "local_entity": entity_code,
            "local_keyword": normalize_space(source.get("keyword", "")),
            "local_classification1": normalize_space(source.get("classification1", "")),
            "local_classification2": normalize_space(source.get("classification2", "")),
            "local_classification3": normalize_space(source.get("classification3", "")),
            "local_deadline": normalize_space(source.get("deadline", "")),
        }
        referer = jetro_local_list_url(base_url, params)
        kept = 0
        total = 0
        current = 0
        page_count = 0
        while page_count < max_pages:
            api_url = jetro_local_api_url(base_url, block_id, current, params)
            try:
                data = fetch_jetro_json(api_url, referer, timeout, user_agent)
            except urllib.error.HTTPError as exc:
                messages.append(f"{source['id']}: HTTP {exc.code} {entity_code} {api_url}")
                break
            except urllib.error.URLError as exc:
                messages.append(f"{source['id']}: URL error {exc.reason} {entity_code} {api_url}")
                break
            except TimeoutError:
                messages.append(f"{source['id']}: timeout {entity_code} {api_url}")
                break
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                messages.append(f"{source['id']}: JSON parse error {exc} {entity_code} {api_url}")
                break

            pagination = data.get("pagination", {})
            total = int(pagination.get("total") or 0)
            per_page = int(pagination.get("perPage") or 30)
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                title = normalize_space(item.get("title", ""))
                agency = normalize_space(item.get("agency", entity.get("name", "")))
                location = normalize_space(item.get("location", prefecture))
                haystack = " ".join([title, agency, location])
                case_type = infer_case_type(haystack, source.get("case_type_hint", ""))
                if not title or contains_any(haystack, exclude_keywords) or contains_any(haystack, non_case_keywords):
                    continue
                if case_type in exclude_categories:
                    continue
                aid = normalize_space(item.get("aid", ""))
                if not aid:
                    continue
                records.append(
                    {
                        "retrieved_at": retrieved_at,
                        "prefecture": prefecture or location,
                        "agency": agency,
                        "case_type": case_type,
                        "title": title,
                        "announcement_date": parse_japanese_date(item.get("date", "")),
                        "url": f"{base_url.rstrip('/')}/gov_procurement/local/articles/{aid}.html",
                        "source": source.get("source_name", "JETRO政府公共調達DB"),
                        "source_id": source.get("id", ""),
                        "source_url": referer,
                    }
                )
                kept += 1
            page_count += 1
            current += per_page
            if current >= total:
                break
            if pause:
                time.sleep(pause)
        messages.append(f"{source['id']}: {entity_code} {entity.get('name', '')} total={total} kept={kept}")
        if pause:
            time.sleep(pause)

    return deduplicate_records(records), messages


def collect_source(source: dict, config: dict, retrieved_at: str) -> tuple[list[dict], list[str]]:
    if source.get("mode") == "kkj_api":
        return collect_kkj_api_source(source, config, retrieved_at)
    if source.get("mode") == "kkj_result_url":
        return collect_kkj_result_url_source(source, config, retrieved_at)
    if source.get("mode") == "jetro_local":
        return collect_jetro_local_source(source, config, retrieved_at)
    if source.get("mode") == "specified_url":
        return collect_specified_url_source(source, config, retrieved_at)
    return collect_generic_html_source(source, config, retrieved_at)


def sort_records(records: list[dict]) -> list[dict]:
    return sorted(
        records,
        key=lambda item: (
            item.get("announcement_date") or "0000-00-00",
            item.get("retrieved_at") or "",
            item.get("prefecture") or "",
            item.get("title") or "",
        ),
        reverse=True,
    )


def record_key(record: dict) -> str:
    url = record.get("url") or ""
    title = record.get("title") or ""
    agency = record.get("agency") or ""
    return f"{url}\n{agency}\n{title}"


def deduplicate_records(records: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for record in records:
        key = record_key(record)
        if key not in deduped:
            deduped[key] = record
    return list(deduped.values())


def load_target_url_sources(path: Path) -> list[dict]:
    if not path or not path.exists():
        return []
    data = load_json(path)
    raw_sources = data.get("sources", data if isinstance(data, list) else [])
    sources: list[dict] = []
    for index, source in enumerate(raw_sources, start=1):
        if not source.get("enabled", True):
            continue
        url = normalize_space(source.get("url", ""))
        if not url:
            continue
        item = dict(source)
        item.setdefault("id", f"specified-url-{index}")
        item.setdefault("mode", "specified_url")
        item.setdefault("source_name", "指定URL")
        item.setdefault("agency", "")
        item.setdefault("prefecture", "")
        parsed = urllib.parse.urlparse(url)
        item.setdefault("allowed_hosts", [parsed.netloc] if parsed.netloc else [])
        sources.append(item)
    return sources


def parse_jst_datetime(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=JST)
            return parsed
        except ValueError:
            continue
    return None


def merge_with_existing(existing_path: Path, fresh: list[dict], generated_at: datetime, days_to_keep: int) -> list[dict]:
    cutoff = generated_at - timedelta(days=days_to_keep)
    merged: dict[str, dict] = {}

    if existing_path.exists():
        try:
            existing_data = load_json(existing_path)
            existing_items = existing_data.get("items", existing_data if isinstance(existing_data, list) else [])
        except (OSError, json.JSONDecodeError):
            existing_items = []
        for record in existing_items:
            seen_at = parse_jst_datetime(record.get("retrieved_at", ""))
            if seen_at and seen_at < cutoff:
                continue
            merged[record_key(record)] = record

    for record in fresh:
        merged[record_key(record)] = record

    return sort_records(list(merged.values()))


def run(config_path: Path, url_config_path: Path, output_path: Path, csv_path: Path | None, log_path: Path) -> int:
    config = load_json(config_path)
    generated_at = now_jst()
    retrieved_at = generated_at.strftime("%Y-%m-%d %H:%M:%S")
    fresh_records: list[dict] = []
    messages: list[str] = []

    sources = [source for source in config.get("sources", []) if source.get("enabled", True)]
    sources.extend(load_target_url_sources(url_config_path))
    for index, source in enumerate(sources, start=1):
        records, source_messages = collect_source(source, config, retrieved_at)
        fresh_records.extend(records)
        messages.extend(source_messages)
        if index < len(sources):
            time.sleep(0.8)

    records = sort_records(deduplicate_records(fresh_records))
    output = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "count": len(records),
        "items": records,
        "source_count": len(sources),
    }
    save_json(output_path, output)

    for message in messages:
        write_log(log_path, message)
    write_log(log_path, f"finished: fresh={len(fresh_records)} displayed={len(records)} output={output_path}")
    print(f"取得完了: 新規候補 {len(fresh_records)} 件 / 表示対象 {len(records)} 件")
    print(f"JSON: {output_path}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="公共事業・調達案件の一覧データを取得します。")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--url-config", type=Path, default=DEFAULT_URL_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args(argv)
    try:
        return run(args.config, args.url_config, args.output, args.csv, args.log)
    except KeyboardInterrupt:
        print("中断しました。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
