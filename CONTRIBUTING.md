# Contributing

感谢你愿意改进这个项目。为了让维护和协作更顺畅，请尽量按下面的方式提交。

## 本地开发

1. Fork 仓库并创建新分支。
2. 使用 Python 3.10+。
3. 安装依赖：

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell 可使用：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. 复制配置文件：

```bash
cp .env_example .env
```

请不要提交 `.env`、数据库、日志、向量库或任何真实 token。

## 提交建议

- 一个 Pull Request 尽量只解决一个问题。
- 如果改动了机器人行为，请在 PR 里说明触发条件、预期结果和手工验证方式。
- 如果新增配置项，请同步更新 `.env_example` 和 `README.md`。
- 如果发现安全问题，请不要直接公开 issue，先按 `SECURITY.md` 的方式联系维护者。
