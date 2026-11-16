"""推送层：飞书群机器人 / 邮件。"""

import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


class PushError(Exception):
    pass


def _post_json(url, payload, timeout=15):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        raise PushError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
    except Exception as e:  # noqa: BLE001
        raise PushError(str(e))
    try:
        data = json.loads(text)
    except Exception:
        return text
    code = data.get("code", data.get("StatusCode", 0))
    if code not in (0, None):
        raise PushError(f"飞书返回错误: {data}")
    return data


def item_lines(items, with_reason=False):
    lines = []
    for it in items:
        score = it.get("importance", 0)
        title = it.get("title", "").strip()
        url = it.get("url") or ""
        summary = (it.get("summary") or "").strip()
        head = f"**[{score}] {title}**" if not url else f"**[{score}] [{title}]({url})**"
        line = head
        if summary:
            line += f"\n{summary}"
        if with_reason and it.get("reason"):
            line += f"\n<font color='grey'>打分理由：{it['reason']}</font>"
        src = it.get("source") or ""
        if src:
            line += f"\n<font color='grey'>来源：{src}</font>"
        lines.append(line)
    return lines


def send_feishu(webhook, title, markdown_blocks, footer=""):
    if not webhook:
        raise PushError("未配置飞书 Webhook 地址")
    elements = []
    for i, block in enumerate(markdown_blocks):
        if i > 0:
            elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": block}})
    if footer:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]})
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title[:120]},
            },
            "elements": elements,
        },
    }
    return _post_json(webhook, payload)


def send_email(email_cfg, subject, html_body):
    if not email_cfg.get("smtp_host") or not email_cfg.get("to"):
        raise PushError("邮件配置不完整")
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("个人情报站", "utf-8")), email_cfg.get("username", "")))
    msg["To"] = email_cfg["to"]

    host = email_cfg["smtp_host"]
    port = int(email_cfg.get("smtp_port") or 465)
    user = email_cfg.get("username", "")
    password = email_cfg.get("password", "")
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.starttls(context=ssl.create_default_context())
    try:
        if user:
            server.login(user, password)
        server.sendmail(user, [x.strip() for x in email_cfg["to"].split(",") if x.strip()], msg.as_string())
    finally:
        server.quit()


def _email_html(title, items):
    parts = [f"<h2>{title}</h2>"]
    for it in items:
        parts.append(
            "<div style='margin:16px 0;padding-bottom:12px;border-bottom:1px solid #eee'>"
            f"<div style='font-size:12px;color:#888'>重要度 {it.get('importance', 0)} · {it.get('source', '')}</div>"
            f"<h3 style='margin:6px 0'><a href=\"{it.get('url', '')}\">{it.get('title', '')}</a></h3>"
            f"<p style='color:#444;margin:6px 0'>{it.get('summary', '')}</p>"
            "</div>"
        )
    return "".join(parts)


def push_high_value(items, cfg):
    """推送高价值情报，返回推送条数。"""
    push = cfg.get("push", {})
    if not push.get("enabled") or not items:
        return 0
    sent = 0
    title = f"情报预警 · {len(items)} 条高价值信息"
    if push.get("feishu_webhook"):
        send_feishu(push["feishu_webhook"], title, item_lines(items, with_reason=True),
                    footer="来自个人情报站 · 自动推送")
        sent += len(items)
    email_cfg = push.get("email", {})
    if email_cfg.get("enabled"):
        send_email(email_cfg, title, _email_html(title, items))
        sent += len(items)
    return sent


def push_digest(day, markdown, items, cfg):
    push = cfg.get("push", {})
    if not push.get("enabled"):
        raise PushError("推送未开启")
    blocks = item_lines(items[:8])
    if not blocks:
        blocks = [markdown[:1500]]
    if push.get("feishu_webhook"):
        send_feishu(push["feishu_webhook"], f"每日情报简报 · {day}", blocks,
                    footer="来自个人情报站 · 每日简报")
    email_cfg = push.get("email", {})
    if email_cfg.get("enabled"):
        send_email(email_cfg, f"每日情报简报 · {day}", _email_html(f"每日情报简报 {day}", items[:12]))
    return True
