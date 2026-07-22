"""
药明康德 603259 价格监控脚本
触发价: 124.50 元
"""
import ctypes
import json
import urllib.request
import os

TARGET_PRICE = 124.50
STOCK_CODE = "sh603259"
STOCK_NAME = "药明康德"
ALERT_FLAG = os.path.join(os.path.dirname(__file__), ".alert_603259_triggered")

if os.path.exists(ALERT_FLAG):
    exit(0)

try:
    url = f"https://hq.sinajs.cn/list={STOCK_CODE}"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("gbk")
    parts = raw.split(",")
    price = float(parts[3])
    prev_close = float(parts[2])
    high = float(parts[4])
    low = float(parts[5])
    change_pct = round((price - prev_close) / prev_close * 100, 2)
except Exception as e:
    ctypes.windll.user32.MessageBoxW(0, f"获取价格失败: {e}", "价格监控错误", 0x10)
    exit(1)

if price <= TARGET_PRICE:
    msg = (
        f"{STOCK_NAME} 已回踩到建仓区!\n\n"
        f"现价: {price:.2f} 元  ({change_pct:+.2f}%)\n"
        f"今日: {low:.2f} - {high:.2f}\n"
        f"目标建仓: ≤{TARGET_PRICE:.2f} 元\n"
        f"止损: 118.73 元\n"
        f"目标: 131.20 元\n"
        f"仓位: 10%"
    )
    ctypes.windll.user32.MessageBoxW(0, msg, f"建仓提醒 - {STOCK_NAME}", 0x40)
    with open(ALERT_FLAG, "w") as f:
        f.write("1")
