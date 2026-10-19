# 部署指南(Linux + systemd)

## 1. 准备
```bash
sudo useradd -r -m -d /opt/interactive-bot bot
sudo -u bot git clone https://github.com/ChiSonKon/telegram-business-ai-bot.git /opt/interactive-bot
cd /opt/interactive-bot
sudo -u bot python3 -m venv venv && sudo -u bot venv/bin/pip install -r requirements.txt
sudo -u bot cp .env_example .env && sudo chmod 600 .env       # 编辑 .env,填入 BOT_TOKEN 等
```

## 2. 启动
```bash
sudo cp deploy/interactive-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now interactive-bot
journalctl -u interactive-bot -f          # 启动日志应出现 "Bot started, polling..." 与各定时任务
```
数据库(SQLite `assets/db.sqlite3`)在首次启动时自动建表,升级时自动加列。

## 3. 升级前备份与回滚
```bash
# 备份(SQLite 在线备份 + 代码)
sudo -u bot sqlite3 assets/db.sqlite3 ".backup '/opt/backup-$(date +%F).sqlite3'"
tar czf /opt/backup-code-$(date +%F).tgz -C /opt interactive-bot --exclude=interactive-bot/venv

# 升级
sudo -u bot git pull && sudo -u bot venv/bin/pip install -r requirements.txt && sudo systemctl restart interactive-bot

# 回滚:停服务 → 还原数据库与代码 → 启动
```

## 4. 上线检查清单
- [ ] `.env` 权限 600,未入库;`ADMIN_USER_IDS` 第一位是业主
- [ ] 已替换 `knowledge_base.json`、`sales_system_prompt.txt` 与 `/cfg` 里的欢迎语、业务线、官方账号白名单
- [ ] 启用收款前:网关专用商户 + 1–2 USDT 小额实测;`/cfg` 中配置并发布官方收款地址
- [ ] 管理群开启话题,Bot 为管理员;`/admin` 能打开控制台
- [ ] 用测试号完整走一遍:/start → 选业务 → 问答 → 转同事 → 同事回复
- [ ] 每日备份 SQLite

## 5. 常见问题
- **AI 没回复**:管理群会收到"AI 调用失败"提醒(脱敏原因);检查 `/admin → 设置 → AI 引擎` 的密钥与模型名。
- **DeepSeek V4 空回复**:V4 默认开启思考模式会吃光 `max_tokens`;本项目已对 DeepSeek 显式关闭 thinking。
- **Business 里看不到高级表情/按钮图标**:Telegram 是否接受以运行时探测为准,被拒时自动降级,见日志 `decorated send rejected`。
