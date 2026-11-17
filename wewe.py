"""WeWe RSS 对接：把公众号文章链接转成整号 RSS 订阅。"""

import json
import urllib.error
import urllib.parse
import urllib.request


class WeweError(Exception):
    pass


def _base(value):
    base = (value or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise WeweError("请填写完整的 WeWe RSS 地址，例如 http://127.0.0.1:4000")
    return base


def _headers(code):
    headers = {"Content-Type": "application/json"}
    if code:
        headers["Authorization"] = code
    return headers


def _read(req):
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        err = WeweError(_error_message(raw) or f"WeWe RSS 返回 HTTP {e.code}")
        err.status = e.code
        raise err
    except urllib.error.URLError as e:
        raise WeweError(f"连接不上 WeWe RSS：{e.reason}")
    except Exception as e:  # noqa: BLE001
        raise WeweError(f"WeWe RSS 请求失败：{e}")


def _error_message(raw):
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return ""
    entries = data if isinstance(data, list) else [data]
    for entry in entries:
        if not isinstance(entry, dict) or "error" not in entry:
            continue
        err = entry["error"]
        payload = err.get("json") if isinstance(err, dict) else None
        msg = ""
        if isinstance(payload, dict) and payload.get("message"):
            msg = payload["message"]
        elif isinstance(err, dict) and err.get("message"):
            msg = err["message"]
        if msg:
            return _zod_message(msg)
    return ""


def _zod_message(msg):
    """把 tRPC/zod 的 JSON 报错整理成一句话。"""
    text = (msg or "").strip()
    if not text.startswith("["):
        return text
    try:
        rows = json.loads(text)
    except Exception:  # noqa: BLE001
        return text
    parts = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("message"):
            continue
        path = ".".join(str(p) for p in (row.get("path") or []))
        parts.append((path + ": " if path else "") + row["message"])
    return "参数校验失败（" + "; ".join(parts) + "）" if parts else text


def _parse(raw):
    data = json.loads(raw)
    entry = data[0] if isinstance(data, list) and data else data
    if not isinstance(entry, dict):
        raise WeweError("WeWe RSS 返回格式无法识别")
    if "error" in entry:
        raise WeweError(_error_message(raw) or "WeWe RSS 返回错误")
    result = entry.get("result", entry)
    payload = result.get("data", result) if isinstance(result, dict) else result
    if isinstance(payload, dict) and "json" in payload:
        return payload["json"]
    return payload


def _post(base, code, procedure, payload):
    batch_url = f"{base}/trpc/{procedure}?batch=1"
    plain_url = f"{base}/trpc/{procedure}"
    # 不同版本的 WeWe RSS 对 tRPC 入参包装不一致，按实测顺序逐个尝试
    attempts = [
        (batch_url, {"0": payload}),
        (plain_url, payload),
        (batch_url, {"0": {"json": payload}}),
        (plain_url, {"json": payload}),
    ]
    last = None
    for url, body in attempts:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=_headers(code),
            method="POST",
        )
        try:
            return _parse(_read(req))
        except WeweError as e:
            last = e
            if getattr(e, "status", 0) not in (400, 404, 405):
                raise
    raise last or WeweError("WeWe RSS 请求失败")


def _query(base, code, procedure, payload):
    query = urllib.parse.urlencode({"batch": "1", "input": json.dumps({"0": {"json": payload}})})
    req = urllib.request.Request(f"{base}/trpc/{procedure}?{query}", headers=_headers(code))
    return _parse(_read(req))


def test_connection(base_url, auth_code):
    base = _base(base_url)
    data = _query(base, auth_code, "feed.list", {"limit": 1000})
    items = data.get("items", []) if isinstance(data, dict) else data
    return {"ok": True, "feeds": len(items or [])}


def resolve_mp(base_url, auth_code, article_url):
    """文章链接 → 公众号信息（含 feed id）。"""
    base = _base(base_url)
    if not article_url.startswith("https://mp.weixin.qq.com/s/"):
        raise WeweError("请填写公众号文章链接（https://mp.weixin.qq.com/s/…）")
    data = _post(base, auth_code, "platform.getMpInfo", {"wxsLink": article_url})
    mps = data if isinstance(data, list) else [data]
    mps = [m for m in mps if isinstance(m, dict) and m.get("id")]
    if not mps:
        raise WeweError("没解析到公众号，请确认文章链接能正常打开")
    return mps[0]


def add_feed(base_url, auth_code, mp):
    base = _base(base_url)
    payload = {
        "id": mp["id"],
        "mpName": mp.get("name") or mp["id"],
        "mpCover": mp.get("cover") or "",
        "mpIntro": mp.get("intro") or "",
        "updateTime": int(mp.get("updateTime") or 0),
    }
    try:
        return _post(base, auth_code, "feed.add", payload)
    except WeweError as e:
        msg = str(e)
        if "Unique constraint" in msg or "already" in msg.lower():
            return payload
        raise


def rss_url(base_url, feed_id):
    return f"{_base(base_url)}/feeds/{feed_id}.rss"


def refresh(base_url, feed_id):
    """尽量触发一次立即更新，失败不影响后续入库。"""
    url = f"{rss_url(base_url, feed_id)}?update=true"
    try:
        with urllib.request.urlopen(url, timeout=25) as resp:
            resp.read(2048)
    except Exception:  # noqa: BLE001
        pass
