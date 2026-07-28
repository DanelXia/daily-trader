"""
散户情绪分析引擎。

从东方财富股吧（上证指数吧）多页抓取收盘后的散户帖子，
过滤机构内容，基于中文金融关键词进行看多/看空情绪评分。
"""

import json
import random
import re
import time
import urllib.request
from datetime import datetime

from config import (
    MAX_RETRIES,
    REQUEST_DELAY_MAX,
    REQUEST_DELAY_MIN,
    SENTIMENT_BEARISH_KEYWORDS,
    SENTIMENT_BULLISH_KEYWORDS,
    SENTIMENT_GUBA_PAGES,
    SENTIMENT_INSTITUTIONAL_KEYWORDS,
    SENTIMENT_MARKET_CLOSE_HOUR,
    SENTIMENT_MAX_POSTS_PER_PLATFORM,
    SENTIMENT_MIN_POSTS_REQUIRED,
    SENTIMENT_PLATFORMS,
)


class SentimentAnalyzer:
    """散户情绪分析引擎"""

    def __init__(self):
        self.today = datetime.now()

    # ==================================================================
    # Public API
    # ==================================================================

    def analyze(self) -> dict | None:
        """
        从各平台抓取散户帖子并进行情绪分析。
        返回 dict 或 None（帖子不足时）。
        """
        all_posts = self._collect_all_posts()
        if not all_posts:
            print("[Sentiment] 未收集到任何帖子")
            return None

        if len(all_posts) < SENTIMENT_MIN_POSTS_REQUIRED:
            print(
                f"[Sentiment] 帖子数量不足 "
                f"({len(all_posts)} < {SENTIMENT_MIN_POSTS_REQUIRED})，跳过分析"
            )
            return None

        result = self._analyze_sentiment(all_posts)
        result["platform_breakdown"] = self._count_by_platform(all_posts)
        result["summary_text"] = self._generate_summary(result)

        print(f"[Sentiment] 分析完成: {result['summary_text']}")
        return result

    # ==================================================================
    # Data Collection
    # ==================================================================

    def _collect_all_posts(self) -> list[dict]:
        posts = []

        em_posts = self._fetch_eastmoney_posts()
        if em_posts:
            posts.extend(em_posts)
            print(f"[Sentiment] 东方财富股吧: {len(em_posts)} posts ({SENTIMENT_GUBA_PAGES}页)")

        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        xq_posts = self._fetch_xueqiu_posts()
        if xq_posts:
            posts.extend(xq_posts)
            print(f"[Sentiment] 雪球: {len(xq_posts)} posts")

        return posts

    def _fetch_eastmoney_posts(self) -> list[dict]:
        """从东方财富股吧（上证指数吧）多页抓取帖子"""
        all_raw = []
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        for page in range(1, SENTIMENT_GUBA_PAGES + 1):
            if page == 1:
                url = "https://guba.eastmoney.com/list,zssh000001,f_1.html"
            else:
                url = f"https://guba.eastmoney.com/list,zssh000001_{page}.html"

            for attempt in range(MAX_RETRIES):
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        html = resp.read().decode("utf-8", errors="replace")
                    page_posts = self._extract_posts_eastmoney(html)
                    all_raw.extend(page_posts)
                    break
                except Exception as e:
                    print(f"[Sentiment] 东方财富 p{page} 失败 (attempt {attempt+1}): {e}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            # 页间延迟，避免被反爬
            if page < SENTIMENT_GUBA_PAGES:
                time.sleep(random.uniform(1.5, 3.0))

        return self._filter_posts(all_raw, platform="eastmoney")

    def _fetch_xueqiu_posts(self) -> list[dict]:
        """从雪球大盘讨论区抓取帖子"""
        try:
            import requests

            session = requests.Session()
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            })

            # 先访问首页获取 cookies
            try:
                session.get(
                    SENTIMENT_PLATFORMS["xueqiu"]["homepage"],
                    timeout=15,
                )
            except Exception:
                pass

            time.sleep(random.uniform(2, 4))

            api_url = SENTIMENT_PLATFORMS["xueqiu"]["api_url"]
            category = SENTIMENT_PLATFORMS["xueqiu"]["category"]

            for attempt in range(MAX_RETRIES):
                try:
                    resp = session.get(
                        api_url,
                        params={"category": category},
                        timeout=15,
                    )
                    data = resp.json()
                    raw_posts = self._extract_posts_xueqiu(data)
                    return self._filter_posts(raw_posts, platform="xueqiu")

                except Exception as e:
                    print(f"[Sentiment] 雪球失败 (attempt {attempt+1}): {e}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        except ImportError:
            print("[Sentiment] requests 库不可用，跳过雪球")
        except Exception as e:
            print(f"[Sentiment] 雪球采集失败: {e}")

        return []

    # ==================================================================
    # Post Extraction (platform-specific parsing)
    # ==================================================================

    def _extract_posts_eastmoney(self, html: str) -> list[dict]:
        """从东方财富股吧 HTML 页面解析帖子列表"""
        posts = []
        try:
            # 每个帖子在一个 <tr class="listitem"> 中
            items = re.findall(
                r'<tr class="listitem">(.*?)</tr>', html, re.DOTALL
            )
            for item in items[:SENTIMENT_MAX_POSTS_PER_PLATFORM]:
                # 标题
                title_match = re.search(
                    r'class="title".*?<a[^>]*>([^<]+)</a>', item, re.DOTALL
                )
                title = title_match.group(1).strip() if title_match else ""

                # 时间 (格式: MM-DD HH:MM)
                time_match = re.search(
                    r'class="update">([^<]+)</div>', item
                )
                timestamp = time_match.group(1).strip() if time_match else ""

                # 阅读数
                read_match = re.search(
                    r'class="read">(\d+)</div>', item
                )
                read_count = int(read_match.group(1)) if read_match else 0

                # 回复数
                reply_match = re.search(
                    r'class="reply">(\d+)</div>', item
                )
                reply_count = int(reply_match.group(1)) if reply_match else 0

                if title:
                    posts.append({
                        "title": title,
                        "snippet": title,
                        "timestamp": timestamp,
                        "read_count": read_count + reply_count,
                        "platform": "eastmoney",
                    })
        except Exception as e:
            print(f"[Sentiment] 东方财富解析失败: {e}")
        return posts

    def _extract_posts_xueqiu(self, data: dict) -> list[dict]:
        """解析雪球接口返回的JSON，提取帖子列表"""
        posts = []
        try:
            items = data.get("list", [])
            if not isinstance(items, list):
                return posts

            for item in items[:SENTIMENT_MAX_POSTS_PER_PLATFORM]:
                desc = str(item.get("description", "") or item.get("text", "") or "")
                desc_clean = re.sub(r"<[^>]+>", "", desc)
                posts.append({
                    "title": desc_clean[:80],
                    "snippet": desc_clean[:200],
                    "timestamp": str(item.get("created_at", "") or ""),
                    "read_count": int(item.get("view_count", 0) or 0),
                    "platform": "xueqiu",
                })
        except Exception as e:
            print(f"[Sentiment] 雪球解析失败: {e}")
        return posts

    # ==================================================================
    # Filters
    # ==================================================================

    def _filter_posts(self, posts: list[dict], platform: str) -> list[dict]:
        posts = self._filter_by_time(posts)
        posts = self._filter_institutional(posts)
        posts = self._deduplicate(posts)
        return posts

    def _filter_by_time(self, posts: list[dict]) -> list[dict]:
        """只保留今日收盘后的帖子"""
        today_str = self.today.strftime("%Y-%m-%d")
        filtered = []
        for p in posts:
            ts = p.get("timestamp", "")
            if not ts:
                continue

            dt = None
            is_12h = False
            for fmt in [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%m-%d %H:%M",
            ]:
                try:
                    dt = datetime.strptime(ts[:16], fmt)
                    if fmt.startswith("%m-%d"):
                        dt = dt.replace(year=self.today.year)
                        is_12h = True
                    break
                except (ValueError, IndexError):
                    continue

            if dt is None:
                filtered.append(p)
                continue

            # 股吧使用12小时制，下午1-8点 +12 转24小时
            if is_12h and 1 <= dt.hour <= 8:
                dt = dt.replace(hour=dt.hour + 12)

            dt_str = dt.strftime("%Y-%m-%d")
            if dt_str == today_str and dt.hour >= SENTIMENT_MARKET_CLOSE_HOUR:
                filtered.append(p)
        return filtered

    def _filter_institutional(self, posts: list[dict]) -> list[dict]:
        """排除包含机构关键词的帖子"""
        filtered = []
        for p in posts:
            text = p.get("title", "") + " " + p.get("snippet", "")
            if not self._contains_any(text, SENTIMENT_INSTITUTIONAL_KEYWORDS):
                filtered.append(p)
        return filtered

    def _deduplicate(self, posts: list[dict]) -> list[dict]:
        """按标题去重"""
        seen = set()
        unique = []
        for p in posts:
            title = p.get("title", "").strip()
            if title and title not in seen:
                seen.add(title)
                unique.append(p)
        return unique

    # ==================================================================
    # Sentiment Scoring
    # ==================================================================

    def _analyze_sentiment(self, posts: list[dict]) -> dict:
        bullish_count = 0
        bearish_count = 0
        total_score = 0
        max_possible = 0
        keyword_counts: dict[str, int] = {}

        for p in posts:
            text = p.get("title", "") + " " + p.get("snippet", "")

            b_count = self._count_keywords(text, SENTIMENT_BULLISH_KEYWORDS)
            be_count = self._count_keywords(text, SENTIMENT_BEARISH_KEYWORDS)

            score = b_count - be_count
            total_score += score
            max_possible += max(b_count + be_count, 1)

            if score > 0:
                bullish_count += 1
            elif score < 0:
                bearish_count += 1

            for kw in self._extract_matched(text, SENTIMENT_BULLISH_KEYWORDS):
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
            for kw in self._extract_matched(text, SENTIMENT_BEARISH_KEYWORDS):
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1

        total = len(posts)
        bullish_ratio = bullish_count / total if total > 0 else 0.5

        if max_possible > 0:
            normalized = total_score / max_possible
            sentiment_index = round(50 + normalized * 50, 1)
        else:
            sentiment_index = 50.0

        sentiment_index = max(0.0, min(100.0, sentiment_index))

        top_kw = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "sentiment_index": sentiment_index,
            "bullish_ratio": round(bullish_ratio, 3),
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": total - bullish_count - bearish_count,
            "total_posts_analyzed": total,
            "top_keywords": [{"keyword": kw, "count": c} for kw, c in top_kw],
        }

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _contains_any(text: str, keywords: list[str]) -> bool:
        return any(kw in text for kw in keywords)

    @staticmethod
    def _count_keywords(text: str, keywords: list[str]) -> int:
        return sum(1 for kw in keywords if kw in text)

    @staticmethod
    def _extract_matched(text: str, keywords: list[str]) -> list[str]:
        return [kw for kw in keywords if kw in text]

    @staticmethod
    def _count_by_platform(posts: list[dict]) -> dict[str, int]:
        result: dict[str, int] = {}
        for p in posts:
            plat = p.get("platform", "unknown")
            result[plat] = result.get(plat, 0) + 1
        return result

    def _generate_summary(self, result: dict) -> str:
        idx = result["sentiment_index"]
        ratio = result["bullish_ratio"]
        total = result["total_posts_analyzed"]

        if idx >= 70:
            mood = "极度乐观"
        elif idx >= 60:
            mood = "偏多"
        elif idx >= 45:
            mood = "中性"
        elif idx >= 35:
            mood = "偏空"
        else:
            mood = "极度悲观"

        top_kw = ", ".join(
            item["keyword"] for item in result.get("top_keywords", [])[:3]
        ) or "无显著关键词"

        return (
            f"散户情绪{mood}（指数={idx}，样本={total}帖，"
            f"看多比例={ratio:.0%}），热门词: {top_kw}"
        )
