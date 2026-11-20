"""RSS / Atom 采集：抓取、解析、正文清洗、链接规范化。"""

import gzip
import hashlib
import html
import re
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - 可选依赖
    BeautifulSoup = None

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 IntelStation/1.0"
)

TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|spm|scm|from|source|share_\w+|ref|fbclid|gclid|_hsenc|_hsmi)$", re.I
)


class FeedError(Exception):
    pass


def fetch_bytes(url, timeout=20, retries=1):
    last_err = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, "
                              "text/xml, text/html;q=0.8, */*;q=0.5",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
            except urllib.error.URLError as e:
                if not isinstance(e.reason, ssl.SSLCertVerificationError):
                    raise
                # 部分站点证书与域名不匹配，浏览器可手动放行；这里降级重试一次
                resp = urllib.request.urlopen(
                    req, timeout=timeout, context=ssl._create_unverified_context()
                )
            with resp:
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in encoding:
                    raw = gzip.decompress(raw)
                elif "deflate" in encoding:
                    import zlib
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                return raw
        except Exception as e:  # noqa: BLE001
            last_err = str(e) or type(e).__name__
            time.sleep(1.2)
    raise FeedError(f"请求失败: {last_err}")


def _local(tag):
    return tag.split("}")[-1].lower()


def _text(node, names):
    for child in node:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _atom_link(node):
    fallback = ""
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        if not href:
            continue
        rel = (child.get("rel") or "alternate").lower()
        if rel == "alternate":
            return href
        fallback = fallback or href
    return fallback


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except Exception:
        dt = None
    if dt is None:
        iso = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(value[:19], fmt)
                    break
                except Exception:
                    continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        return int(dt.timestamp())
    return int(dt.astimezone(timezone.utc).timestamp())


def html_to_text(raw):
    if not raw:
        return ""
    if BeautifulSoup is not None:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
    else:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
        text = re.sub(r"(?s)<[^>]+>", "\n", text)
        text = html.unescape(text)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _BlockText(HTMLParser):
    """抓取指定容器（id 或标签名）内的文本。"""

    def __init__(self, tag="", ident=""):
        super().__init__(convert_charrefs=True)
        self.tag = tag
        self.ident = ident
        self.depth = 0
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if not self.active:
            hit = bool(self.ident) and dict(attrs).get("id") == self.ident
            hit = hit or (bool(self.tag) and tag == self.tag)
            if hit:
                self.active = True
                self.depth = 1
            return
        if tag in VOID_TAGS:
            if tag == "br":
                self.parts.append("\n")
            return
        if tag in ("p", "div", "li", "section", "h1", "h2", "h3"):
            self.parts.append("\n")
        self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if self.active and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if not self.active or tag in VOID_TAGS:
            return
        self.depth -= 1
        if self.depth <= 0:
            self.active = False
        elif tag in ("p", "div", "li", "section", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.active and data.strip():
            self.parts.append(data.strip())


def _meta_content(raw, *names):
    """按 property/name 读取 meta 内容，兼容 content 属性在前的写法。"""
    for name in names:
        pattern = r"<meta[^>]+(?:property|name)\s*=\s*[\"']%s[\"'][^>]*>" % re.escape(name)
        for m in re.finditer(pattern, raw, re.I):
            found = re.search(r"content\s*=\s*[\"']([^\"']*)", m.group(0), re.I)
            if found:
                return html.unescape(found.group(1)).strip()
    return ""


def fetch_article(url, limit=20000):
    """抓取文章页，提取标题、作者、发布时间、描述与正文。"""
    raw = fetch_bytes(url, timeout=15, retries=1)
    charset = "utf-8"
    found = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?([\w-]+)", raw[:4096], re.I)
    if found:
        charset = found.group(1).decode("ascii", "ignore")
    try:
        page = raw.decode(charset, "ignore")
    except LookupError:
        page = raw.decode("utf-8", "ignore")
    if not page.strip():
        raise FeedError("页面内容为空")

    title = _meta_content(page, "og:title", "twitter:title")
    if not title:
        found = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
        title = html.unescape(found.group(1)).strip() if found else ""

    author = _meta_content(page, "author", "article:author", "og:article:author", "twitter:creator")
    if not author:
        found = re.search(r"var\s+nickname\s*=\s*[\"']([^\"']{1,40})", page)
        author = found.group(1).strip() if found else ""

    published = 0
    published_text = _meta_content(
        page, "article:published_time", "og:release_date", "publish_time", "pubdate"
    )
    if published_text:
        published = parse_date(published_text) or 0
    if not published:
        found = re.search(r"var\s+ct\s*=\s*[\"']?(\d{10})", page)
        if found:
            published = int(found.group(1))

    body = ""
    for tag, ident in (("", "js_content"), ("article", ""), ("", "article")):
        parser = _BlockText(tag=tag, ident=ident)
        try:
            parser.feed(page)
        except Exception:  # noqa: BLE001
            continue
        text = html_to_text("\n".join(parser.parts))
        if len(text) > len(body):
            body = text
    if len(body) < 120:
        body = html_to_text(page)

    return {
        "title": title[:200],
        "author": author[:80],
        "published_at": published,
        "description": _meta_content(page, "og:description", "description", "twitter:description")[:300],
        "content": body[:limit].strip(),
    }


def normalize_url(url):
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return url.strip()
    query = [(k, v) for k, v in _parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _parse_qsl(query):
    from urllib.parse import parse_qsl
    return parse_qsl(query, keep_blank_values=True)


def dedupe_key_for(url, title, feed_url=""):
    norm = normalize_url(url)
    if norm:
        seed = norm
    else:
        title_token = re.sub(r"\W+", "", title or "")
        seed = f"{feed_url}|{title_token}"
    return hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()


def parse_feed(raw, base_url=""):
    """解析 RSS2.0 / Atom / RDF，返回 (meta, items)。"""
    # 部分源（如 36氪）正文里混入控制字符，会让 XML 解析直接失败，先剔除
    if isinstance(raw, bytes):
        raw = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)
    else:
        raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        head = raw[:200].decode("utf-8", "ignore") if isinstance(raw, bytes) else raw[:200]
        raise FeedError(f"不是有效的 RSS/Atom 内容（{head[:80]}）")

    meta = {"title": "", "site_url": ""}
    items = []
    root_kind = _local(root.tag)

    if root_kind == "feed":  # Atom
        meta["title"] = _text(root, {"title"})
        for child in root:
            if _local(child.tag) == "link" and (child.get("rel") or "alternate") in ("alternate", ""):
                meta["site_url"] = child.get("href") or ""
                break
        for entry in root:
            if _local(entry.tag) != "entry":
                continue
            content = _text(entry, {"content", "summary"})
            link = _atom_link(entry)
            author = ""
            for child in entry:
                if _local(child.tag) == "author":
                    author = _text(child, {"name"}) or (child.text or "").strip()
                    break
            items.append({
                "title": _text(entry, {"title"}),
                "url": urljoin(base_url, link) if link else "",
                "author": author,
                "published_at": parse_date(_text(entry, {"published", "updated", "issued"})),
                "content": content,
                "raw_id": _text(entry, {"id"}),
            })
    else:  # RSS 2.0 / RDF
        channel = None
        for child in root:
            if _local(child.tag) == "channel":
                channel = child
                break
        node = channel if channel is not None else root
        meta["title"] = _text(node, {"title"})
        link = _text(node, {"link"})
        meta["site_url"] = urljoin(base_url, link) if link else ""

        item_nodes = [n for n in root.iter() if _local(n.tag) == "item"]
        for node in item_nodes:
            link = _text(node, {"link"})
            if not link:
                for child in node:
                    if _local(child.tag) == "link" and (child.text or "").strip():
                        link = child.text.strip()
                        break
            content = _text(node, {"encoded", "content", "description", "summary"})
            items.append({
                "title": _text(node, {"title"}),
                "url": urljoin(base_url, link) if link else "",
                "author": _text(node, {"creator", "author"}),
                "published_at": parse_date(_text(node, {"pubdate", "date", "published", "updated"})),
                "content": content,
                "raw_id": _text(node, {"guid", "id"}),
            })

    cleaned = []
    seen = set()
    now = int(time.time())
    for it in items:
        title = re.sub(r"\s+", " ", html.unescape(it["title"] or "")).strip()
        if not title:
            continue
        url = it["url"] or ""
        key = dedupe_key_for(url, title, base_url)
        if key in seen:
            continue
        seen.add(key)
        body = html_to_text(it["content"])
        cleaned.append({
            "title": title,
            "url": url,
            "author": (it["author"] or "").strip(),
            "published_at": it["published_at"] or now,
            "content": body[:8000],
            "dedupe_key": key,
        })

    meta["title"] = re.sub(r"\s+", " ", meta["title"] or "").strip()
    return meta, cleaned


def parse_json_feed(raw, base_url=""):
    """解析 JSON 文章列表接口（如机器之心），返回 (meta, items)。"""
    import json

    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    try:
        data = json.loads(text)
    except Exception:
        raise FeedError("不是有效的 JSON 内容")

    entries = None
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        for key in ("articles", "items", "data", "results", "list"):
            value = data.get(key)
            if isinstance(value, list):
                entries = value
                break
    if entries is None:
        raise FeedError("JSON 中未找到文章列表")

    host = urlsplit(base_url).netloc if base_url else ""
    now = int(time.time())
    items = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = re.sub(r"\s+", " ", str(entry.get("title") or "")).strip()
        if not title:
            continue
        link = entry.get("url") or entry.get("link") or entry.get("permalink") or ""
        if not link and entry.get("slug") and host:
            link = f"https://{host}/articles/{entry['slug']}"
        if link and not link.startswith("http"):
            link = urljoin(base_url, link)
        content = entry.get("content") or entry.get("summary") or entry.get("description") or ""
        published = (
            entry.get("publishedAt") or entry.get("published_at")
            or entry.get("date") or entry.get("pubDate") or ""
        )
        key = dedupe_key_for(link, title, base_url)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "title": title,
            "url": link,
            "author": str(entry.get("author") or ""),
            "published_at": parse_date(str(published)) or now,
            "content": html_to_text(str(content))[:8000],
            "dedupe_key": key,
        })
    return {"title": "", "site_url": ""}, items


def fetch_feed(url):
    raw = fetch_bytes(url)
    head = raw.lstrip()[:1]
    if head in (b"{", b"["):
        return parse_json_feed(raw, base_url=url)
    return parse_feed(raw, base_url=url)
