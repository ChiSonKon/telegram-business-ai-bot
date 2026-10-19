import asyncio
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "assets").mkdir()
    for k, v in dict(BOT_TOKEN="1:x", APP_NAME="t", ADMIN_GROUP_ID="-100", ADMIN_USER_IDS="1").items():
        monkeypatch.setenv(k, v)
    monkeypatch.syspath_prepend(str(ROOT))
    for name in [m for m in sys.modules if m == "db" or m.startswith("db.") or m.startswith("interactive-bot") or m.startswith("whitecat_core")]:
        del sys.modules[name]
    database = importlib.import_module("db.database")
    importlib.import_module("db.model")
    database.Base.metadata.create_all(database.engine)
    pkg = "interactive-bot"
    mod = types = type("E", (), {})()
    mod.ai_turn = importlib.import_module(f"{pkg}.ai_turn")
    mod.service = importlib.import_module(f"{pkg}.frontdesk.service")
    mod.sender = importlib.import_module(f"{pkg}.humanlike_sender")
    mod.gate = importlib.import_module(f"{pkg}.frontdesk.gate")
    mod.runtime = importlib.import_module("whitecat_core.runtime")
    mod.model = importlib.import_module("db.model")
    mod.db = database
    mod.precheck = importlib.import_module(f"{pkg}.frontdesk.precheck")
    mod.abuse = importlib.import_module(f"{pkg}.frontdesk.abuse")
    mod.gs = importlib.import_module(f"{pkg}.global_settings")
    mod.commerce = importlib.import_module(f"{pkg}.commerce.service")
    mod.notify = importlib.import_module(f"{pkg}.commerce.notify")
    mod.monitor = importlib.import_module(f"{pkg}.commerce.ton_monitor")
    mod.budget = importlib.import_module(f"{pkg}.frontdesk.budget")
    mod.router = importlib.import_module(f"{pkg}.frontdesk.router")
    mod.trust = importlib.import_module(f"{pkg}.frontdesk.trust")
    mod.sandbox = importlib.import_module(f"{pkg}.admin.sandbox")
    mod.runtime.reset_time()

    _real_sleep = asyncio.sleep

    async def no_sleep(*a, **k):
        await _real_sleep(0)                                               # 必须让出事件循环,否则后台循环任务会饿死主线程
    monkeypatch.setattr(mod.sender.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(mod.ai_turn, "search_knowledge", lambda q, **k: "月度套餐 29 U 官网 https://tg.web3baimao.com/")
    monkeypatch.setattr(mod.ai_turn, "load_system_prompt", lambda: "")
    with mod.db.SessionMaker() as s:
        s.add(mod.model.User(user_id=7, first_name="a", chat_mode="AI_MODE"))
        s.commit()
    return mod


