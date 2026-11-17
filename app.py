"""个人情报站 · 本地 Web 服务（零第三方依赖，python app.py 即可启动）。"""

import base64
import json
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import ai
import config
import db
import feeds
import pipeline

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
UPLOAD_DIR = os.path.join(config.DATA_DIR, "uploads")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
}


def _enrich_manual_item(item_id, url):
    """后台提取投递原文信息，并放入加工队列。"""
    try:
        info = feeds.fetch_article(url)
    except Exception as e:  # noqa: BLE001
        db.add_event(f"投递原文提取失败：{str(e)[:120]}", level="warn")
        return
    db.enrich_intake(item_id, info)
    db.add_event(f"已提取投递原文：{(info.get('title') or url)[:40]}")


def _to_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _feed_filter(value):
    """来源筛选：支持数字 ID 与 manual（人工投递）。"""
    value = str(value or "").strip()
    if value == "manual":
        return "manual"
    return _to_int(value) or None


def _tier(value, default="media"):
    value = (value or "").strip()
    return value if value in db.TIERS else default


RATING_DELTA = {"low": -25, "mid": 0, "high": 12}
RATING_LABELS = {"low": "低", "mid": "中", "high": "高"}


class Api:
    """所有接口实现，返回 (payload, status)。"""

    def overview(self, q, body):
        return db.stats_overview(), 200

    def knowledge(self, q, body):
        return db.stats_knowledge(), 200

    def knowledge_summary(self, q, body):
        months = _to_int((q.get("months") or ["3"])[0], 3)
        return pipeline.knowledge_summary(months, config.load_config()), 200

    def intake(self, q, body):
        limit = min(100, _to_int((q.get("limit") or [20])[0], 20))
        return db.list_intake(limit), 200

    def intake_add(self, q, body):
        url = (body.get("url") or "").strip()
        if not url:
            return {"error": "请填写原文链接"}, 400
        if not url.startswith(("http://", "https://")):
            return {"error": "原文链接需以 http(s) 开头"}, 400
        title = (body.get("title") or "").strip() or url
        rating = (body.get("rating") or "").strip()
        if rating not in ("", "low", "mid", "high"):
            return {"error": "无效的人工评判"}, 400
        try:
            attachment = self._save_image(body.get("image"))
        except ValueError as e:
            return {"error": str(e)}, 400
        channel = (body.get("channel") or "人工投递").strip() or "人工投递"
        if body.get("importance") is None:
            importance = max(0, min(100, 50 + RATING_DELTA.get(rating, 0)))
        else:
            importance = max(0, min(100, _to_int(body.get("importance"), 50)))
        reason = (body.get("note") or "").strip()
        if not reason:
            reason = f"人工评判 {RATING_LABELS[rating]}" if rating else "人工投递"
        result = db.add_intake({
            "title": title,
            "url": url,
            "author": (body.get("author") or "").strip(),
            "channel": channel,
            "summary": (body.get("summary") or "").strip(),
            "content": (body.get("content") or "").strip(),
            "tags": self._split_list(body.get("tags")),
            "points": self._split_list(body.get("points")),
            "importance": importance,
            "rating": rating,
            "topic": (body.get("topic") or "").strip(),
            "reason": reason,
            "read": bool(body.get("read")),
            "attachment": attachment,
            "external_id": (body.get("external_id") or "").strip(),
            "published_at": _to_int(body.get("published_at"), 0) or None,
        })
        if not result["duplicate"] and not (body.get("external_id") or "").strip():
            db.add_event(f"人工投递 {title[:40]}（{channel}）")
        if result.get("id") and (not result["duplicate"] or db.intake_needs_enrich(result["id"])):
            threading.Thread(
                target=_enrich_manual_item, args=(result["id"], url), daemon=True
            ).start()
        return {"ok": True, **result}, 200

    @staticmethod
    def _split_list(value):
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        text = (value or "").replace("，", ",").replace("；", "\n").replace(";", "\n")
        result = []
        for chunk in text.splitlines():
            result.extend(x.strip() for x in chunk.split(","))
        return [x for x in result if x]

    @staticmethod
    def _save_image(data_url):
        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            return ""
        header, _, payload = data_url.partition(",")
        ext = "png"
        for candidate in ("jpeg", "jpg", "webp", "gif", "png"):
            if candidate in header:
                ext = "jpg" if candidate == "jpeg" else candidate
                break
        try:
            raw = base64.b64decode(payload, validate=True)
        except Exception:
            raise ValueError("截图数据无法解析")
        if len(raw) > 6 * 1024 * 1024:
            raise ValueError("截图需小于 6MB")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        name = f"intake_{int(time.time() * 1000)}_{os.urandom(3).hex()}.{ext}"
        with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
            f.write(raw)
        return "/uploads/" + name

    def items(self, q, body):
        data = db.query_items(
            status=(q.get("status") or ["all"])[0],
            feed_id=_feed_filter((q.get("feed_id") or [""])[0]),
            topic=(q.get("topic") or [""])[0] or None,
            tag=(q.get("tag") or [""])[0] or None,
            q=(q.get("q") or [""])[0] or None,
            min_importance=_to_int((q.get("min") or [0])[0]),
            days=_to_int((q.get("days") or [0])[0]) or None,
            sort=(q.get("sort") or ["time"])[0],
            limit=min(100, _to_int((q.get("limit") or [20])[0], 20)),
            offset=_to_int((q.get("offset") or [0])[0]),
        )
        return data, 200

    def item_detail(self, q, body, item_id):
        item = db.get_item(item_id)
        if not item:
            return {"error": "情报不存在"}, 404
        if item.get("origin") == "manual":
            item["source"] = item.get("channel") or "人工投递"
            item["source_tier"] = "media"
            return item, 200
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT title, url, tier FROM feeds WHERE id = ?", (item["feed_id"],)
            ).fetchone()
        item["source"] = row["title"] if row else ""
        item["source_tier"] = (row["tier"] if row else "") or "media"
        return item, 200

    def item_update(self, q, body, item_id):
        simple = {}
        for key in ("read", "starred"):
            if key in body:
                simple[key] = 1 if body[key] else 0
        rating = body.get("rating")
        if rating is not None:
            rating = (rating or "").strip()
            if rating not in ("", "low", "mid", "high"):
                return {"error": "无效的人工评判"}, 400
        if not simple and rating is None:
            return {"error": "没有可更新字段"}, 400
        importance = None
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT importance, rating FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if not row:
                return {"error": "情报不存在"}, 404
            if simple:
                fields = ", ".join(f"{k} = ?" for k in simple)
                conn.execute(f"UPDATE items SET {fields} WHERE id = ?",
                             list(simple.values()) + [item_id])
            if rating is not None:
                old_delta = RATING_DELTA.get(row["rating"] or "", 0)
                importance = max(0, min(100, row["importance"] - old_delta + RATING_DELTA.get(rating, 0)))
                conn.execute("UPDATE items SET rating = ?, importance = ? WHERE id = ?",
                             (rating, importance, item_id))
        result = {"ok": True}
        if importance is not None:
            result.update({"rating": rating, "importance": importance})
        return result, 200

    def item_delete(self, q, body, item_id):
        with db.get_db() as conn:
            row = conn.execute("SELECT title, attachment FROM items WHERE id = ?", (item_id,)).fetchone()
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        if row:
            if row["attachment"].startswith("/uploads/"):
                try:
                    os.remove(os.path.join(UPLOAD_DIR, os.path.basename(row["attachment"])))
                except OSError:
                    pass
            db.add_event(f"丢弃情报 {row['title'][:40]}")
        return {"ok": True}, 200

    def items_discard(self, q, body):
        ids = body.get("ids") or []
        if ids:
            ids = [_to_int(i) for i in ids]
            ids = [i for i in ids if i > 0]
            if not ids:
                return {"error": "请选择要丢弃的情报"}, 400
            rows = db.discard_items(ids=ids)
        elif body.get("all"):
            exclude = [_to_int(i) for i in (body.get("exclude") or [])]
            rows = db.discard_items(
                status=(body.get("status") or "all"),
                feed_id=_feed_filter(body.get("feed_id")),
                topic=(body.get("topic") or "") or None,
                tag=(body.get("tag") or "") or None,
                q=(body.get("q") or "") or None,
                min_importance=_to_int(body.get("min_importance")),
                days=_to_int(body.get("days")) or None,
                exclude=[i for i in exclude if i > 0] or None,
            )
        else:
            return {"error": "请选择要丢弃的情报"}, 400
        for row in rows:
            att = row.get("attachment") or ""
            if att.startswith("/uploads/"):
                try:
                    os.remove(os.path.join(UPLOAD_DIR, os.path.basename(att)))
                except OSError:
                    pass
        if rows:
            db.add_event(f"批量丢弃 {len(rows)} 条情报")
        return {"ok": True, "deleted": len(rows)}, 200

    def feeds(self, q, body):
        with db.get_db() as conn:
            rows = conn.execute(
                """SELECT f.*, COUNT(i.id) AS item_count,
                          SUM(CASE WHEN i.read = 0 THEN 1 ELSE 0 END) AS unread
                     FROM feeds f LEFT JOIN items i ON i.feed_id = f.id
                    GROUP BY f.id ORDER BY f.enabled DESC, f.id"""
            ).fetchall()
        return [dict(r) for r in rows], 200

    def feed_add(self, q, body):
        url = (body.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"error": "请填写完整的 http(s) 订阅地址"}, 400
        category = (body.get("category") or "未分类").strip()
        tier = _tier(body.get("tier"))
        try:
            meta, items = feeds.fetch_feed(url)
        except Exception as e:  # noqa: BLE001
            return {"error": f"订阅校验失败：{e}"}, 400
        title = (body.get("title") or "").strip() or meta.get("title") or url
        with db.get_db() as conn:
            exists = conn.execute("SELECT id FROM feeds WHERE url = ?", (url,)).fetchone()
            if exists:
                return {"error": "该订阅已存在"}, 400
            cur = conn.execute(
                "INSERT INTO feeds (url, title, site_url, category, tier, created_at) VALUES (?,?,?,?,?,?)",
                (url, title, meta.get("site_url", ""), category, tier, int(time.time())),
            )
            feed_id = cur.lastrowid
        db.add_event(f"新增信息源 {title}（{url}）")
        return {"ok": True, "id": feed_id, "title": title, "sample": len(items)}, 200

    def feed_update(self, q, body, feed_id):
        fields, params = [], []
        for key in ("title", "category", "enabled", "tier"):
            if key in body:
                fields.append(f"{key} = ?")
                if key == "enabled":
                    params.append(1 if body[key] else 0)
                elif key == "tier":
                    params.append(_tier(body[key]))
                else:
                    params.append(body[key])
        if not fields:
            return {"error": "没有可更新字段"}, 400
        params.append(feed_id)
        with db.get_db() as conn:
            conn.execute(f"UPDATE feeds SET {', '.join(fields)} WHERE id = ?", params)
        return {"ok": True}, 200

    def feed_delete(self, q, body, feed_id):
        with db.get_db() as conn:
            row = conn.execute("SELECT title FROM feeds WHERE id = ?", (feed_id,)).fetchone()
            conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
            conn.execute("DELETE FROM items WHERE feed_id = ?", (feed_id,))
        if row:
            db.add_event(f"删除信息源 {row['title']}")
        return {"ok": True}, 200

    def topics(self, q, body):
        return pipeline.get_topics(), 200

    def topic_add(self, q, body):
        name = (body.get("name") or "").strip()
        if not name:
            return {"error": "请填写主题名称"}, 400
        keywords = body.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.replace("，", ",").split(",") if k.strip()]
        weight = max(1, min(30, _to_int(body.get("weight"), 10)))
        with db.get_db() as conn:
            exists = conn.execute("SELECT id FROM topics WHERE name = ?", (name,)).fetchone()
            if exists:
                return {"error": "主题已存在"}, 400
            conn.execute(
                "INSERT INTO topics (name, keywords, weight, created_at) VALUES (?,?,?,?)",
                (name, json.dumps(keywords, ensure_ascii=False), weight, int(time.time())),
            )
        db.add_event(f"新增关注主题 {name}")
        return {"ok": True}, 200

    def topic_update(self, q, body, topic_id):
        fields, params = [], []
        if "name" in body and body["name"].strip():
            fields.append("name = ?")
            params.append(body["name"].strip())
        if "keywords" in body:
            kws = body["keywords"]
            if isinstance(kws, str):
                kws = [k.strip() for k in kws.replace("，", ",").split(",") if k.strip()]
            fields.append("keywords = ?")
            params.append(json.dumps(kws, ensure_ascii=False))
        if "weight" in body:
            fields.append("weight = ?")
            params.append(max(1, min(30, _to_int(body["weight"], 10))))
        if "enabled" in body:
            fields.append("enabled = ?")
            params.append(1 if body["enabled"] else 0)
        if not fields:
            return {"error": "没有可更新字段"}, 400
        params.append(topic_id)
        with db.get_db() as conn:
            conn.execute(f"UPDATE topics SET {', '.join(fields)} WHERE id = ?", params)
        return {"ok": True}, 200

    def topic_delete(self, q, body, topic_id):
        with db.get_db() as conn:
            conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        return {"ok": True}, 200

    def task_run(self, q, body):
        cfg = config.load_config()
        feed_id = _to_int(body.get("feed_id")) or None
        if pipeline.STATE.get("running"):
            return {"busy": True, "message": "任务正在执行，请稍候"}, 200
        threading.Thread(
            target=lambda: pipeline.run_pipeline(cfg, feed_id=feed_id),
            daemon=True,
        ).start()
        return {"ok": True, "started": True}, 200

    def task_status(self, q, body):
        st = pipeline.status()
        st["last_result"] = pipeline.STATE.get("last_result")
        return st, 200

    def task_process(self, q, body):
        cfg = config.load_config()
        if pipeline.STATE.get("running"):
            return {"busy": True, "message": "任务正在执行，请稍候"}, 200

        def job():
            if not pipeline.LOCK.acquire(blocking=False):
                return
            try:
                pipeline._set_state(True, "加工队列")
                pipeline.STATE["started_at"] = int(time.time())
                for _ in range(10):
                    res = pipeline.process_pending(
                        cfg, progress=lambda m: pipeline._set_state(True, m)
                    )
                    if not res.get("processed"):
                        break
            finally:
                pipeline._set_state(False, "空闲")
                pipeline.STATE["started_at"] = None
                pipeline.LOCK.release()

        threading.Thread(target=job, daemon=True).start()
        return {"ok": True, "started": True}, 200

    def digest(self, q, body):
        day = (q.get("day") or [pipeline.today_str()])[0]
        return pipeline.get_digest(day), 200

    def digest_generate(self, q, body):
        day = (body.get("day") or pipeline.today_str()).strip()
        result = pipeline.generate_digest(config.load_config(), day=day, use_ai=bool(body.get("use_ai", True)))
        return result, 200

    def settings(self, q, body):
        return config.public_config(config.load_config()), 200

    def settings_save(self, q, body):
        old = config.load_config()
        new = body or {}
        ai_part = new.get("ai", {})
        if ai_part and not ai_part.get("api_key"):
            ai_part["api_key"] = old["ai"].get("api_key", "")
        cfg = config.save_config(config._merge(old, new))
        db.add_event("更新系统设置")
        return config.public_config(cfg), 200

    def test_ai(self, q, body):
        cfg = config.load_config()
        if body.get("base_url"):
            cfg["ai"]["base_url"] = body["base_url"]
        if body.get("model"):
            cfg["ai"]["model"] = body["model"]
        if body.get("api_key"):
            cfg["ai"]["api_key"] = body["api_key"]
        if not cfg["ai"].get("api_key"):
            return {"error": "请先填写 API Key"}, 400
        try:
            reply = ai.test_connection(cfg)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}, 400
        return {"ok": True, "reply": reply}, 200

    def resources(self, q, body):
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM resources ORDER BY category, CASE tier "
                "WHEN 'official' THEN 0 WHEN 'industry' THEN 1 ELSE 2 END, id"
            ).fetchall()
        return [dict(r) for r in rows], 200

    def resource_add(self, q, body):
        name = (body.get("name") or "").strip()
        url = (body.get("url") or "").strip()
        if not name:
            return {"error": "请填写站点名称"}, 400
        if url and not url.startswith(("http://", "https://")):
            return {"error": "请填写完整的 http(s) 地址"}, 400
        category = (body.get("category") or "未分类").strip()
        tier = _tier(body.get("tier"), "official")
        note = (body.get("note") or "").strip()
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO resources (name, url, category, tier, note, created_at) VALUES (?,?,?,?,?,?)",
                (name, url, category, tier, note, int(time.time())),
            )
        db.add_event(f"新增权威源 {name}")
        return {"ok": True}, 200

    def resource_update(self, q, body, rid):
        fields, params = [], []
        for key in ("name", "url", "category", "tier", "note"):
            if key in body:
                value = (body[key] or "").strip()
                if key == "tier":
                    value = _tier(value, "official")
                fields.append(f"{key} = ?")
                params.append(value)
        if not fields:
            return {"error": "没有可更新字段"}, 400
        params.append(rid)
        with db.get_db() as conn:
            conn.execute(f"UPDATE resources SET {', '.join(fields)} WHERE id = ?", params)
        return {"ok": True}, 200

    def resource_delete(self, q, body, rid):
        with db.get_db() as conn:
            row = conn.execute("SELECT name FROM resources WHERE id = ?", (rid,)).fetchone()
            conn.execute("DELETE FROM resources WHERE id = ?", (rid,))
        if row:
            db.add_event(f"删除权威源 {row['name']}")
        return {"ok": True}, 200

    def resource_subscribe(self, q, body, rid):
        """尝试把资源站点本身当作 RSS / JSON 订阅源接入。"""
        with db.get_db() as conn:
            row = conn.execute("SELECT * FROM resources WHERE id = ?", (rid,)).fetchone()
        if not row:
            return {"error": "资源不存在"}, 404
        url = (row["url"] or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"error": "该资源没有可用的网址"}, 400
        try:
            meta, items = feeds.fetch_feed(url)
        except Exception:  # noqa: BLE001
            return {"error": "该站点未提供可识别的 RSS/JSON 订阅，请在「信息源」中手动填写订阅地址"}, 400
        title = row["name"] or meta.get("title") or url
        with db.get_db() as conn:
            exists = conn.execute("SELECT id FROM feeds WHERE url = ?", (url,)).fetchone()
            if exists:
                return {"error": "该订阅已存在"}, 400
            cur = conn.execute(
                "INSERT INTO feeds (url, title, site_url, category, tier, created_at) VALUES (?,?,?,?,?,?)",
                (url, title, meta.get("site_url", ""), row["category"], _tier(row["tier"], "official"),
                 int(time.time())),
            )
            feed_id = cur.lastrowid
        db.add_event(f"从资源库新增信息源 {title}（{url}）")
        return {"ok": True, "id": feed_id, "title": title, "sample": len(items)}, 200

    def events(self, q, body):
        return db.list_events(_to_int((q.get("limit") or [40])[0], 40)), 200

    def rebuild_stats(self, q, body):
        return db.stats_overview(), 200


ROUTES = [
    ("GET", r"^/api/overview$", Api.overview, False),
    ("GET", r"^/api/knowledge$", Api.knowledge, False),
    ("GET", r"^/api/knowledge/summary$", Api.knowledge_summary, False),
    ("GET", r"^/api/intake$", Api.intake, False),
    ("POST", r"^/api/intake$", Api.intake_add, False),
    ("GET", r"^/api/items$", Api.items, False),
    ("POST", r"^/api/items/discard$", Api.items_discard, False),
    ("GET", r"^/api/items/(\d+)$", Api.item_detail, True),
    ("POST", r"^/api/items/(\d+)$", Api.item_update, True),
    ("DELETE", r"^/api/items/(\d+)$", Api.item_delete, True),
    ("GET", r"^/api/feeds$", Api.feeds, False),
    ("POST", r"^/api/feeds$", Api.feed_add, False),
    ("PATCH", r"^/api/feeds/(\d+)$", Api.feed_update, True),
    ("DELETE", r"^/api/feeds/(\d+)$", Api.feed_delete, True),
    ("GET", r"^/api/topics$", Api.topics, False),
    ("POST", r"^/api/topics$", Api.topic_add, False),
    ("PATCH", r"^/api/topics/(\d+)$", Api.topic_update, True),
    ("DELETE", r"^/api/topics/(\d+)$", Api.topic_delete, True),
    ("POST", r"^/api/tasks/run$", Api.task_run, False),
    ("POST", r"^/api/tasks/process$", Api.task_process, False),
    ("GET", r"^/api/tasks/status$", Api.task_status, False),
    ("GET", r"^/api/digest$", Api.digest, False),
    ("POST", r"^/api/digest/generate$", Api.digest_generate, False),
    ("GET", r"^/api/settings$", Api.settings, False),
    ("POST", r"^/api/settings$", Api.settings_save, False),
    ("POST", r"^/api/settings/test-ai$", Api.test_ai, False),
    ("GET", r"^/api/events$", Api.events, False),
    ("GET", r"^/api/resources$", Api.resources, False),
    ("POST", r"^/api/resources$", Api.resource_add, False),
    ("PATCH", r"^/api/resources/(\d+)$", Api.resource_update, True),
    ("DELETE", r"^/api/resources/(\d+)$", Api.resource_delete, True),
    ("POST", r"^/api/resources/(\d+)/subscribe$", Api.resource_subscribe, True),
]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "IntelStation/1.0"

    def log_message(self, fmt, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def read_body(self):
        length = _to_int(self.headers.get("Content-Length"), 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.dispatch("GET", parsed)
        elif parsed.path.startswith("/uploads/"):
            self.serve_upload(parsed.path)
        else:
            self.serve_static(parsed.path)

    def do_POST(self):
        self.dispatch("POST", urlparse(self.path))

    def do_PATCH(self):
        self.dispatch("PATCH", urlparse(self.path))

    def do_DELETE(self):
        self.dispatch("DELETE", urlparse(self.path))

    def dispatch(self, method, parsed):
        import re as _re

        body = self.read_body() if method in ("POST", "PATCH", "PUT") else {}
        for r_method, pattern, func, has_id in ROUTES:
            if r_method != method:
                continue
            m = _re.match(pattern, parsed.path)
            if not m:
                continue
            q = parse_qs(parsed.query)
            try:
                if has_id:
                    payload, status = func(self.api, q, body, int(m.group(1)))
                else:
                    payload, status = func(self.api, q, body)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                db.add_event(f"接口异常 {parsed.path}: {str(e)[:160]}", level="error")
                payload, status = {"error": str(e)}, 500
            self.send_json(payload, status)
            return
        self.send_json({"error": "接口不存在"}, 404)

    def serve_static(self, path):
        rel = unquote(path).lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            full = os.path.join(WEB_DIR, "index.html")
            rel = "index.html"
        ext = os.path.splitext(full)[1].lower()
        try:
            with open(full, "rb") as f:
                data = f.read()
        except Exception:
            self._send(500, b"internal error", "text/plain; charset=utf-8")
            return
        self._send(200, data, MIME.get(ext, "application/octet-stream"))

    def serve_upload(self, path):
        name = os.path.basename(unquote(path))
        full = os.path.join(UPLOAD_DIR, name)
        if not name or not os.path.isfile(full):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            with open(full, "rb") as f:
                data = f.read()
        except Exception:
            self._send(500, b"internal error", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(full)[1].lower()
        self._send(200, data, MIME.get(ext, "application/octet-stream"))

    @property
    def api(self):
        return Api()


def scheduler():
    """后台调度：定时采集加工，定点生成日报。"""
    last_digest_day = ""
    while True:
        try:
            cfg = config.load_config()
            interval = max(5, _to_int(cfg["schedule"].get("fetch_minutes"), 30))
            with db.get_db() as conn:
                last = conn.execute("SELECT MAX(last_fetch_at) m FROM feeds").fetchone()["m"] or 0
            due_at = last + interval * 60 if last else time.time() + interval * 60
            if time.time() >= due_at and not pipeline.STATE.get("running"):
                db.add_event(f"定时任务触发（间隔 {interval} 分钟）")
                pipeline.run_pipeline(cfg)

            digest_time = cfg["schedule"].get("digest_time") or "09:00"
            now_hm = time.strftime("%H:%M")
            today = pipeline.today_str()
            if now_hm >= digest_time and last_digest_day != today:
                with db.get_db() as conn:
                    has_items = conn.execute(
                        "SELECT COUNT(*) c FROM items WHERE fetched_at >= ?",
                        (int(time.time()) - 86400,),
                    ).fetchone()["c"]
                if has_items:
                    pipeline.generate_digest(cfg, day=today)
                    last_digest_day = today
        except Exception as e:  # noqa: BLE001
            db.add_event(f"调度器异常：{str(e)[:160]}", level="error")
        time.sleep(30)


def bootstrap_first_run():
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    if count > 0:
        return
    time.sleep(3)
    db.add_event("首次启动，开始初始化采集")
    cfg = config.load_config()
    pipeline.run_pipeline(cfg)
    for _ in range(6):
        res = pipeline.process_pending(cfg, limit=200)
        if not res.get("processed"):
            break


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    db.init_db()
    cfg = config.load_config()
    host = cfg["server"]["host"]
    port = int(cfg["server"]["port"])

    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        print("端口被占用，启动失败")
        return

    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=bootstrap_first_run, daemon=True).start()

    url = f"http://{host}:{port}"
    print("=" * 52)
    print("  个人情报站已启动")
    print(f"  访问地址: {url}")
    print("  按 Ctrl+C 停止")
    print("=" * 52)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
