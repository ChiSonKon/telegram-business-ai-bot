"""控制台页面渲染:返回 (HTML 文本, 键盘)。原则:待办优先、一屏一件事、每个数字都能点进去处理。"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from .. import branding
from ..global_settings import get_admin_group_id, get_global_setting
from ..ui import btn, kb
from . import queries as q
from .actions import RELEASE_REASONS
from . import permissions

DOT = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "P3": "⚪"}
STATE_ORDER_ZH = {"payment_review": "待核对", "paid": "已收款待交付", "fulfilling": "交付中", "awaiting_payment": "等待付款",
                  "delivered": "已交付", "quoted": "已报价", "cancelled": "已取消", "superseded": "已作废"}
BACK = [btn("返回控制台", icon="back", callback_data="adm:home")]


def _esc(x) -> str:
    return html.escape(str(x or ""))


def _bar(values: list[float], width: int = 7) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    top = max(values) or 1
    return "".join(blocks[min(7, int(v / top * 7))] if v else "▁" for v in values[:width])


def _delta(now: float, before: float) -> str:
    if before == 0:
        return "" if now == 0 else " (新增)"
    d = (now - before) / before * 100
    return f" ({'↑' if d > 0 else '↓'}{abs(d):.0f}%)" if abs(d) >= 1 else ""


def home() -> tuple[str, object]:
    d = q.dashboard()
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    queue, orders, t, y, h = d["queue"], d["orders"], d["today"], d["yesterday"], d["health"]

    todo = []
    if d["p0"]:
        todo.append(f"🔴 <b>{d['p0']}</b> 个紧急（付款/争议）")
    if d.get("pending", {}).get("quote"):
        todo.append(f"💬 <b>{d['pending']['quote']}</b> 位客户待报价")
    if d.get("pending", {}).get("impersonation"):
        todo.append(f"⚠️ <b>{d['pending']['impersonation']}</b> 条冒充报告（24 小时）")
    others = len(queue) - d["p0"]
    if others:
        todo.append(f"🟠 <b>{others}</b> 位客户等待跟进（最久 {q.fmt_wait(d['oldest'])}）")
    if orders["review"]:
        todo.append(f"🧾 <b>{len(orders['review'])}</b> 笔订单待核对")
    if orders["todo"]:
        todo.append(f"📦 <b>{len(orders['todo'])}</b> 笔已收款待交付")
    if d["takeovers"]:
        todo.append(f"👤 接管中 {len(d['takeovers'])} 位（最久 {q.fmt_wait(d['takeovers'][0]['since_min'])}）")
    if h["failing"]:
        todo.append(f"📤 <b>{h['failing']}</b> 条通知多次发送失败")
    todo_text = "\n".join(todo) if todo else "✅ 没有待处理事项"

    if h["priced"]:
        spend = f"${t['cost']:.2f} / ${h['budget']:.2f}（{t['cost'] / h['budget'] * 100:.0f}%）" if h["budget"] else f"${t['cost']:.2f}"
    else:
        spend = f"未配置单价（按条数兜底 {t['replies']}/{3000}）"
    ai_state = {"closed": "正常", "open": "⚠️ 已熔断，暂用留言兜底", "half_open": "恢复探测中"}[h["breaker"]]
    if h["limit_hit"]:
        ai_state = "⛔ 今日预算/上限已用完，AI 已暂停"
    alert_text = "\n".join(f"• {_esc(x)}" for x in d.get("alerts", []))

    text = (
        f"[[gear]] <b>白猫控制台</b>  <code>{now}</code>\n\n"
        f"<b>需要你处理</b>\n{todo_text}\n\n"
        f"<b>今天</b>\n"
        f"{q.funnel_line()}\n"
        f"转同事 {t['handoffs']} · AI 回复 {t['replies']}{_delta(t['replies'], y['replies'])}\n"
        f"AI 花费 {spend}\n\n"
        f"<b>系统</b>\n"
        f"AI：{ai_state}\n"
        f"收款：Stars 可用 · TON {'已配置' if h['ton'] else '未配置'} · 商品 {h['catalog']} 个\n"
        f"通知队列：{h['backlog']} 待发" + (f"\n\n<b>告警</b>\n{alert_text}" if alert_text else "")
        + "\n\n" + branding.footer()
    )
    badge = lambda n: f" ({n})" if n else ""
    rows = [
        [btn("待跟进" + badge(len(queue)), icon="bell", style="danger" if d["p0"] else "primary", callback_data="adm:q:all:0")],
        [btn("订单" + badge(len(orders["review"]) + len(orders["todo"])), icon="payment", style="primary", callback_data="adm:ord")],
        [btn("找客户", icon="user", callback_data="adm:find"), btn("数据", icon="chart", callback_data="adm:today")],
        [btn("设置", icon="gear", callback_data="adm:set"), btn("操作记录", icon="note", callback_data="adm:log")],
        [btn("刷新", icon="refresh", callback_data="adm:home"), btn("更多工具", icon="folder", callback_data="adm:more")],
    ]
    return text, kb(rows)


def queue(items: list[dict], flt: str, page: int) -> tuple[str, object]:
    per = 6
    pages = max(1, (len(items) + per - 1) // per)
    page = min(max(page, 0), pages - 1)
    chunk = items[page * per:(page + 1) * per]
    labels = {"urgent": "仅紧急", "quote": "待报价", "mine": "我接管的"}
    head = f"[[bell]] <b>待跟进</b>  共 {len(items)} 位" + (f"（{labels[flt]}）" if flt in labels else "")
    body = "\n".join(
        f"{DOT[i['prio']]} {_esc(i['name'])} · {i['reason'] or '待处理'} · 已等 {q.fmt_wait(i['wait_min'])}" for i in chunk
    ) or "\n没有等待跟进的客户 🎉"
    rows = [[btn(f"{DOT[i['prio']]} {i['name'][:12]} · {q.fmt_wait(i['wait_min'])}", callback_data=f"adm:case:{i['uid']}")] for i in chunk]
    nav = []
    if page > 0:
        nav.append(btn("上一页", icon="back", callback_data=f"adm:q:{flt}:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("下一页", icon="next", callback_data=f"adm:q:{flt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("全部", callback_data="adm:q:all:0"), btn("紧急", callback_data="adm:q:urgent:0"), btn("待报价", callback_data="adm:q:quote:0"), btn("我接管", callback_data="adm:q:mine:0")])
    rows.append([btn("刷新", icon="refresh", callback_data=f"adm:q:{flt}:{page}")])
    rows.append(BACK)
    return f"{head}\n\n{body}", kb(rows)


def case(v: dict | None) -> tuple[str, object]:
    if not v:
        return "没有找到这位客户。", kb([BACK])
    uid = v["uid"]
    who = f"<a href=\"tg://user?id={uid}\">{_esc(v['name'])}</a> {_esc(v['username'])}  <code>{uid}</code>"
    lines = [f"[[user]] <b>客户</b> {who}",
             f"状态：<b>{q.STATE_ZH.get(v['state'], v['state'])}</b>" + (f" · 已等 {q.fmt_wait(v['wait_min'])}" if v["state"] == "HUMAN_PENDING" else "")
             + (f" · {DOT[v['prio']]} {v['prio']}" if v["state"] in ("HUMAN_PENDING", "HUMAN_ACTIVE") else ""),
             f"来意：{q.ROLE_ZH.get(v['role'], '未选择')}" + (f" · 业务线 {_esc(v['line'])}" if v["line"] else "")]
    if v["reason"]:
        lines.append(f"转交原因：{_esc(v['reason'])}")
    brief = v.get("brief") or {}
    if brief:
        def bv(key, label):
            val = brief.get(key)
            return f"{label}：{_esc(', '.join(map(str, val)) if isinstance(val, list) else val)}" if val not in (None, "", []) else None
        vals = [bv(k, label) for k, label in (("source", "来源"), ("line", "业务线"), ("budget", "预算"), ("signals", "信号"), ("pending", "待回答"))]
        lines += ["", "<b>简报</b>"] + [x for x in vals if x]
    if v["summary"]:
        lines.append(f"预检摘要：{_esc(v['summary'][:500])}")
    elif v["first"]:
        lines.append(f"客户原话：{_esc(v['first'][:200])}")
    if v.get("messages"):
        lines += ["", "<b>最近对话</b>"]
        lines += [f"{('客户' if m['direction'] == 'in' else 'AI/同事')}：{_esc(m['text'])}" for m in v["messages"]]
    if v["quota_limit"]:
        lines.append(f"AI 额度（24 小时）：{v['quota_used']}/{v['quota_limit']}")
    for o in v["orders"]:
        lines.append(f"🧾 订单 <code>{o['id']}</code> {_esc(o['title'])} · {STATE_ORDER_ZH.get(o['state'], o['state'])}")
    if v["state"] == "HUMAN_PENDING" and v["reason_code"] in ("quota_exhausted",):
        lines.append("\n💡 额度用完导致的转交：可直接「增加 6 轮并恢复 AI」。")

    rows = []
    if v["state"] not in ("HUMAN_ACTIVE", "RESOLVED"):
        rows.append([btn("接管对话", icon="bell_off", style="danger", callback_data=f"adm:take:{uid}")])
    row2 = [btn("标记已解决", icon="check", style="success", callback_data=f"adm:done:{uid}")] if v["state"] != "RESOLVED" else []
    if v["state"] in ("HUMAN_PENDING", "HUMAN_ACTIVE") and v["role"] in ("prospect", "support"):
        row2.append(btn("恢复 AI", icon="robot", callback_data=f"adm:rel:{uid}"))
    if row2:
        rows.append(row2)
    if v["quota_limit"]:
        rows.append([btn("增加 6 轮 AI", icon="plus", callback_data=f"adm:grant:{uid}:0"),
                     btn("增加并恢复", icon="refresh", callback_data=f"adm:grant:{uid}:1")] if v["state"] in ("HUMAN_PENDING", "HUMAN_ACTIVE") and v["role"] in ("prospect", "support")
                    else [btn("增加 6 轮 AI", icon="plus", callback_data=f"adm:grant:{uid}:0")])
    rows.append([btn("发送报价", icon="payment", callback_data=f"adm:qt:{uid}")])
    link = topic_link(v["thread"])
    if link:
        rows.append([btn("打开对话话题", icon="chat", url=link)])
    rows.append([btn("返回待跟进", icon="back", callback_data="adm:q:all:0"), btn("控制台", icon="gear", callback_data="adm:home")])
    return "\n".join(lines), kb(rows)


def topic_link(thread_id: int | None) -> str | None:
    gid = str(get_admin_group_id())
    if not thread_id or not gid.startswith("-100"):
        return None
    return f"https://t.me/c/{gid[4:]}/{thread_id}"


def release_choices(uid: int) -> tuple[str, object]:
    rows = [[btn(label, callback_data=f"adm:relok:{uid}:{code}")] for code, label in RELEASE_REASONS.items()]
    rows.append([btn("取消", icon="back", callback_data=f"adm:case:{uid}")])
    return "[[robot]] <b>恢复 AI</b>\n请选择恢复原因（会记入操作记录）：", kb(rows)


def quote_choices(uid: int, catalog: dict) -> tuple[str, object]:
    if not catalog:
        return "还没有商品。请先到「设置 → 商品与支付」添加。", kb([[btn("去设置", icon="gear", callback_data="adm:set:shop")], BACK])
    rows = [[btn(f"{(v.get('title_zh') or k)[:18]} · " + _price_text(v), callback_data=f"adm:qts:{uid}:{k}")] for k, v in catalog.items()]
    rows.append([btn("取消", icon="back", callback_data=f"adm:case:{uid}")])
    return "[[payment]] <b>发送报价</b>\n选择商品，客户会收到报价卡（USDT 商品先选付款网络，按配置的有效期计时）：", kb(rows)


def orders(ov: dict) -> tuple[str, object]:
    def block(title, items, empty=""):
        if not items:
            return f"<b>{title}</b>  无\n" if empty == "" else ""
        return f"<b>{title}</b>\n" + "\n".join(f"• <code>{o['id']}</code> {_esc(o['title'])} · {q.fmt_wait(o['wait_min'])}" for o in items) + "\n"
    text = "[[payment]] <b>订单</b>\n\n" + block("需要我处理 · 待核对", ov["review"]) + block("需要我处理 · 待交付", ov["todo"]) + block("等待客户付款", ov["waiting"], "x") + block("最近完成", ov["recent"], "x")
    rows = [[btn(f"{STATE_ORDER_ZH.get(o['state'], o['state'])} · {o['id']}", style="danger" if o["state"] == "payment_review" else "primary",
                 callback_data=f"adm:ordc:{o['id']}")] for o in ov["review"] + ov["todo"] + ov["waiting"] + ov["recent"]]
    rows.append([btn("刷新", icon="refresh", callback_data="adm:ord")])
    rows.append(BACK)
    return text, kb(rows)


def roles_page(role_map: dict[int, str], can_edit: bool = False, names: dict | None = None, pending: dict | None = None) -> tuple[str, object]:
    names = names or {}

    def label(uid):
        n = names.get(uid) or {}
        who = (f"@{n['username']}" if n.get("username") else n.get("name") or "") if n else ""
        return f"{_esc(who)} <code>{uid}</code>".strip()
    body = "\n".join(f"• {label(uid)} {permissions.ROLE_ZH.get(role, role)}" for uid, role in sorted(role_map.items())) or "暂无角色配置"
    if pending:
        body += "\n\n<b>等待对方首次互动后生效</b>\n" + "\n".join(f"• @{_esc(u)} {permissions.ROLE_ZH.get(v['role'], v['role'])}" for u, v in pending.items())
    rows = []
    if can_edit:
        rows.append([btn("添加管理员", icon="plus", style="primary", callback_data="adm:roleadd")])
        for uid, role in sorted(role_map.items()):
            n = names.get(uid) or {}
            rows.append([btn(f"{(('@' + n['username']) if n.get('username') else n.get('name') or uid)} · {permissions.ROLE_ZH.get(role, role)}"[:60],
                             callback_data=f"adm:role:{uid}")])
    else:
        body += "\n\n(只有业主可以添加或修改管理员)"
    rows.append([btn("返回更多工具", icon="back", callback_data="adm:more")])
    return f"[[crown]] <b>管理员与角色</b>\n\n{body}", kb(rows)


def role_detail(uid: int, name: str, role: str, locked: bool) -> tuple[str, object]:
    rows = []
    if not locked:
        for r in permissions.ROLES:
            mark = "✓ " if r == role else ""
            rows.append([btn(f"{mark}{permissions.ROLE_ZH[r]}", style="success" if r == role else None, callback_data=f"adm:rolesel:{uid}:{r}")])
        rows.append([btn("移除管理员", icon="alert", style="danger", callback_data=f"adm:roledel:{uid}")])
    rows.append([btn("返回", icon="back", callback_data="adm:roles")])
    note = "\n.env 中配置的业主，只能在服务器上修改。" if locked else "\n点选一个角色即可更换。"
    return f"[[crown]] <b>{_esc(name)}</b> <code>{uid}</code>\n当前角色：{permissions.ROLE_ZH.get(role, role)}{note}", kb(rows)


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def data_page(days: int = 1) -> tuple[str, object]:
    """数据页:漏斗 + 卡点 + 来源 + 转交原因。days ∈ {1, 7, 30}。"""
    cur = q.funnel_summary(days)
    prev = q.funnel_summary(days, end_offset_days=days)
    label = {1: "今日", 7: "近 7 天", 30: "近 30 天"}.get(days, f"近 {days} 天")
    lines = [f"[[chart]] <b>数据 · {label}</b>", "", "<b>漏斗</b>"]
    for s, p in zip(cur["funnel"]["stages"], prev["funnel"]["stages"]):
        lines.append(f"{s['name']}  <b>{s['count']}</b>（{_pct(s['rate'])}）{_delta(s['count'], p['count'])}")
    drops = [d for d in cur["dropoffs"] if d[2] >= 3][:3]
    lines += ["", "<b>卡点（出示 → 作答）</b>"] + (
        [f"{'🔴' if d[4] > 0.25 else '•'} {_esc(d[0])} · {_esc(d[1])}：{d[2]} → {d[3]}，流失 {_pct(d[4])}" for d in drops] or ["样本还不够（每题至少 3 次出示）"])
    src = cur["sources"][:5]
    lines += ["", "<b>来源</b>"] + ([f"• {_esc(s['source'])}：进线 {s['entries']} · 转化 {s['converted']}" for s in src] or ["暂无"])
    h = cur["handoffs"]
    reasons = "、".join(f"{_esc(k)} {v}" for k, v in sorted(h["reasons"].items(), key=lambda kv: -kv[1])[:5]) or "无"
    lines += ["", "<b>转交</b>", f"原因：{reasons}",
              f"同事首次回复中位等待：{('%d 分钟' % h['median_wait_min']) if h['median_wait_min'] is not None else '暂无数据'}（样本 {h['replied']}）"]
    rows = [[btn(("✓ " if d == days else "") + n, callback_data=f"adm:data:{d}") for d, n in ((1, "今日"), (7, "7 天"), (30, "30 天"))],
            [btn("刷新", icon="refresh", callback_data=f"adm:data:{days}"), btn("成本与明细", icon="chart", callback_data="adm:today")], BACK]
    return "\n".join(lines), kb(rows)


def today(series: list[dict]) -> tuple[str, object]:
    t = series[-1]
    text = (
        "[[chart]] <b>数据</b>\n\n"
        f"<b>今天</b>\n新客户 {t['new']} · 预检完成 {t['prechecked']} · 转同事 {t['handoffs']}\n"
        f"AI 回复 {t['replies']} · 成交 {t['paid']} · 花费 ${t['cost']:.2f}\n拦截/限流次数 {t['strikes']}\n\n"
        f"<b>近 7 天</b>\n"
        f"新客户  <code>{_bar([x['new'] for x in series])}</code> {sum(x['new'] for x in series)}\n"
        f"AI 回复 <code>{_bar([x['replies'] for x in series])}</code> {sum(x['replies'] for x in series)}\n"
        f"成交    <code>{_bar([x['paid'] for x in series])}</code> {sum(x['paid'] for x in series)}\n"
        f"花费    <code>{_bar([x['cost'] for x in series])}</code> ${sum(x['cost'] for x in series):.2f}"
    )
    flags = q.abuse_flags()
    if flags:
        text += "\n\n<b>近 1 小时异常频繁的客户</b>\n" + "\n".join(f"• <code>{f['uid']}</code> {f['strikes']} 次" for f in flags)
    return text, kb([[btn("刷新", icon="refresh", callback_data="adm:today"), btn("漏斗与卡点", icon="chart", callback_data="adm:data:7")], BACK])


def log(rows: list[dict]) -> tuple[str, object]:
    body = "\n".join(f"• {q.fmt_wait(r['min'])}前 · <code>{r['admin']}</code> · {_esc(r['action'])} {_esc(r['target'])}"
                     + (f" — {_esc(r['detail'])}" if r["detail"] else "") for r in rows) or "还没有操作记录"
    return f"[[note]] <b>操作记录</b>（最近 {len(rows)} 条）\n\n{body}", kb([BACK])


def settings_hub() -> tuple[str, object]:
    return ("[[gear]] <b>设置</b>\n每一项都会显示当前值，修改前先预览，确认后才生效，并记入操作记录。",
            kb([[btn("设置中心(文案/流程/规则/发布与回滚)", icon="gear", style="primary", callback_data="cfg:home")],
                [btn("AI 成本与预算", icon="chart", callback_data="adm:set:cost")],
                [btn("商品与支付", icon="payment", callback_data="adm:set:shop")],
                [btn("业务线", icon="package", callback_data="adm:set:lines")],
                [btn("验证与限流", icon="lock", callback_data="adm:set:guard")],
                [btn("提示词", icon="note", callback_data="adm:set:prompt")],
                BACK]))


def more_tools() -> tuple[str, object]:
    return ("[[folder]] <b>更多工具</b>", kb([
        [btn("广播中心", icon="megaphone", callback_data="panel_broadcast"), btn("/start 设置", icon="wave", callback_data="panel_start_settings")],
        [btn("AI 模型配置", icon="robot", callback_data="panel_ai_settings"), btn("提示词编辑", icon="note", callback_data="promptcfg_panel")],
        [btn("全局参数", icon="gear", callback_data="globalcfg_panel"), btn("管理群组", icon="folder", callback_data="panel_group")],
        [btn("管理员与角色", icon="crown", callback_data="adm:roles"), btn("所有用户", icon="users", callback_data="panel_users")],
        BACK]))


# ---------- 设置页 ----------
def set_cost() -> tuple[str, object]:
    from ..frontdesk import budget
    p = budget.prices()
    body = (f"输入价 ${p[0]}/百万 token · 输出价 ${p[1]}/百万 token" if p else "⚠️ 尚未配置单价（目前只有每日回复条数兜底）") + \
        f"\n每日预算 ${budget.daily_budget():.2f}" + f"\n条数兜底 {int(get_global_setting('global_daily_reply_cap') or 3000)} 条/天"
    return (f"[[chart]] <b>AI 成本与预算</b>\n\n{body}\n\n预算用完后 AI 自动暂停，客户的留言会转给你们，不会冷场。",
            kb([[btn("修改单价与预算", icon="gear", style="primary", callback_data="adm:wiz:cost")], [btn("返回设置", icon="back", callback_data="adm:set")]]))


def _price_text(v: dict) -> str:
    return " / ".join(x for x in (f"{v['usdt']} USDT" if v.get("usdt") else "", f"{v['stars']}⭐" if v.get("stars") else "",
                                  f"{v['ton']} TON" if v.get("ton") else "") if x)


def set_shop(catalog: dict, ton_cfg: dict) -> tuple[str, object]:
    items = "\n".join(f"• <code>{_esc(k)}</code> {_esc(v.get('title_zh'))} · " + _price_text(v)
                      + (f" · 类目 <code>{_esc(v['category'])}</code>" if v.get("category") else "") for k, v in catalog.items()) or "还没有商品"
    addr = ton_cfg.get("address")
    shown = f"{addr[:6]}…{addr[-6:]}" if addr else "未配置"
    rows = [[btn("添加/修改商品", icon="plus", style="primary", callback_data="adm:wiz:sku")]]
    rows += [[btn(f"删除 {k}", icon="alert", callback_data=f"adm:skudel:{k}")] for k in list(catalog)[:6]]
    rows += [[btn("设置 TON 收款地址", icon="payment", callback_data="adm:wiz:ton")], [btn("返回设置", icon="back", callback_data="adm:set")]]
    return f"[[payment]] <b>商品与支付</b>\n\n{items}\n\nTON 收款地址：<code>{shown}</code>\nStars：无需配置，开启即用", kb(rows)


def set_lines(lines: list[dict]) -> tuple[str, object]:
    items = "\n".join(f"• <code>{_esc(x['id'])}</code> {_esc(x.get('name_zh'))} · 关键词 {len(x.get('keywords', []))} 个" for x in lines)
    rows = [[btn("添加业务线", icon="plus", style="primary", callback_data="adm:wiz:line")]]
    rows += [[btn(f"删除 {x['id']}", icon="alert", callback_data=f"adm:linedel:{x['id']}")] for x in lines[:6]]
    rows.append([btn("返回设置", icon="back", callback_data="adm:set")])
    return f"[[package]] <b>业务线</b>\n客户预检时的业务选择、追问项来自这里。\n\n{items}", kb(rows)


def set_guard() -> tuple[str, object]:
    from ..frontdesk import abuse
    off = bool(get_global_setting("disable_captcha"))
    text = (f"[[lock]] <b>验证与限流</b>\n\n人机验证：{'⛔ 已关闭' if off else '✅ 开启（仅频繁超额调用 AI 时出现）'}\n"
            f"触发条件：{abuse.STRIKE_WINDOW_MIN} 分钟内被拦截/限流 ≥ {abuse.STRIKES_TO_CHALLENGE} 次，或 {abuse.BURST_WINDOW_MIN} 分钟内发起 ≥ {abuse.BURST_TURNS} 次 AI 回合；\n"
            f"通过一次验证后计数清零。预检阶段不会出现验证。\n\n每分钟限流：{get_global_setting('rate_limit_per_minute')} 条\n发言间隔：{get_global_setting('message_interval')} 秒")
    return text, kb([[btn("关闭验证" if not off else "开启验证", icon="lock", callback_data="adm:guardtoggle")],
                     [btn("调整限流参数", icon="gear", callback_data="globalcfg_panel")], [btn("返回设置", icon="back", callback_data="adm:set")]])


def set_prompt(content: str) -> tuple[str, object]:
    return (f"[[note]] <b>运营提示词</b>（{len(content)} 字）\n\n<pre>{_esc(content[:700])}{'…' if len(content) > 700 else ''}</pre>\n"
            "这是附加在系统安全前言之后的业务说明；身份披露、不报价不给地址等规则始终优先。",
            kb([[btn("在线编辑", icon="note", callback_data="promptcfg_panel")],
                [btn("恢复为仓库默认", icon="refresh", style="danger", callback_data="adm:promptreset")], [btn("返回设置", icon="back", callback_data="adm:set")]]))
