"""采集 → 加工 → 存储 → 推送 的主流水线。"""

import json
import re
import threading
import time
from datetime import datetime

import ai
import db
import feeds
import push

LOCK = threading.Lock()
STATE = {"running": False, "stage": "空闲", "started_at": None, "last_result": None}

HIGH_VALUE = 70


def status():
    return dict(STATE)


def _set_state(running, stage):
    STATE["running"] = running
    STATE["stage"] = stage
    if running and not STATE.get("started_at"):
        STATE["started_at"] = int(time.time())


def _title_shingles(title):
    t = re.sub(r"[\W_]+", "", (title or "").lower())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _similar(a, b):
    if not a or not b:
        return False
    inter = len(a & b)
    union = len(a | b)
    return union > 0 and inter / union >= 0.82


def _load_recent_titles(days=3):
    since = int(time.time()) - days * 86400
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT title FROM items WHERE published_at >= ? ORDER BY published_at DESC LIMIT 600",
            (since,),
        ).fetchall()
    return [(_title_shingles(r["title"]), r["title"]) for r in rows]


def fetch_all(cfg, feed_id=None, progress=None):
    with db.get_db() as conn:
        if feed_id:
            feed_rows = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,)).fetchall()
        else:
            feed_rows = conn.execute("SELECT * FROM feeds WHERE enabled = 1 ORDER BY id").fetchall()
    feed_rows = [dict(r) for r in feed_rows]
    if not feed_rows:
        return {"feeds": 0, "new": 0, "details": []}

    recent = _load_recent_titles()
    max_items = int(cfg.get("collect", {}).get("max_items_per_feed") or 30)
    details = []
    total_new = 0

    for idx, feed in enumerate(feed_rows, 1):
        name = feed["title"] or feed["url"]
        if progress:
            progress(f"采集 {idx}/{len(feed_rows)} · {name}")
        try:
            meta, items = feeds.fetch_feed(feed["url"])
            items = items[:max_items]
            new_count = 0
            now = int(time.time())
            with db.get_db() as conn:
                for it in items:
                    shingles = _title_shingles(it["title"])
                    if any(_similar(shingles, s) for s, _ in recent):
                        continue
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO items
                           (feed_id, dedupe_key, url, title, author, published_at, fetched_at, content)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (feed["id"], it["dedupe_key"], it["url"], it["title"],
                         it["author"], it["published_at"], now, it["content"]),
                    )
                    if cur.rowcount:
                        new_count += 1
                        recent.append((shingles, it["title"]))
                conn.execute(
                    """UPDATE feeds SET last_fetch_at = ?, last_status = ?, error_count = 0,
                       title = CASE WHEN title = '' THEN ? ELSE title END,
                       site_url = CASE WHEN site_url IS NULL OR site_url = '' THEN ? ELSE site_url END
                       WHERE id = ?""",
                    (now, f"成功 · 新增 {new_count} 条", meta.get("title", ""),
                     meta.get("site_url", ""), feed["id"]),
                )
            total_new += new_count
            details.append({"feed": name, "ok": True, "new": new_count, "total": len(items)})
            db.add_event(f"采集成功 {name}：拉取 {len(items)} 条，新增 {new_count} 条")
        except Exception as e:  # noqa: BLE001
            msg = str(e)[:180]
            with db.get_db() as conn:
                conn.execute(
                    "UPDATE feeds SET last_fetch_at = ?, last_status = ?, error_count = error_count + 1 WHERE id = ?",
                    (int(time.time()), f"失败 · {msg}", feed["id"]),
                )
            details.append({"feed": name, "ok": False, "error": msg, "new": 0})
            db.add_event(f"采集失败 {name}：{msg}", level="error")

    keep_days = int(cfg.get("collect", {}).get("keep_days") or 0)
    if keep_days > 0:
        cutoff = int(time.time()) - keep_days * 86400
        with db.get_db() as conn:
            conn.execute("DELETE FROM items WHERE fetched_at < ?", (cutoff,))

    return {"feeds": len(feed_rows), "new": total_new, "details": details}


def process_pending(cfg, limit=None, progress=None):
    topics = get_topics()
    limit = int(limit or cfg.get("schedule", {}).get("process_limit") or 40)
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE processed = 0 ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    pending = [dict(r) for r in rows]
    if not pending:
        return {"processed": 0, "ai": False, "error": ""}

    for row in pending:
        try:
            row["tags"] = json.loads(row.get("tags") or "[]")
        except Exception:
            row["tags"] = []
    with db.get_db() as conn:
        feed_map = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM feeds").fetchall()}
    for row in pending:
        row["source"] = feed_map.get(row["feed_id"], "")

    use_ai = ai.is_ready(cfg)
    batch_size = max(1, int(cfg.get("ai", {}).get("batch_size") or 6))
    ai_error = ""
    processed = 0
    ai_used = False

    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        if progress:
            progress(f"加工情报 {start + 1}-{start + len(batch)}/{len(pending)}")
        results = {}
        if use_ai and not ai_error:
            try:
                results = ai.analyze_batch(batch, topics, cfg)
                ai_used = True
            except Exception as e:  # noqa: BLE001
                ai_error = str(e)[:200]
                db.add_event(f"模型加工失败，已回退规则评分：{ai_error}", level="error")

        now = int(time.time())
        with db.get_db() as conn:
            for idx, item in enumerate(batch, 1):
                res = results.get(idx) or ai.rule_based_analysis(item, topics)
                conn.execute(
                    """UPDATE items SET summary = ?, key_points = ?, tags = ?, entities = ?,
                       importance = ?, reason = ?, topic = ?, processed = 1 WHERE id = ?""",
                    (res["summary"], json.dumps(res["key_points"], ensure_ascii=False),
                     json.dumps(res["tags"], ensure_ascii=False),
                     json.dumps(res["entities"], ensure_ascii=False),
                     int(res["importance"]), res["reason"], res["topic"], item["id"]),
                )
                processed += 1
        time.sleep(0.1)

    if ai_error:
        db.add_event("本批情报使用规则评分完成（模型不可用）", level="warn")

    # 高价值即时推送
    pushed = 0
    push_cfg = cfg.get("push", {})
    schedule = cfg.get("schedule", {})
    if push_cfg.get("enabled") and schedule.get("push_on_fetch", True):
        threshold = int(push_cfg.get("min_importance") or 78)
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM items WHERE pushed = 0 AND processed = 1 AND importance >= ? "
                "ORDER BY importance DESC LIMIT ?",
                (threshold, int(push_cfg.get("max_items") or 6)),
            ).fetchall()
        candidates = [db.row_to_item(r) for r in rows]
        if candidates:
            for c in candidates:
                c["source"] = feed_map.get(c["feed_id"], "")
            try:
                pushed = push.push_high_value(candidates, cfg)
                ids = [c["id"] for c in candidates]
                with db.get_db() as conn:
                    conn.executemany("UPDATE items SET pushed = 1 WHERE id = ?", [(i,) for i in ids])
                db.add_event(f"已推送 {len(candidates)} 条高价值情报")
            except Exception as e:  # noqa: BLE001
                db.add_event(f"推送失败：{str(e)[:160]}", level="error")

    db.add_event(f"加工完成 {processed} 条（{'模型' if ai_used else '规则'}）")
    return {"processed": processed, "ai": ai_used, "error": ai_error, "pushed": pushed}


def run_pipeline(cfg, feed_id=None, do_process=True):
    if not LOCK.acquire(blocking=False):
        return {"busy": True, "message": "已有任务在执行"}
    try:
        _set_state(True, "开始采集")
        STATE["started_at"] = int(time.time())
        t0 = time.time()
        fetch_res = fetch_all(cfg, feed_id=feed_id, progress=lambda m: _set_state(True, m))
        process_res = {"processed": 0, "pushed": 0}
        if do_process and cfg.get("schedule", {}).get("auto_process", True):
            _set_state(True, "开始加工")
            process_res = process_pending(cfg, progress=lambda m: _set_state(True, m))
        _set_state(False, "空闲")
        result = {
            "busy": False,
            "fetched": fetch_res,
            "processed": process_res,
            "cost_ms": int((time.time() - t0) * 1000),
        }
        STATE["last_result"] = result
        db.add_event(
            f"一轮任务完成：新增 {fetch_res['new']} 条，加工 {process_res.get('processed', 0)} 条，"
            f"推送 {process_res.get('pushed', 0)} 条"
        )
        return result
    finally:
        _set_state(False, "空闲")
        STATE["started_at"] = None
        LOCK.release()


def get_topics():
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM topics WHERE enabled = 1 ORDER BY weight DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["keywords"] = json.loads(d.get("keywords") or "[]")
        except Exception:
            d["keywords"] = []
        out.append(d)
    return out


def today_str():
    return time.strftime("%Y-%m-%d")


def _day_range(day):
    dt = datetime.strptime(day, "%Y-%m-%d")
    start = int(dt.timestamp())
    return start, start + 86400


def generate_digest(cfg, day=None, use_ai=True):
    day = day or today_str()
    start, end = _day_range(day)
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE fetched_at >= ? AND fetched_at < ? "
            "ORDER BY importance DESC, published_at DESC LIMIT 160",
            (start, end),
        ).fetchall()
        feed_map = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM feeds").fetchall()}
    items = []
    for r in rows:
        d = db.row_to_item(r)
        d["source"] = feed_map.get(d["feed_id"], "")
        items.append(d)

    if not items:
        content = f"# 每日情报简报 · {day}\n\n> 今日暂无采集到情报。请在「信息源」中检查订阅是否正常。\n"
        _save_digest(day, content, 0)
        return {"day": day, "content": content, "item_count": 0}

    top = [i for i in items if i["importance"] >= HIGH_VALUE]
    overview = ai.write_digest_overview(day, items, cfg) if (use_ai and ai.is_ready(cfg)) else ""

    lines = [f"# 每日情报简报 · {day}", ""]
    lines.append(f"> 今日收录 **{len(items)}** 条，其中高价值 **{len(top)}** 条，"
                 f"覆盖 **{len({i['source'] for i in items})}** 个信息源。")
    lines.append("")

    if overview:
        lines += ["## 今日综述", "", overview, ""]

    if top:
        lines += ["## 关键情报", ""]
        for it in top[:12]:
            lines.append(f"### [{it['importance']}] {it['title']}")
            if it.get("summary"):
                lines.append(it["summary"])
            if it.get("key_points"):
                lines.append("")
                for p in it["key_points"]:
                    lines.append(f"- {p}")
            meta = " · ".join(x for x in [
                it.get("source", ""),
                time.strftime("%m-%d %H:%M", time.localtime(it.get("published_at") or time.time())),
                "、".join(it.get("tags", [])),
            ] if x)
            if it.get("url"):
                lines.append(f"\n[{meta}]({it['url']})")
            else:
                lines.append(f"\n{meta}")
            lines.append("")

    grouped = {}
    for it in items:
        key = it.get("topic") or "其他动态"
        grouped.setdefault(key, []).append(it)
    if grouped:
        lines += ["## 主题动态", ""]
        for topic, group in grouped.items():
            high_n = len([x for x in group if x["importance"] >= HIGH_VALUE])
            lines.append(f"**{topic}** · {len(group)} 条（高价值 {high_n}）")
            for it in group[:5]:
                lines.append(f"- [{it['importance']}] {it['title']} — {it.get('summary', '')[:60]}")
            lines.append("")

    lines += ["## 全部情报", ""]
    for it in items[:40]:
        src = it.get("source", "")
        lines.append(f"- **[{it['importance']}] {it['title']}** · {src}")

    content = "\n".join(lines).strip() + "\n"
    _save_digest(day, content, len(items))
    db.add_event(f"已生成 {day} 情报简报（{len(items)} 条）")
    return {"day": day, "content": content, "item_count": len(items)}


def _save_digest(day, content, count):
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO digests (day, content, item_count, created_at) VALUES (?,?,?,?)
               ON CONFLICT(day) DO UPDATE SET content = excluded.content,
               item_count = excluded.item_count, created_at = excluded.created_at""",
            (day, content, count, int(time.time())),
        )


def get_digest(day=None):
    day = day or today_str()
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM digests WHERE day = ?", (day,)).fetchone()
    if not row:
        return {"day": day, "content": "", "item_count": 0}
    return dict(row)
