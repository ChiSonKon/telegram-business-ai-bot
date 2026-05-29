# -*- coding: utf-8 -*-
"""
白猫工作室智能双向机器人 — 本地一键自动化部署脚本
运行此脚本会自动将本地代码打包、上传至 VPS、停止旧冲突服务、配置系统服务并上线运行。
"""
import os
import tarfile
import paramiko
import sys

# 强制输出为 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# VPS 连接配置
HOSTNAME = '107.175.124.236'
PORT = 54322
USERNAME = 'root'
PASSWORD = 'Racknerd@cch666'

# 部署路径配置
REMOTE_DIR = '/root/interactive-bot'
TAR_NAME = 'bot.tar.gz'
LOCAL_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_TAR_PATH = os.path.join(LOCAL_SRC_DIR, TAR_NAME)

# 生产环境 .env 变量配置
ENV_CONTENT = """# ═══ 基本配置 ═══
APP_NAME=interactive-bot
BOT_TOKEN=8684360330:AAFkqGAL44lDC4exUo2R5gWuDVBEJ0ZbE6E
BOT_USERNAME=biqrxnxi_baimaoBOT

# ═══ 管理配置 ═══
# 客服管理超级话题群组ID（自动配置自您的私有管理群ID）
ADMIN_GROUP_ID=-1003897520360
# 管理员实际 Telegram 用户 ID（自动提取自您的聊天群组记录：白猫主理人）
ADMIN_USER_IDS=7642230430

# ═══ 默认 LLM 配置 (DeepSeek) ═══
LLM_API_BASE=https://api.deepseek.com/v1
LLM_API_KEY=sk-c151965f4588431f89470445f3f12c0b
LLM_MODEL=deepseek-v4-flash

# ═══ 风控与会话配置 ═══
BLACKLIST_USER_IDS=
RATE_LIMIT_PER_MINUTE=10
MESSAGE_INTERVAL=3
DISABLE_CAPTCHA=FALSE

# ═══ 功能开关 ═══
DELETE_TOPIC_AS_FOREVER_BAN=FALSE
DELETE_USER_MESSAGE_ON_CLEAR_CMD=TRUE
WELCOME_MESSAGE="欢迎来到白猫工作室 (Web3Baimao) ⚡️\\n我是您的 AI 客服助手，很高兴为您服务！\\n\\n我们提供专业的智能机器人定制开发、智能合约定制与安全审计、Web3社区矩阵系统搭建等服务。\\n\\n💡 如需人工客服，点击下方按钮即可呼叫主理人喔。"
"""

# SYSTEMD 守护进程配置
SYSTEMD_CONTENT = """[Unit]
Description=Web3Baimao Bidirectional Telegram Bot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/interactive-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/root/interactive-bot/venv/bin/python -m interactive-bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

def exclude_files(tarinfo):
    """过滤不需要打包的本地开发垃圾文件和敏感配置"""
    name = tarinfo.name
    ignored_patterns = [
        'venv',
        '.venv',
        '.git',
        '.env',
        '__pycache__',
        '.idea',
        '.vscode',
        'bot.tar.gz',
        'deploy.py',
        'log.txt',
    ]
    for pattern in ignored_patterns:
        if pattern in name:
            return None
    return tarinfo

def make_tarfile():
    """本地打包代码"""
    print(f"[*] 正在打包本地源码到 {LOCAL_TAR_PATH}...")
    with tarfile.open(LOCAL_TAR_PATH, "w:gz") as tar:
        tar.add(LOCAL_SRC_DIR, arcname='.', filter=exclude_files)
    print("[+] 本地打包完成！")

def deploy_to_vps():
    """上传并部署"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        print(f"[*] 正在建立 SSH 连接 {HOSTNAME}:{PORT}...")
        ssh.connect(HOSTNAME, port=PORT, username=USERNAME, password=PASSWORD, timeout=15)
        print("[+] SSH 连接成功！")
        
        # 1. 备份服务器现有的数据库和 .env (如果存在的话，确保安全第一)
        print("[*] 正在检查并备份服务器现有数据文件...")
        ssh.exec_command(f"mkdir -p {REMOTE_DIR}/assets")
        ssh.exec_command(f"cp {REMOTE_DIR}/assets/db.sqlite3 {REMOTE_DIR}/assets/db.sqlite3.bak 2>/dev/null")
        ssh.exec_command(f"cp {REMOTE_DIR}/.env {REMOTE_DIR}/.env.bak 2>/dev/null")
        
        # 2. 上传 tar 压缩包
        print(f"[*] 正在上传 {TAR_NAME} 至 VPS...")
        sftp = ssh.open_sftp()
        sftp.put(LOCAL_TAR_PATH, f"/root/{TAR_NAME}")
        sftp.close()
        print("[+] 上传压缩包成功！")
        
        # 3. 停止冲突服务
        print("[*] 正在关停冲突的旧 bot 守护服务...")
        ssh.exec_command("systemctl stop web3uu-telegram-bot.service")
        ssh.exec_command("systemctl disable web3uu-telegram-bot.service")
        
        # 停止新 bot 服务（如果在运行）
        ssh.exec_command("systemctl stop interactive-bot.service")
        
        # 4. 解压并还原
        print("[*] 正在 VPS 上解压新代码包...")
        ssh.exec_command(f"mkdir -p {REMOTE_DIR}")
        stdin, stdout, stderr = ssh.exec_command(f"tar -xzf /root/{TAR_NAME} -C {REMOTE_DIR}")
        stdout.read(); stderr.read()  # 等待解压完成
        
        # 还原备份的数据库 (如果之前存在过的话)
        ssh.exec_command(f"mv {REMOTE_DIR}/assets/db.sqlite3.bak {REMOTE_DIR}/assets/db.sqlite3 2>/dev/null")
        print("[+] 代码包解压成功！")
        
        # 5. 生成生产环境 .env 和 systemd 服务配置
        print("[*] 正在写入最新生产环境 .env 配置文件...")
        sftp = ssh.open_sftp()
        with sftp.file(f"{REMOTE_DIR}/.env", "w") as f:
            f.write(ENV_CONTENT)
        print("[+] 写入最新 .env 成功！")
            
        print("[*] 正在生成 Systemd 服务配置文件...")
        with sftp.file("/etc/systemd/system/interactive-bot.service", "w") as f:
            f.write(SYSTEMD_CONTENT)
        sftp.close()
        print("[+] Systemd 服务配置文件生成成功！")
        
        # 6. 配置 Python3 虚拟隔离环境 venv 并安装依赖
        print("[*] 正在检查并配置 VPS Python 虚拟环境 (这可能需要 1-2 分钟)...")
        stdin, stdout, stderr = ssh.exec_command(f"test -d {REMOTE_DIR}/venv && echo 'yes' || echo 'no'")
        has_venv = stdout.read().decode().strip() == 'yes'
        if not has_venv:
            print("[*] 正在创建全新的 venv 虚拟隔离环境...")
            stdin, stdout, stderr = ssh.exec_command(f"python3 -m venv {REMOTE_DIR}/venv")
            stdout.read(); stderr.read()
        
        print("[*] 正在安装 Python 依赖项...")
        stdin, stdout, stderr = ssh.exec_command(f"{REMOTE_DIR}/venv/bin/pip install --upgrade pip")
        stdout.read()
        stdin, stdout, stderr = ssh.exec_command(f"{REMOTE_DIR}/venv/bin/pip install -r {REMOTE_DIR}/requirements.txt")
        # 实时等待依赖安装完成
        stdout.read()
        print("[+] Python 依赖库安装完成！")
        
        # 7. 启动新服务
        print("[*] 正在启动并激活白猫智能双向机器人服务...")
        ssh.exec_command("systemctl daemon-reload")
        ssh.exec_command("systemctl enable interactive-bot.service")
        stdin, stdout, stderr = ssh.exec_command("systemctl start interactive-bot.service")
        stdout.read(); stderr.read()
        
        # 8. 验证运行状态
        print("\n" + "="*40 + "\n[+] 部署完毕，正在验证服务状态：")
        stdin, stdout, stderr = ssh.exec_command("systemctl status interactive-bot.service")
        print(stdout.read().decode('utf-8', errors='ignore'))
        
        print("\n[+] 追踪最新的启动日志输出：")
        stdin, stdout, stderr = ssh.exec_command("journalctl -u interactive-bot.service -n 30 --no-pager")
        print(stdout.read().decode('utf-8', errors='ignore'))
        
        # 清理远程临时压缩包
        ssh.exec_command(f"rm -f /root/{TAR_NAME}")
        ssh.close()
        
    except Exception as e:
        print(f"[-] 部署过程中发生错误: {e}")
        
    finally:
        # 清理本地临时打包文件
        if os.path.exists(LOCAL_TAR_PATH):
            os.remove(LOCAL_TAR_PATH)
            print("[*] 清理本地临时打包文件 bot.tar.gz")

if __name__ == '__main__':
    make_tarfile()
    deploy_to_vps()
