"""配置存储:版本化修订(revision)+ 每位管理员一份草稿 + 发布/回滚 + 高危延迟生效 + 导入导出 + 发布后守护。

- revision.snapshot_json = 该次发布后"所有被覆盖的键"的完整快照(意图状态);changed_keys = 本次改动的键。
- 读取:按 revision 升序叠加,高危键若 effective_at 还没到,则仍用上一版的值(延迟生效)。
- 读取失败(表损坏等)整体回退到默认值并记录错误,绝不让机器人因配置存储故障而沉默。
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from db.database import SessionMaker
from db.model import AppSetting, ConfigDraft, ConfigRevision

from . import _MISSING
from . import lint as L
from . import registry as R

logger = logging.getLogger(__name__)

_cache: dict | None = None
_next_effective: float | None = None
_errors: deque = deque(maxlen=200)
GUARD_WINDOW_S = 15 * 60
GUARD_MAX_ERRORS = 3


class PublishBlocked(Exception):
    def __init__(self, issues: list):
        super().__init__("; ".join(str(i) for i in issues))
        self.issues = issues


class NeedsConfirmation(Exception):
    """存在 warn 级问题 / 高危项需要确认短语。"""
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind, self.detail = kind, detail


class LockedKey(Exception):
    pass


@dataclass
class DraftResult:
    key: str
    ok: bool
    issues: list = field(default_factory=list)


def _now() -> float:
    from whitecat_core.runtime import now
    return now().timestamp()


def invalidate() -> None:
    global _cache, _next_effective
    _cache, _next_effective = None, None


# ---------- 读取 ----------
def _compute(db) -> tuple[dict, float | None]:
    ov: dict = {}
    nxt: float | None = None
    now = _now()
    for r in db.query(ConfigRevision).order_by(ConfigRevision.id.asc()).all():
        snap = json.loads(r.snapshot_json or "{}")
        for k in json.loads(r.changed_keys or "[]"):
            if r.effective_at is None or r.effective_at <= now:
                if k in snap:
                    ov[k] = snap[k]
                else:
                    ov.pop(k, None)                       # 快照里没有 = 恢复默认
            else:
                nxt = r.effective_at if nxt is None else min(nxt, r.effective_at)
    return ov, nxt


def get_active() -> dict:
    global _cache, _next_effective
    if _cache is None or (_next_effective is not None and _now() >= _next_effective):
        try:
            with SessionMaker() as db:
                _cache, _next_effective = _compute(db)
        except Exception as exc:
            logger.error("config store unavailable, falling back to defaults: %s", type(exc).__name__)
            _cache, _next_effective = {}, None
    return _cache


def override(key: str):
    return get_active().get(key, _MISSING)


def effective_value(key: str):
    v = override(key)
    return R.default_value(key) if v is _MISSING else v


def latest_revision_id() -> int:
    with SessionMaker() as db:
        r = db.query(ConfigRevision).order_by(ConfigRevision.id.desc()).first()
        return r.id if r else 0


def _latest_snapshot(db) -> dict:
    r = db.query(ConfigRevision).order_by(ConfigRevision.id.desc()).first()
    return json.loads(r.snapshot_json) if r else {}


# ---------- 草稿 ----------
def draft_value(admin_id: int, key: str):
    try:
        with SessionMaker() as db:
            d = db.query(ConfigDraft).filter_by(author_id=admin_id, key=key).first()
            return json.loads(d.value_json) if d else _MISSING
    except Exception:
        return _MISSING


def list_drafts(admin_id: int) -> dict:
    with SessionMaker() as db:
        return {d.key: json.loads(d.value_json) for d in db.query(ConfigDraft).filter_by(author_id=admin_id).all()}


def discard_draft(admin_id: int, key: str | None = None) -> int:
    with SessionMaker() as db:
        q = db.query(ConfigDraft).filter_by(author_id=admin_id)
        if key:
            q = q.filter_by(key=key)
        n = q.delete()
        db.commit()
        return n


def save_draft(admin_id: int, key: str, value) -> list[L.Issue]:
    """保存草稿并立即 lint。有 error 的草稿可以保存,但不能发布。"""
    from ..admin import permissions
    if key in R.LOCKED_KEYS:
        raise LockedKey(key)
    ck = R.get_key(key)
    if ck is None:
        raise KeyError(key)
    if not permissions.can_edit_key(admin_id, ck.edit_role, ck.risk):
        raise permissions.PermissionDenied(permissions.role_of(admin_id), "config_high" if ck.risk == "high" else "config_edit")
    issues = L.lint(key, value)
    with SessionMaker() as db:
        d = db.query(ConfigDraft).filter_by(author_id=admin_id, key=key).first()
        payload = json.dumps(value, ensure_ascii=False)
        lint_json = json.dumps([[i.level, i.msg] for i in issues], ensure_ascii=False)
        if d:
            d.value_json, d.lint_json = payload, lint_json
        else:
            db.add(ConfigDraft(author_id=admin_id, key=key, value_json=payload, lint_json=lint_json))
        db.commit()
    return issues


# ---------- 发布 ----------
def confirm_phrase_for(key: str, value) -> str:
    """高危项发布时 owner 需输入的确认短语:地址取尾 6 位,其余为"确认"。"""
    if isinstance(value, str) and len(value) >= 6:
        return value[-6:]
    if isinstance(value, list):
        for it in value:
            if isinstance(it, dict) and it.get("address"):
                return str(it["address"])[-6:]
    return "确认"


def _delay_minutes() -> int:
    v = override("config.high_risk_delay_minutes")
    return int(15 if v is _MISSING else v)


def publish(admin_id: int, keys: list[str] | None = None, note: str = "", *, confirm_warnings: bool = False,
            confirm_phrase: str | None = None, audit: bool = True) -> int:
    """发布该管理员的草稿(全部或指定键)。普通项立即生效;高危项延迟 high_risk_delay_minutes 分钟生效。返回最后写入的 revision id。"""
    from ..admin import permissions
    drafts = {k: v for k, v in list_drafts(admin_id).items() if keys is None or k in keys}
    if not drafts:
        raise ValueError("没有可发布的草稿")
    warnings, normal, high = [], {}, {}
    for k, v in drafts.items():
        ck = R.get_key(k)
        if ck is None or k in R.LOCKED_KEYS:
            raise LockedKey(k)
        if not permissions.can_publish_key(admin_id, ck.publish_role, ck.risk):
            raise permissions.PermissionDenied(permissions.role_of(admin_id), "config_high" if ck.risk == "high" else "config_publish")
        issues = L.lint(k, v)
        if L.errors(issues):
            raise PublishBlocked([i for i in issues if i.level == "error"])
        warnings += [f"{k}: {i.msg}" for i in issues if i.level == "warn"]
        (high if ck.risk == "high" else normal)[k] = v
    if warnings and not confirm_warnings:
        raise NeedsConfirmation("warn", "\n".join(warnings))
    if high:
        phrases = {confirm_phrase_for(k, v) for k, v in high.items()}
        if confirm_phrase is None or confirm_phrase.strip() not in phrases:
            raise NeedsConfirmation("phrase", "、".join(sorted(phrases)))
    last = 0
    if normal:
        last = _write_revision(admin_id, normal, note, effective_at=None, audit=audit)
    if high:
        delay = _delay_minutes()
        last = _write_revision(admin_id, high, note, effective_at=(_now() + delay * 60) if delay > 0 else None, audit=audit)
    with SessionMaker() as db:
        db.query(ConfigDraft).filter(ConfigDraft.author_id == admin_id, ConfigDraft.key.in_(list(drafts))).delete(synchronize_session=False)
        db.commit()
    return last


def _write_revision(admin_id: int, values: dict, note: str, *, effective_at: float | None, reverted_from: int | None = None,
                    full_snapshot: dict | None = None, audit: bool = True) -> int:
    with SessionMaker() as db:
        before = _latest_snapshot(db)
        snap = dict(full_snapshot) if full_snapshot is not None else {**before, **values}
        changed = [k for k in sorted(set(before) | set(snap)) if before.get(k, _MISSING) != snap.get(k, _MISSING)] if full_snapshot is not None else sorted(values)
        rev = ConfigRevision(author_id=admin_id, note=(note or "")[:256], snapshot_json=json.dumps(snap, ensure_ascii=False),
                             changed_keys=json.dumps(changed, ensure_ascii=False), effective_at=effective_at, reverted_from=reverted_from)
        db.add(rev)
        db.commit()
        rid = rev.id
    invalidate()
    if effective_at is None:
        for k in changed:
            _apply_sync(k, snap.get(k, _MISSING))
    _audit_and_notify(admin_id, rid, changed, before, snap, effective_at, reverted_from, audit)
    return rid


def _audit_and_notify(admin_id, rid, changed, before, after, effective_at, reverted_from, audit=True):
    try:
        from ..admin import actions
        from ..analytics.events import emit
        from ..outbox import enqueue_admin
        action = "回滚配置" if reverted_from else "发布配置"
        if audit:
            actions.audit(admin_id, action, f"v{rid}", ",".join(changed))
            with SessionMaker() as db:
                from db.model import AdminAudit
                a = db.query(AdminAudit).order_by(AdminAudit.id.desc()).first()
                if a:
                    a.before_json = json.dumps({k: before.get(k) for k in changed}, ensure_ascii=False)[:4000]
                    a.after_json = json.dumps({k: after.get(k) for k in changed}, ensure_ascii=False)[:4000]
                    db.commit()
        emit("config_rolled_back" if reverted_from else "config_published", admin_id, revision=rid)
        delay_note = ""
        if effective_at:
            mins = max(1, int((effective_at - _now()) // 60))
            delay_note = f"\n⚠️ 高危配置将在约 {mins} 分钟后生效;如非本人操作,请在设置中心「历史版本」立即撤销。"
        enqueue_admin(f"cfg:{rid}", f"[[gear]] <b>配置 v{rid} 已{'回滚' if reverted_from else '发布'}</b>\n改动:{', '.join(changed)[:300]}\n操作人:<code>{admin_id}</code>{delay_note}")
    except Exception as exc:
        logger.warning("config audit/notify failed: %s", type(exc).__name__)


def revoke(admin_id: int, revision_id: int) -> bool:
    """撤销尚未生效的高危修订(必须是最新一条)。owner 才能操作。"""
    from ..admin import permissions
    permissions.require(admin_id, "config_high")
    with SessionMaker() as db:
        r = db.get(ConfigRevision, revision_id)
        latest = db.query(ConfigRevision).order_by(ConfigRevision.id.desc()).first()
        if not r or not latest or latest.id != r.id or not r.effective_at or r.effective_at <= _now():
            return False
        prev = db.query(ConfigRevision).filter(ConfigRevision.id < r.id).order_by(ConfigRevision.id.desc()).first()
        r.snapshot_json = prev.snapshot_json if prev else "{}"
        r.changed_keys = "[]"
        r.effective_at = None
        r.note = (r.note + " [已撤销]")[:256]
        db.commit()
    invalidate()
    from ..admin import actions
    actions.audit(admin_id, "撤销配置", f"v{revision_id}")
    return True


def rollback(admin_id: int, revision_id: int, *, auto: bool = False) -> int:
    """回滚 = 生成一个新 revision(内容=旧快照),不删除历史。manager 只能回滚 normal 项。"""
    from ..admin import permissions
    with SessionMaker() as db:
        target = db.get(ConfigRevision, revision_id)
        if not target:
            raise KeyError(revision_id)
        snap = json.loads(target.snapshot_json or "{}")
        cur = _latest_snapshot(db)
    if not auto:
        permissions.require(admin_id, "config_rollback")
        diff = [k for k in set(snap) | set(cur) if snap.get(k, _MISSING) != cur.get(k, _MISSING)]
        if any((R.get_key(k) and R.get_key(k).risk == "high") for k in diff) and not permissions.can(admin_id, "config_high"):
            raise permissions.PermissionDenied(permissions.role_of(admin_id), "config_high")
    return _write_revision(admin_id, {}, f"回滚到 v{revision_id}" + ("(自动)" if auto else ""), effective_at=None,
                           reverted_from=revision_id, full_snapshot=snap)


def history(n: int = 20) -> list[dict]:
    with SessionMaker() as db:
        rows = db.query(ConfigRevision).order_by(ConfigRevision.id.desc()).limit(n).all()
        return [{"id": r.id, "created_at": r.created_at, "author": r.author_id, "note": r.note, "changed": json.loads(r.changed_keys or "[]"),
                 "effective_at": r.effective_at, "reverted_from": r.reverted_from} for r in rows]


# ---------- 旧存储同步(读取方仍然读旧位置,保证兼容与可回退) ----------
def _apply_sync(key: str, value) -> None:
    ck = R.get_key(key)
    if not ck or not ck.sync:
        return
    try:
        if value is _MISSING:
            value = R.default_value(key)
            if key == "lines":
                value = None
        kind, _, arg = ck.sync.partition(":")
        if kind == "appsetting" and value is not None:
            with SessionMaker() as db:
                row = db.query(AppSetting).filter(AppSetting.key == arg).first()
                text = json.dumps(value, ensure_ascii=False)
                if row:
                    row.value = text
                else:
                    db.add(AppSetting(key=arg, value=text))
                db.commit()
        elif kind == "global":
            from ..global_settings import update_global_config
            update_global_config({arg: value})
        elif kind == "catalog":
            from ..commerce import service as commerce
            commerce._set(commerce.CATALOG_KEY, value or {})
        elif kind == "prompt":
            from .. import prompt_settings
            prompt_settings.set_system_prompt(value or "")
    except Exception as exc:
        logger.warning("config sync %s failed: %s", key, type(exc).__name__)


def quick_publish(admin_id: int, key: str, value, note: str = "", *, audit: bool = True) -> int:
    """旧命令/向导的快捷方式:写草稿 → 立即发布(普通项)→ 审计。有 error 级自检问题会抛 PublishBlocked。"""
    issues = save_draft(admin_id, key, value)
    if L.errors(issues):
        discard_draft(admin_id, key)
        raise PublishBlocked([i for i in issues if i.level == "error"])
    return publish(admin_id, [key], note or "快捷设置", confirm_warnings=True, audit=audit)


# ---------- 导入导出 ----------
def export(ns: str | None = None, include_high: bool = False) -> dict:
    out = {}
    for key, ck in R.registry().items():
        if ns and ns != "all" and not (key == ns or key.startswith(ns + ".") or ck.group == ns):
            continue
        if ck.kind == "secret" or (ck.risk == "high" and not include_high):
            continue
        out[key] = effective_value(key)
    return {"version": latest_revision_id(), "values": out}


def import_json(admin_id: int, data: dict) -> list[DraftResult]:
    values = data.get("values") if isinstance(data, dict) and isinstance(data.get("values"), dict) else data
    if not isinstance(values, dict):
        raise ValueError("JSON 顶层必须是对象")
    results = []
    for key, val in values.items():
        if key not in R.registry():
            results.append(DraftResult(key, False, [L.Issue("error", "未知或锁定的配置项,已忽略")]))
            continue
        if val == effective_value(key):
            continue
        try:
            issues = save_draft(admin_id, key, val)
            results.append(DraftResult(key, not L.errors(issues), issues))
        except Exception as exc:
            results.append(DraftResult(key, False, [L.Issue("error", f"{type(exc).__name__}: {exc}")]))
    return results


# ---------- 发布后守护 ----------
def report_render_error(key: str) -> None:
    """文案渲染/格式化异常计数;发布后 15 分钟内同一次发布造成 ≥3 次异常 → 自动回滚到上一版并通知 owner。"""
    k = "copy." + key if ("copy." + key) in R.registry() else key
    if not k.startswith(R.GUARDED_NS) and k != "lines":
        return
    now = _now()
    _errors.append((now, k))
    recent = [t for t, _ in _errors if now - t <= GUARD_WINDOW_S]
    if len(recent) < GUARD_MAX_ERRORS:
        return
    try:
        with SessionMaker() as db:
            guarded = [r for r in db.query(ConfigRevision).order_by(ConfigRevision.id.desc()).limit(5).all()
                       if r.reverted_from is None and r.author_id != 0
                       and any(x.startswith(R.GUARDED_NS) or x == "lines" for x in json.loads(r.changed_keys or "[]"))]
            target = guarded[0] if guarded else None
            if not target:
                return
            prev = db.query(ConfigRevision).filter(ConfigRevision.id < target.id).order_by(ConfigRevision.id.desc()).first()
            prev_id = prev.id if prev else None
        _errors.clear()
        if prev_id is None:
            return
        rid = rollback(0, prev_id, auto=True)
        from ..outbox import enqueue_admin
        enqueue_admin(f"cfg-auto:{rid}", f"[[alert]] <b>配置已自动回滚</b>\nv{target.id} 发布后 15 分钟内渲染异常 {len(recent)} 次,已回滚(新版本 v{rid})。请检查文案占位符。")
    except Exception as exc:
        logger.error("auto rollback failed: %s", type(exc).__name__)


# ---------- 首次启动:从旧配置导入 revision 1 ----------
def bootstrap() -> int | None:
    """若还没有任何 revision,把现有 AppSetting / 全局参数 / ADMIN_USER_IDS 导入为 revision 1(author=0)。幂等。"""
    with SessionMaker() as db:
        if db.query(ConfigRevision).first():
            return None
        snap: dict = {}

        def legacy(name):
            row = db.query(AppSetting).filter(AppSetting.key == name).first()
            try:
                return json.loads(row.value) if row and row.value else None
            except ValueError:
                return None
        lines = legacy("frontdesk.lines")
        if lines:
            snap["lines"] = lines
        catalog = legacy("commerce.catalog")
        if catalog:
            snap["catalog"] = catalog
        ton = legacy("commerce.ton") or {}
        if ton.get("address"):
            snap["payments.ton.address"] = ton["address"]
        row = db.query(AppSetting).filter(AppSetting.key == "system_prompt").first()
        if row and (row.value or "").strip():
            snap["ai.operator_prompt"] = row.value
    try:
        from ..global_settings import get_global_setting
        for name in ("disable_captcha", "rate_limit_per_minute", "message_interval"):
            v = get_global_setting(name)
            if v not in (None, ""):
                snap["abuse." + name] = bool(v) if name == "disable_captcha" else v
        for name in ("llm_price_in_per_m", "llm_price_out_per_m", "global_daily_budget_usd", "global_daily_reply_cap"):
            v = get_global_setting(name)
            if v not in (None, ""):
                snap["budget." + name] = v
    except Exception:
        pass
    from .. import admin_user_ids
    snap["access.roles"] = {str(u): "owner" for u in admin_user_ids}
    with SessionMaker() as db:
        rev = ConfigRevision(author_id=0, note="迁移自旧配置", snapshot_json=json.dumps(snap, ensure_ascii=False),
                             changed_keys=json.dumps(sorted(snap), ensure_ascii=False))
        db.add(rev)
        db.commit()
        rid = rev.id
    invalidate()
    return rid
