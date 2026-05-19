#!/usr/bin/env python3
"""
独立爬虫入口 — 可配合 Cron Job 定时运行

用法:
    python scripts/scrape_prices.py

Cron 配置示例（每6小时执行一次）:
    0 */6 * * * cd /path/to/bot && /path/to/venv/bin/python scripts/scrape_prices.py >> /var/log/scraper.log 2>&1
"""
import logging
import os
import sys
import asyncio

# 将项目根目录加入 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s - %(levelname)s - %(message)s",
)

async def main():
    print("🕷️ 开始抓取 web3baimao.com 数据...")
    result = await run_scraper()
    print(f"✅ 抓取完成，共 {len(result.get('scraped_products', []))} 个发卡网商品，{len(result.get('scraped_services', []))} 个服务介绍")
    print(f"📁 数据已保存到 knowledge_base.json")


if __name__ == "__main__":
    # 直接导入 scraper 模块
    from importlib import import_module
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "interactive-bot", "rag"))

    # 由于模块名包含连字符，使用直接文件导入
    import importlib.util
    scraper_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "interactive-bot", "rag", "scraper.py"
    )
    spec = importlib.util.spec_from_file_location("scraper", scraper_path)
    scraper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scraper)
    run_scraper = scraper.run_scraper

    asyncio.run(main())
