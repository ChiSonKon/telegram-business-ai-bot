"""管理员角色与权限(04 §4):角色存于配置 access.roles;.env 的 ADMIN_USER_IDS 在迁移时全部导入为 owner。

所有控制台回调/命令统一调用 can()/require()。拒绝时给出"你的角色(客服)没有这个权限"。
"""
from __future__ import annotations

ROLES = ("owner", "manager", "agent", "finance", "viewer")
ROLE_ZH = {"owner": "业主", "manager": "主管", "agent": "客服", "finance": "财务", "viewer": "只读"}

# 能力 → 允许的角色
CAPS: dict[str, tuple[str, ...]] = {
    "view": ROLES,
    "case_ops": ("owner", "manager", "agent"),                 # 接管 / 结案 / 恢复 AI / 加额度
    "quote_catalog": ("owner", "manager", "agent"),            # 目录商品报价
    "quote_custom": ("owner", "manager", "agent"),             # 自定义金额报价(agent 受上限限制,见 quote_limit)
    "payment_ops": ("owner", "finance"),                       # 确认收款 / 取消订单 / 登记退款
    "deliver": ("owner", "manager", "agent", "finance"),       # 开始交付 / 标记已交付
    "config_edit": ("owner", "manager"),
    "config_publish": ("owner", "manager"),
    "config_high": ("owner",),                                 # 收款、白名单、角色
    "config_rollback": ("owner", "manager"),                   # manager 仅 normal 项
    "export_orders": ("owner", "finance"),
    "broadcast": ("owner", "manager"),
    "audit_view": ROLES,
}
AGENT_CUSTOM_QUOTE_MAX_U = 1000


class PermissionDenied(Exception):
    def __init__(self, role: str | None, cap: str):
        super().__init__(f"{role} lacks {cap}")
        self.role, self.cap = role, cap


def roles() -> dict[int, str]:
    """生效的角色表(配置 access.roles)。解析失败返回空表,由 env 管理员兜底。"""
    from ..config import cfg
    raw = cfg.get("access.roles", default={}) or {}
    out = {}
    for k, v in raw.items():
        try:
            if v in ROLES:
                out[int(k)] = v
        except (TypeError, ValueError):
            continue
    return out


def role_of(user_id: int) -> str | None:
    r = roles().get(user_id)
    if r:
        return r
    from .. import admin_user_ids
    return "owner" if user_id in admin_user_ids else None          # 迁移前 / 通过 /addadmin 新增且尚未分配角色 → 保持原有权限


def is_admin(user_id: int) -> bool:
    return role_of(user_id) is not None


def can(user_id: int, cap: str) -> bool:
    role = role_of(user_id)
    return bool(role and role in CAPS.get(cap, ()))


def require(user_id: int, cap: str) -> None:
    if not can(user_id, cap):
        raise PermissionDenied(role_of(user_id), cap)


def deny_text(user_id: int) -> str:
    role = role_of(user_id)
    return f"你的角色({ROLE_ZH.get(role, '未授权')})没有这个权限"


def owners() -> list[int]:
    from .. import admin_user_ids
    ids = [u for u, r in roles().items() if r == "owner"]
    for u in admin_user_ids:
        if u not in roles() and u not in ids:
            ids.append(u)
    return ids


def quote_limit(user_id: int) -> float | None:
    """自定义金额报价上限(U);None = 不限。"""
    from ..config import cfg
    if role_of(user_id) == "agent":
        return float(cfg.get("quote.agent_custom_max_u", default=AGENT_CUSTOM_QUOTE_MAX_U))
    return None


def can_edit_key(user_id: int, key_edit_role: str, risk: str) -> bool:
    role = role_of(user_id)
    if not role:
        return False
    if risk == "high" or key_edit_role == "owner":
        return role == "owner"
    return role in ("owner", "manager")


def can_publish_key(user_id: int, key_publish_role: str, risk: str) -> bool:
    return can_edit_key(user_id, key_publish_role, risk)


# ---------- 管理员管理:直接发用户名即可(业主专用,立即生效) ----------
import json as _json
import re as _re

ROLE_WORDS = {"业主": "owner", "owner": "owner", "主管": "manager", "经理": "manager", "manager": "manager",
              "客服": "agent", "agent": "agent", "财务": "finance", "finance": "finance", "只读": "viewer", "viewer": "viewer"}
PENDING_KEY = "admin.pending"
_USERNAME_RE = _re.compile(r"^@?([A-Za-z0-9_]{4,32})$")


def _env_ids() -> set[int]:
    import os
    out = set()
    for x in (os.getenv("ADMIN_USER_IDS") or "").split(","):
        try:
            out.add(int(x.strip()))
        except ValueError:
            pass
    return out


def parse_admin_line(line: str) -> tuple[str | int, str]:
    """'@alice manager' / '123456789 财务' / 't.me/alice' → (用户名或数字ID, 角色);角色缺省为客服。"""
    toks = line.replace(",", " ").replace("，", " ").split()
    if not toks:
        raise ValueError("请发送用户名，例如：@alice 或 @alice 主管")
    who, role = toks[0], "agent"
    for t in toks[1:]:
        if t.lower() in ROLE_WORDS:
            role = ROLE_WORDS[t.lower()]
        else:
            raise ValueError(f"看不懂「{t}」。角色可选：业主 / 主管 / 客服 / 财务 / 只读")
    who = who.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "")
    if who.lstrip("-").isdigit():
        return int(who), role
    m = _USERNAME_RE.match(who)
    if not m:
        raise ValueError(f"「{toks[0]}」不是有效的用户名（4–32 位字母数字下划线），也可以直接发数字 ID")
    return m.group(1), role


def resolve_username(username: str) -> int | None:
    """从已和机器人互动过的用户里按用户名找 ID(Telegram 不允许机器人凭用户名查任意用户)。"""
    from sqlalchemy import func
    from db.database import SessionMaker
    from db.model import User
    with SessionMaker() as db:
        u = db.query(User).filter(func.lower(User.username) == username.lower()).first()
        return u.user_id if u else None


def _pending() -> dict:
    from ..commerce import service
    return service._get(PENDING_KEY, {}) or {}


def _save_pending(d: dict) -> None:
    from ..commerce import service
    service._set(PENDING_KEY, d)


def _write_roles(actor_id: int, new_map: dict[int, str], note: str) -> None:
    from ..config import store
    store._write_revision(actor_id, {"access.roles": {str(k): v for k, v in sorted(new_map.items())}}, note, effective_at=None)
    sync_admin_ids()


def _effective_map() -> dict[int, str]:
    m = dict(roles())
    for uid in _env_ids():
        m.setdefault(uid, "owner")
    return m


def sync_admin_ids() -> None:
    """让 admin_user_ids(旧代码里大量直接判断)与角色表保持一致;.env 里的业主永不被移除。"""
    from .. import admin_user_ids
    want = set(roles()) | _env_ids()
    for uid in want:
        if uid not in admin_user_ids:
            admin_user_ids.append(uid)
    for uid in list(admin_user_ids):
        if uid not in want:
            admin_user_ids.remove(uid)


def set_role(actor_id: int, user_id: int, role: str, *, note: str = "") -> None:
    """业主设置/修改某人的角色,立即生效并写入配置历史与审计。"""
    require(actor_id, "config_high")
    if role not in ROLES:
        raise ValueError("未知角色")
    cur = _effective_map()
    if cur.get(user_id) == "owner" and role != "owner" and sum(1 for r in cur.values() if r == "owner") <= 1:
        raise ValueError("至少要保留一位业主")
    if user_id in _env_ids() and role != "owner":
        raise ValueError(".env 里配置的管理员固定为业主，不能降级（需在服务器上修改）")
    cur[user_id] = role
    _write_roles(actor_id, cur, note or f"设置 {user_id} 为 {ROLE_ZH[role]}")


def remove_role(actor_id: int, user_id: int) -> None:
    require(actor_id, "config_high")
    if user_id in _env_ids():
        raise ValueError(".env 里配置的管理员不能在这里移除（需在服务器上修改）")
    cur = _effective_map()
    if cur.get(user_id) == "owner" and sum(1 for r in cur.values() if r == "owner") <= 1:
        raise ValueError("至少要保留一位业主")
    cur.pop(user_id, None)
    _write_roles(actor_id, cur, f"移除管理员 {user_id}")


def add_admin(actor_id: int, who: str | int, role: str) -> tuple[str, int | None]:
    """按用户名或数字 ID 添加。返回 (状态, user_id):'added' 已生效 / 'pending' 对方还没和机器人互动过,待其 /start 后自动生效。"""
    if isinstance(who, int):
        set_role(actor_id, who, role)
        return "added", who
    uid = resolve_username(who)
    if uid:
        set_role(actor_id, uid, role)
        return "added", uid
    require(actor_id, "config_high")
    pend = _pending()
    pend[who.lower()] = {"role": role, "by": actor_id}
    _save_pending(pend)
    return "pending", None


def claim_pending(tg_user) -> str | None:
    """被预先添加的用户名第一次和机器人互动时自动成为管理员。返回授予的角色或 None。"""
    username = getattr(tg_user, "username", None)
    if not username:
        return None
    pend = _pending()
    entry = pend.get(username.lower())
    if not entry:
        return None
    pend.pop(username.lower(), None)
    _save_pending(pend)
    cur = _effective_map()
    cur[tg_user.id] = entry["role"]
    _write_roles(int(entry.get("by") or 0), cur, f"@{username} 首次互动，自动成为{ROLE_ZH[entry['role']]}")
    return entry["role"]
