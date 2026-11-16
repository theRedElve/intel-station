"""SQLite 存储层。所有表结构、初始化种子数据、常用查询都放这里。"""

import os
import sqlite3
import time
import json
from contextlib import contextmanager

from config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "intel.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    site_url TEXT DEFAULT '',
    category TEXT DEFAULT '未分类',
    tier TEXT DEFAULT 'media',
    enabled INTEGER DEFAULT 1,
    created_at INTEGER,
    last_fetch_at INTEGER,
    last_status TEXT DEFAULT '',
    error_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER,
    dedupe_key TEXT UNIQUE,
    url TEXT DEFAULT '',
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    published_at INTEGER,
    fetched_at INTEGER,
    content TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    key_points TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    entities TEXT DEFAULT '[]',
    importance INTEGER DEFAULT 0,
    reason TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    verify_hint TEXT DEFAULT '',
    processed INTEGER DEFAULT 0,
    read INTEGER DEFAULT 0,
    starred INTEGER DEFAULT 0,
    pushed INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_items_pub ON items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_state ON items(processed, importance DESC);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    keywords TEXT DEFAULT '[]',
    weight INTEGER DEFAULT 10,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT UNIQUE NOT NULL,
    content TEXT DEFAULT '',
    item_count INTEGER DEFAULT 0,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT DEFAULT '',
    category TEXT DEFAULT '未分类',
    tier TEXT DEFAULT 'official',
    note TEXT DEFAULT '',
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    level TEXT DEFAULT 'info',
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
"""

SEED_FEEDS = [
    ("https://sspai.com/feed", "少数派", "https://sspai.com", "产品效率"),
    ("https://www.ifanr.com/feed", "爱范儿", "https://www.ifanr.com", "科技商业"),
    ("https://www.qbitai.com/feed", "量子位", "https://www.qbitai.com", "AI"),
    ("https://www.solidot.org/index.rss", "Solidot", "https://www.solidot.org", "技术"),
    ("https://www.infoq.cn/feed", "InfoQ 中文", "https://www.infoq.cn", "技术"),
    ("https://www.geekpark.net/rss", "极客公园", "https://www.geekpark.net", "科技商业"),
    ("https://www.36kr.com/feed", "36氪", "https://36kr.com", "创投商业"),
]

SEED_TOPICS = [
    ("AI 与大模型", ["大模型", "AI", "GPT", "LLM", "Agent", "智能体", "AIGC", "算力"], 12),
    ("芯片与硬件", ["芯片", "半导体", "GPU", "英伟达", "台积电", "光刻"], 10),
    ("创业与融资", ["融资", "估值", "创业", "IPO", "并购", "轮融资"], 10),
    ("产品与设计", ["产品", "设计", "用户体验", "交互", "增长"], 8),
    ("开源与技术", ["开源", "GitHub", "框架", "发布", "版本"], 8),
]

# 现有订阅的信源等级（仅在首次新增 tier 字段时写入，之后以界面设置为准）
DEFAULT_FEED_TIERS = {
    "统计之都 COS": "industry",
    "美团技术团队": "industry",
    "有赞技术团队": "industry",
}

# 权威信息源清单：按「政府/监管与平台官方 > 行业研究 > 媒体资讯」组织。
# note 标注「待核验」的链接建议在浏览器中人工确认后再长期使用。
SEED_RESOURCES = [
    # 政策与监管
    ("国家市场监督管理总局", "https://www.samr.gov.cn", "政策与监管", "official", "市场监管政策与处罚公告原文"),
    ("商务部", "http://www.mofcom.gov.cn", "政策与监管", "official", "商贸流通、电子商务政策发布"),
    ("工业和信息化部", "https://www.miit.gov.cn", "政策与监管", "official", "产业政策与行业运行数据"),
    ("国家互联网信息办公室", "https://www.cac.gov.cn", "政策与监管", "official", "网信政策、法规与执法信息"),
    ("国家税务总局", "https://www.chinatax.gov.cn", "政策与监管", "official", "税收政策与征管公告"),
    ("国家发展和改革委员会", "https://www.ndrc.gov.cn", "政策与监管", "official", "宏观政策与产业规划"),
    ("国家邮政局", "https://www.spb.gov.cn", "政策与监管", "official", "快递与物流行业运行数据"),
    # 数据与统计
    ("国家统计局", "https://www.stats.gov.cn", "数据与统计", "official", "宏观与行业统计数据原始口径"),
    ("国家统计局数据查询", "https://data.stats.gov.cn", "数据与统计", "official", "分行业、分地区数据检索"),
    ("中国互联网络信息中心", "https://www.cnnic.net.cn", "数据与统计", "official", "CNNIC 互联网发展统计报告"),
    ("巨量算数", "https://trendinsight.oceanengine.com", "数据与统计", "industry", "抖音生态内容与消费趋势"),
    ("百度指数", "https://index.baidu.com", "数据与统计", "industry", "搜索热度与人群画像"),
    ("艾瑞咨询", "https://www.iresearch.com.cn", "数据与统计", "industry", "行业研究报告与数据"),
    ("QuestMobile", "https://www.questmobile.com.cn", "数据与统计", "industry", "移动互联网用户与流量数据"),
    ("易观分析", "https://www.analysys.cn", "数据与统计", "industry", "数字消费与行业分析"),
    # 行业研究
    ("中国信息通信研究院", "https://www.caict.ac.cn", "行业研究", "official", "ICT 与数字经济白皮书"),
    ("中国电子商务研究中心", "https://www.100ec.cn", "行业研究", "industry", "电商行业数据与研究报告"),
    ("亿邦动力", "https://www.ebrun.com", "行业研究", "industry", "电商与零售行业资讯"),
    ("中国连锁经营协会", "http://www.ccfa.org.cn", "行业研究", "industry", "连锁零售行业报告"),
    ("德勤中国", "https://www2.deloitte.com/cn/zh.html", "行业研究", "industry", "咨询行业研究报告"),
    ("麦肯锡中国", "https://www.mckinsey.com.cn", "行业研究", "industry", "咨询行业洞察与报告"),
    ("波士顿咨询", "https://www.bcg.com/zh-cn", "行业研究", "industry", "咨询行业研究报告"),
    # 平台规则
    ("淘宝规则中心", "https://rule.taobao.com", "平台规则", "official", "淘宝平台规则与违规处理"),
    ("天猫规则", "https://rule.tmall.com", "平台规则", "official", "天猫商家规则与招商政策"),
    ("京东商家规则", "https://rule.jd.com", "平台规则", "official", "京东平台规则中心"),
    ("拼多多商家规则", "https://rule.pinduoduo.com", "平台规则", "official", "链接待核验"),
    ("抖音电商学习中心", "https://school.jinritemai.com", "平台规则", "official", "抖音电商规则与经营课程"),
    ("快手电商规则", "https://school.kwaixiaodian.com", "平台规则", "official", "链接待核验"),
    ("微信公众平台", "https://mp.weixin.qq.com", "平台规则", "official", "运营规范与内容政策"),
    ("亚马逊全球开店政策", "https://sell.amazon.com/zh/policies", "平台规则", "official", "跨境平台政策与合规要求"),
    # 学术与论文
    ("中国知网", "https://www.cnki.net", "学术与论文", "industry", "中文学术文献检索"),
    ("万方数据", "https://www.wanfangdata.com.cn", "学术与论文", "industry", "中文期刊与学位论文"),
    ("arXiv", "https://arxiv.org", "学术与论文", "industry", "预印本论文，前沿研究首选"),
    ("SSRN", "https://www.ssrn.com", "学术与论文", "industry", "社科与商科预印本"),
    ("Google Scholar", "https://scholar.google.com", "学术与论文", "industry", "跨库学术检索"),
    # 资本与财报
    ("巨潮资讯网", "http://www.cninfo.com.cn", "资本与财报", "official", "A 股上市公司公告与财报原文"),
    ("上海证券交易所", "https://www.sse.com.cn", "资本与财报", "official", "上市公司信息披露"),
    ("深圳证券交易所", "https://www.szse.cn", "资本与财报", "official", "上市公司信息披露"),
    ("美国 SEC EDGAR", "https://www.sec.gov/edgar", "资本与财报", "official", "美股公司财报与披露原文"),
    ("香港交易所披露易", "https://www.hkexnews.hk", "资本与财报", "official", "港股公司公告与财报"),
]

TIERS = ("official", "industry", "media")


@contextmanager
def get_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn):
    """对旧数据库做增量字段迁移。"""
    feed_cols = _columns(conn, "feeds")
    if "tier" not in feed_cols:
        conn.execute("ALTER TABLE feeds ADD COLUMN tier TEXT DEFAULT 'media'")
        for title, tier in DEFAULT_FEED_TIERS.items():
            conn.execute("UPDATE feeds SET tier = ? WHERE title = ?", (tier, title))
    item_cols = _columns(conn, "items")
    if "verify_hint" not in item_cols:
        conn.execute("ALTER TABLE items ADD COLUMN verify_hint TEXT DEFAULT ''")


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        row = conn.execute("SELECT COUNT(*) AS c FROM feeds").fetchone()
        if row["c"] == 0:
            now = int(time.time())
            for url, title, site, cat in SEED_FEEDS:
                conn.execute(
                    "INSERT OR IGNORE INTO feeds (url, title, site_url, category, created_at) VALUES (?,?,?,?,?)",
                    (url, title, site, cat, now),
                )
            for title, tier in DEFAULT_FEED_TIERS.items():
                conn.execute("UPDATE feeds SET tier = ? WHERE title = ?", (tier, title))
        row = conn.execute("SELECT COUNT(*) AS c FROM topics").fetchone()
        if row["c"] == 0:
            now = int(time.time())
            for name, kws, weight in SEED_TOPICS:
                conn.execute(
                    "INSERT OR IGNORE INTO topics (name, keywords, weight, created_at) VALUES (?,?,?,?)",
                    (name, json.dumps(kws, ensure_ascii=False), weight, now),
                )
        row = conn.execute("SELECT COUNT(*) AS c FROM resources").fetchone()
        if row["c"] == 0:
            now = int(time.time())
            for name, url, category, tier, note in SEED_RESOURCES:
                conn.execute(
                    "INSERT INTO resources (name, url, category, tier, note, created_at) VALUES (?,?,?,?,?,?)",
                    (name, url, category, tier, note, now),
                )


def add_event(message, level="info"):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO events (ts, level, message) VALUES (?,?,?)",
                (int(time.time()), level, message[:500]),
            )
            conn.execute(
                "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 400)"
            )
    except Exception:
        pass


def list_events(limit=40):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ts, level, message FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def row_to_item(row):
    d = dict(row)
    for field in ("key_points", "tags", "entities"):
        try:
            d[field] = json.loads(d.get(field) or "[]")
        except Exception:
            d[field] = []
    return d


def get_item(item_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return row_to_item(row) if row else None


def query_items(status="all", feed_id=None, topic=None, tag=None, q=None,
                min_importance=0, days=None, sort="time", limit=20, offset=0):
    where = ["1=1"]
    params = []
    if status == "unread":
        where.append("items.read = 0")
    elif status == "high":
        where.append("items.importance >= 70")
    elif status == "starred":
        where.append("items.starred = 1")
    if feed_id:
        where.append("items.feed_id = ?")
        params.append(feed_id)
    if topic:
        where.append("items.topic = ?")
        params.append(topic)
    if tag:
        where.append("items.tags LIKE ?")
        params.append(f"%{tag}%")
    if q:
        where.append("(items.title LIKE ? OR items.summary LIKE ? OR items.content LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if min_importance:
        where.append("items.importance >= ?")
        params.append(min_importance)
    if days:
        where.append("items.published_at >= ?")
        params.append(int(time.time()) - days * 86400)

    order = "items.importance DESC, items.published_at DESC" if sort == "importance" \
        else "items.published_at DESC"
    sql = (
        "SELECT items.*, feeds.title AS source, feeds.tier AS source_tier FROM items "
        "LEFT JOIN feeds ON feeds.id = items.feed_id "
        f"WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?"
    )
    count_sql = f"SELECT COUNT(*) AS c FROM items WHERE {' AND '.join(where)}"

    with get_db() as conn:
        total = conn.execute(count_sql, params).fetchone()["c"]
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
    return {"total": total, "items": [row_to_item(r) for r in rows]}


def stats_overview():
    now = int(time.time())
    lt = time.localtime(now)
    day_start = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        today = conn.execute(
            "SELECT COUNT(*) c FROM items WHERE fetched_at >= ?", (day_start,)
        ).fetchone()["c"]
        unread = conn.execute("SELECT COUNT(*) c FROM items WHERE read = 0").fetchone()["c"]
        high = conn.execute(
            "SELECT COUNT(*) c FROM items WHERE importance >= 70 AND read = 0"
        ).fetchone()["c"]
        processed = conn.execute("SELECT COUNT(*) c FROM items WHERE processed = 0").fetchone()["c"]
        feeds = conn.execute("SELECT COUNT(*) c FROM feeds WHERE enabled = 1").fetchone()["c"]
        starred = conn.execute("SELECT COUNT(*) c FROM items WHERE starred = 1").fetchone()["c"]

        trend = []
        for i in range(6, -1, -1):
            start = day_start - i * 86400
            end = start + 86400
            c = conn.execute(
                "SELECT COUNT(*) c FROM items WHERE fetched_at >= ? AND fetched_at < ?",
                (start, end),
            ).fetchone()["c"]
            trend.append({"day": time.strftime("%m-%d", time.localtime(start)), "count": c})

        tags = conn.execute(
            "SELECT tags FROM items WHERE tags != '[]' ORDER BY published_at DESC LIMIT 400"
        ).fetchall()
        tag_counter = {}
        for r in tags:
            try:
                for t in json.loads(r["tags"]):
                    t = str(t).strip()
                    if t:
                        tag_counter[t] = tag_counter.get(t, 0) + 1
            except Exception:
                pass
        top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])[:12]

        sources = conn.execute(
            """SELECT f.id, f.title, COUNT(i.id) c,
                      SUM(CASE WHEN i.importance >= 70 THEN 1 ELSE 0 END) high
                 FROM feeds f LEFT JOIN items i ON i.feed_id = f.id
                GROUP BY f.id ORDER BY c DESC LIMIT 8"""
        ).fetchall()

        topic_rows = conn.execute("SELECT name, weight FROM topics WHERE enabled = 1").fetchall()
        topic_counts = []
        for t in topic_rows:
            c = conn.execute(
                "SELECT COUNT(*) c FROM items WHERE topic = ?", (t["name"],)
            ).fetchone()["c"]
            topic_counts.append({"name": t["name"], "count": c, "weight": t["weight"]})

        last_fetch = conn.execute("SELECT MAX(last_fetch_at) m FROM feeds").fetchone()["m"]

    return {
        "total": total,
        "today": today,
        "unread": unread,
        "high": high,
        "pending": processed,
        "feeds": feeds,
        "starred": starred,
        "trend": trend,
        "top_tags": [{"tag": k, "count": v} for k, v in top_tags],
        "sources": [dict(s) for s in sources],
        "topics": topic_counts,
        "last_fetch_at": last_fetch,
    }
