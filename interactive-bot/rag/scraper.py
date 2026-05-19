"""
RAG 爬虫 — 抓取 web3baimao.com 和 airdrop.web3baimao.com 的实时数据
将商品/价格/业务介绍存为 knowledge_base.json

可作为独立脚本运行，配合 Cron Job 定时更新：
    python -m interactive-bot.rag.scraper
或
    python scripts/scrape_prices.py
"""
import json
import os
import logging
from datetime import datetime

import re

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "knowledge_base.json",
)


async def scrape_shop(url: str = "https://web3baimao.com/") -> list[dict]:
    """抓取发卡网商品列表和价格"""
    products = []
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # 尝试解析商品卡片（兼容常见发卡网/落地页结构）
        cards = soup.select(
            ".goods-item, .goods-card, .product-card, .product-item, "
            ".card, .item, .shop-item, .commodity, .goods"
        )

        for card in cards:
            name_el = card.select_one("h3, h4, .title, .goods-name, .name")
            price_el = card.select_one(".price, .amount, .cost, .money, .goods-price")
            stock_el = card.select_one(".stock, .inventory, .goods-stock, .status")

            if name_el and price_el:
                products.append({
                    "name": name_el.get_text(strip=True),
                    "price": price_el.get_text(strip=True),
                    "stock": stock_el.get_text(strip=True) if stock_el else "",
                    "category": "发卡网商品",
                    "source": url,
                })

        # 兜底：如果页面结构不匹配，按文本行提取包含价格的商品线索，避免完全抓不到。
        if not products:
            text_lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
            price_pattern = re.compile(r"(¥|￥|USDT|U|u|元|CNY|cny)\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*(USDT|U|u|元)")
            for line in text_lines:
                if price_pattern.search(line) and len(line) <= 120:
                    products.append({
                        "name": line,
                        "price": "页面文本含价格，成交前请到发卡网确认",
                        "stock": "",
                        "category": "发卡网商品",
                        "source": url,
                    })
                if len(products) >= 30:
                    break

        logger.info(f"从 {url} 抓取到 {len(products)} 个商品")

    except Exception as e:
        logger.error(f"抓取 {url} 失败: {e}")

    return products


async def scrape_airdrop_intro(url: str = "https://airdrop.web3baimao.com/") -> list[dict]:
    """抓取空投业务介绍页面"""
    services = []
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # 提取主要内容区块
        sections = soup.select("section, .section, .content-block, article")
        for section in sections:
            title_el = section.select_one("h2, h3")
            content_el = section.select_one("p, .description, .content")

            if title_el:
                services.append({
                    "title": title_el.get_text(strip=True),
                    "content": content_el.get_text(strip=True) if content_el else "",
                    "url": url,
                })

        logger.info(f"从 {url} 抓取到 {len(services)} 个服务介绍")

    except Exception as e:
        logger.error(f"抓取 {url} 失败: {e}")

    return services


async def run_scraper(output_path: str = None):
    """
    执行完整的抓取流程，更新 knowledge_base.json。
    仅更新爬取到的字段，保留手动维护的数据。
    """
    path = output_path or OUTPUT_FILE

    # 加载现有数据
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    # 抓取新数据（显式传入目标 URL）
    shop_products = await scrape_shop("https://web3baimao.com/")
    airdrop_services = await scrape_airdrop_intro("https://airdrop.web3baimao.com/")

    # 每次都更新对应字段；为空也写入，便于判断卡网是否抓取失败
    existing["scraped_products"] = shop_products

    existing["scraped_services"] = airdrop_services

    existing["last_updated"] = datetime.now().isoformat()

    # 写入
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    logger.info(f"知识库已更新: {path}")
    return existing


# 支持直接运行
if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_scraper())
