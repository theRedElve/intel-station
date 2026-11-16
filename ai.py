"""大模型加工层：兼容 OpenAI Chat Completions 协议（豆包 / DeepSeek / 通义 / Kimi 等）。"""

import json
import re
import time
import urllib.error
import urllib.request

SYSTEM_PROMPT = (
    "你是一名资深情报分析师，为个人情报站做信息加工。"
    "你的输出必须是严格合法的 JSON，不能包含解释文字或 Markdown 代码块标记。"
)

SCHEMA_HINT = """请对每条情报输出一个对象，字段如下：
{
  "id": 输入的序号（整数）,
  "summary": "不超过 80 字的中文摘要，讲清发生了什么、涉及谁、有什么影响",
  "key_points": ["2-3 条关键信息，每条不超过 30 字"],
  "tags": ["2-4 个中文短标签，不带#，如 大模型、融资、芯片"],
  "topic": "命中的关注主题名称；都不命中则填空字符串",
  "entities": ["文中出现的公司/产品/人物名，最多 5 个"],
  "importance": 0-100 的整数,
  "reason": "不超过 30 字的打分理由"
}
重要性评分标准：
- 85-100：行业重大事件、政策变化、关键技术突破、与关注主题强相关且有独到信息；
- 70-84：与关注主题相关的重要进展、有价值的深度分析；
- 45-69：一般行业资讯、常规产品更新；
- 0-44：营销软文、重复报道、价值较低的内容。
信源等级：official 为政府/监管/平台官方，industry 为行业研究/学术/企业官方，media 为媒体资讯。
同等信息量下，官方与行业源更可信、信息更原始，应给予更高重要度。"""


class AIError(Exception):
    pass


TIER_BONUS = {"official": 8, "industry": 4, "media": 0}
TIER_LABELS = {"official": "官方", "industry": "行业", "media": "媒体"}


def tier_bonus(tier):
    return TIER_BONUS.get(tier or "media", 0)


VERIFY_PATTERNS = [
    (r"数据显示|数据表明|数据指出|数据统计", "数据显示"),
    (r"报告指出|报告显示|报告称|白皮书|蓝皮书", "报告指出"),
    (r"研究显示|研究表明|研究发现|调查显示|调研显示|统计显示|抽样调查", "研究显示"),
    (r"据统计|据测算|据估算|第三方数据|机构测算", "据统计"),
]


def detect_verify_hint(*texts):
    """识别引用数据/研究结论的表述，返回命中类型；无命中返回空字符串。"""
    text = " ".join(t or "" for t in texts)[:4000]
    for pattern, label in VERIFY_PATTERNS:
        if re.search(pattern, text):
            return label
    return ""


def is_ready(cfg):
    ai = cfg.get("ai", {})
    return bool(ai.get("enabled") and ai.get("api_key") and ai.get("base_url") and ai.get("model"))


def _post(cfg, payload):
    ai = cfg["ai"]
    url = ai["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ai['api_key']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=int(ai.get("timeout", 90))) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise AIError(f"模型接口返回 HTTP {e.code}: {detail}")
    except Exception as e:  # noqa: BLE001
        raise AIError(f"模型接口不可用: {e}")

    try:
        return data["choices"][0]["message"]["content"] or ""
    except Exception:
        raise AIError(f"模型返回格式异常: {str(data)[:300]}")


def _extract_json_array(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise AIError("模型未返回 JSON 数组")
    return json.loads(text[start:end + 1])


def _clip(text, limit):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def analyze_batch(items, topics, cfg):
    """对一批情报做摘要/标签/打分，返回 {序号: 分析结果}。"""
    topic_lines = "\n".join(
        f"- {t['name']}（权重 {t.get('weight', 10)}；关键词：{', '.join(t.get('keywords', []))}）"
        for t in topics
    ) or "- 暂无关注主题"

    payload_items = []
    for idx, it in enumerate(items, 1):
        payload_items.append({
            "id": idx,
            "title": it["title"],
            "source": it.get("source", ""),
            "source_tier": it.get("source_tier") or "media",
            "published": time.strftime("%Y-%m-%d %H:%M", time.localtime(it.get("published_at") or time.time())),
            "content": _clip(it.get("content", ""), 1800),
        })

    user_prompt = (
        f"我的关注主题：\n{topic_lines}\n\n"
        f"待加工情报（JSON）：\n{json.dumps(payload_items, ensure_ascii=False)}\n\n"
        f"{SCHEMA_HINT}\n\n"
        f"请输出 JSON 数组，数量必须等于输入的 {len(payload_items)} 条，id 与输入一一对应。"
    )

    content = _post(cfg, {
        "model": cfg["ai"]["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    })

    arr = _extract_json_array(content)
    out = {}
    for obj in arr:
        if not isinstance(obj, dict):
            continue
        try:
            idx = int(obj.get("id"))
        except Exception:
            continue
        out[idx] = _normalize_analysis(obj, topics)
    if not out:
        raise AIError("模型返回了空结果")
    return out


def _normalize_analysis(obj, topics):
    topic_names = {t["name"] for t in topics}
    raw_points = obj.get("key_points") or []
    if isinstance(raw_points, str):
        raw_points = [raw_points]
    raw_tags = obj.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = re.split(r"[,，、\s]+", raw_tags)
    raw_entities = obj.get("entities") or []
    if isinstance(raw_entities, str):
        raw_entities = re.split(r"[,，、]+", raw_entities)

    try:
        importance = int(float(str(obj.get("importance") or 0).strip().rstrip("分")))
    except Exception:
        importance = 0

    topic = str(obj.get("topic") or "").strip()
    return {
        "summary": _clip(str(obj.get("summary") or ""), 200),
        "key_points": [_clip(str(p), 60) for p in raw_points if str(p).strip()][:4],
        "tags": [_clip(str(t).lstrip("#"), 16) for t in raw_tags if str(t).strip()][:5],
        "entities": [_clip(str(e), 24) for e in raw_entities if str(e).strip()][:6],
        "topic": topic if topic in topic_names else topic[:30],
        "importance": max(0, min(100, importance)),
        "reason": _clip(str(obj.get("reason") or ""), 60),
    }


def match_topics(text, topics):
    hits = []
    low = (text or "").lower()
    for t in topics:
        for kw in t.get("keywords", []):
            if kw and kw.lower() in low:
                hits.append(t)
                break
    return hits


JUNK_LINE = re.compile(
    r"点击查看原文|阅读原文|原文链接|欢迎关注|微信号|扫码|长按识别|关注我们|"
    r"未经授权|版权归|本文来自|原标题|编辑：|排版：|题图来自"
)


def _rule_summary(item):
    lines = [ln.strip() for ln in (item.get("content") or "").split("\n") if ln.strip()]
    text = ""
    for line in lines:
        if len(line) < 12:
            continue
        match = JUNK_LINE.search(line)
        if match:
            prefix = line[:match.start()].strip()
            if len(prefix) >= 12:
                text = prefix
            break
        text = line
        break
    if len(text) < 12:
        return ""
    return _clip(text, 110)


def rule_based_analysis(item, topics):
    """未配置模型时的兜底：关键词命中 + 时效性加权。"""
    title_hits = match_topics(item["title"], topics)
    content_hits = [
        t for t in match_topics(item.get("content", ""), topics)
        if t not in title_hits
    ]
    score = 38
    for t in title_hits:
        score += min(18, 8 + int(t.get("weight", 10) / 3))
    score += min(6, 3 * len(content_hits))
    age = time.time() - (item.get("published_at") or time.time())
    if age < 86400:
        score += 8
    elif age < 3 * 86400:
        score += 4
    if re.search(r"融资|发布|首发|开源|突破|收购|上市|政策|监管|裁员|禁令", item["title"]):
        score += 7
    if len(item.get("content", "")) > 800:
        score += 3
    score = max(5, min(96, score))

    summary = _rule_summary(item)
    all_hits = title_hits + content_hits
    tags = [t["name"] for t in all_hits][:3] or ["资讯"]
    return {
        "summary": summary,
        "key_points": [],
        "tags": tags,
        "entities": [],
        "topic": all_hits[0]["name"] if all_hits else "",
        "importance": score,
        "reason": "关键词与时效性规则评分",
    }


def period_summary(period, cfg):
    """把一段时间内的已读情报写成一段中文总结。"""
    label = "近三个月" if period["months"] >= 3 else "近一个月"
    topic_line = "、".join(f"{t['topic']}（{t['c']} 条）" for t in period["topics"]) or "无"
    item_lines = "\n".join(
        f"- [{it.get('topic') or '未分类'}] {it['title']}：{_clip(it.get('summary') or '', 90)}"
        for it in period["samples"]
    ) or "- 无"
    user_prompt = (
        f"时间范围：{label}；已读 {period['total']} 条；高价值 {period['high']} 条；"
        f"主题分布：{topic_line}。\n"
        f"代表情报：\n{item_lines}\n\n"
        "请用 120-180 字中文总结这段时间的知识重点、关注方向的变化和值得回看的内容，"
        "语气克制，不要逐条罗列，不要编造数据，只输出总结正文。"
    )
    content = _post(cfg, {
        "model": cfg["ai"]["model"],
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": 0.3,
        "max_tokens": 600,
    })
    return _clip(content, 400)


def test_connection(cfg):
    content = _post(cfg, {
        "model": cfg["ai"]["model"],
        "messages": [{"role": "user", "content": "只回复两个字：可用"}],
        "temperature": 0,
        "max_tokens": 16,
    })
    return _clip(content, 40)


def write_digest_overview(day, items, cfg):
    """让模型写一段当日情报综述；失败时返回空字符串。"""
    if not items:
        return ""
    lines = [
        f"- [{it.get('importance', 0)}] {it['title']}：{it.get('summary', '')}"
        for it in items[:18]
    ]
    user_prompt = (
        f"以下是 {day} 收录的情报条目：\n" + "\n".join(lines) +
        "\n\n请写一段 120-180 字的中文情报综述：点出当天最值得关注的 2-3 件事、彼此关联与潜在影响。"
        "只输出正文，不要标题，不要 Markdown。"
    )
    try:
        content = _post(cfg, {
            "model": cfg["ai"]["model"],
            "messages": [
                {"role": "system", "content": "你是冷静、克制的行业情报分析师。"},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 600,
        })
        return _clip(content, 600)
    except Exception:
        return ""
