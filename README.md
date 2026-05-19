# Intelligent Telegram Assistant

> Web3 智能双向机器人：Telegram Business 客服接待、AI 自动回复、RAG 知识库、人工接管和动态后台配置一体化方案。

<div align="center">
  <p>
    <strong>由 <a href="https://t.me/biqrxnxiYW">白猫工作室 (Web3Baimao)</a> 倾力打造并开源</strong>
  </p>
  <p>
    <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
    <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-green">
    <img alt="Telegram" src="https://img.shields.io/badge/Telegram-Business-blue">
  </p>
</div>

## 适合谁

- 想把 Telegram 私聊升级成“AI + 人工客服”双向接待系统的团队。
- 有产品、FAQ、报价、教程资料，希望机器人能基于知识库回答的项目方。
- 需要在后台群组里按用户 Topic 管理对话、接管和恢复 AI 的运营人员。
- 想学习 Telegram Business Bot、RAG、OpenAI 兼容模型接入的开发者。

## 核心特性

- **Telegram Business 双向接待**：用户私聊会同步到后台群组 Topic，客服在 Topic 内回复即可回传给用户。
- **AI 自动回复与人工热切换**：客服未介入时由 AI 接待，管理员可一键接管或恢复 AI。
- **RAG 知识库**：基于 `knowledge_base.json` 构建本地 ChromaDB 向量检索，回答更贴近业务资料。
- **多模型容灾**：支持 DeepSeek、OpenAI、Ollama 和任意 OpenAI API 兼容服务，并提供 fallback 配置。
- **动态后台面板**：通过 `/admin` 管理欢迎语、按钮、模型、Prompt、群发和全局风控配置。
- **会话与风控**：短期/长期记忆、频率限制、黑名单、验证码和支付关键词规则。

## 项目结构

```text
.
├── interactive-bot/          # 机器人主程序和 handlers
├── db/                       # SQLite / SQLAlchemy 数据模型
├── assets/imgs/              # 示例图片资源
├── scripts/                  # 辅助脚本
├── knowledge_base.json       # 业务知识库示例
├── sales_system_prompt.txt   # 默认销售/客服提示词
├── .env_example              # 环境变量模板
└── requirements.txt          # Python 依赖
```

## 快速开始

### 1. 准备 Telegram

1. 向 [@BotFather](https://t.me/BotFather) 申请一个新的 Telegram Bot Token。
2. 建立一个私有 Telegram 群组，开启“话题/Topic”功能，作为客服后台。
3. 将 Bot 拉入后台群组，并赋予消息管理、话题管理等必要权限。
4. 使用 `@GetTheirIDBot` 获取后台群组 ID 和管理员用户 ID。

### 2. 安装依赖

环境要求：Python 3.10+

```bash
git clone https://github.com/YOUR_NAME/YOUR_REPO.git
cd YOUR_REPO

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env_example .env
```

编辑 `.env`，至少填写：

```env
BOT_TOKEN=1234567890:REPLACE_WITH_YOUR_TELEGRAM_BOT_TOKEN
BOT_USERNAME=your_bot_username
ADMIN_GROUP_ID=-1001234567890
ADMIN_USER_IDS=123456789,987654321
LLM_API_BASE=https://api.deepseek.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL=deepseek-chat
```

### 4. 运行

```bash
python -m interactive-bot
```

运行后可以在机器人私聊或后台群组中使用 `/admin` 打开管理面板。

## 自定义知识库

- `knowledge_base.json`：替换产品、报价、FAQ、联系方式和业务资料。
- `sales_system_prompt.txt`：配置 AI 的角色、人设、回复风格、安全边界和引导策略。
- `knowledge_base.example.json`：可公开展示的脱敏知识库模板，建议新用户先复制它再改成自己的业务资料。

修改知识库后重启机器人，系统会重新构建本地向量数据。运行时生成的 `assets/chromadb/`、`assets/db.sqlite3`、`assets/*.pickle` 和 `log.txt` 不应提交到 GitHub。

## 开源前安全检查

- 重置所有曾经写进源码、截图、日志或 `.env_example` 的 Bot Token 和 API Key。
- 不要提交 `.env`、真实后台群组 ID、管理员 ID、客户资料、支付信息或钱包地址。
- 如果 `knowledge_base.json` 用作公开示例，建议保留占位符，避免放真实价格、收款信息和私域渠道。
- 如果你不想公开当前业务资料，可以只提交 `knowledge_base.example.json`，并把真实的 `knowledge_base.json` 放到私有部署环境。
- 生产环境建议使用单独的 Bot、单独的后台群组和最低权限管理员账号。

## Roadmap

- [ ] 增加 Docker / docker-compose 部署示例。
- [ ] 增加自动化测试和 lint 配置。
- [ ] 提供更通用的 `knowledge_base.example.json`。
- [ ] 增加截图或 GIF 演示后台接管流程。
- [ ] 增加插件化的支付、CRM 或工单系统集成。

## 贡献

欢迎提交 Issue 和 Pull Request。提交前请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)。如果你发现安全问题，请按 [SECURITY.md](./SECURITY.md) 私下报告。

## 鸣谢与合作

本项目由 **[白猫工作室 (Web3Baimao)](https://t.me/biqrxnxiYW)** 提供核心架构设计与优化。

- 官方反馈与交流频道：[点击加入白猫 Web3 频道](https://t.me/biqrxnxiYW)
- 官方发卡与业务网站：[web3baimao.com](https://web3baimao.com/)

## License

Apache License 2.0. 详情见 [LICENSE](./LICENSE)。
