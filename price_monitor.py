# -*- coding: utf-8 -*-
"""
竞品价格监控系统  price_monitor.py
零第三方依赖。每天运行一次即可，配合系统计划任务实现定时监控。

流程：
  采集（urllib抓取 / 失败自动回退离线夹具）
    -> 正则解析价格 -> 写入历史库 JSON（去重，同日覆盖）
    -> 与上次价、7日前价对比 -> 生成 Markdown + HTML 周报
    -> 输出价格策略提示（跟价/观察/无需动作）

用法：
  python price_monitor.py                 # 执行一轮监控并出报告
  python price_monitor.py --once          # 同上（可用于计划任务）

真实使用时：
  把 products.json 里 url 换成京东/天猫真实商品链接、price_pattern
  按页面真实结构调整；电商大站多有反爬，建议改为接入平台开放API
  或官方联盟接口，本脚本保留采集管道与分析逻辑的完整演示。
"""
import json, re, os, sys, argparse, urllib.request
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(BASE, "data", "price_history.json")
DROP_ALERT = 0.05   # 降价超过5%触发跟价提示


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def fetch(product):
    """优先抓真实URL，失败回退本地夹具，保证监控管道不中断"""
    try:
        req = urllib.request.Request(product["url"], headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")
        return html, "online"
    except Exception as e:
        fx = os.path.join(BASE, product["fixture"])
        with open(fx, encoding="utf-8") as f:
            return f.read(), f"fixture({type(e).__name__})"


def parse_price(html, pattern):
    m = re.search(pattern, html)
    return float(m.group(1)) if m else None


def run():
    products = load_json(os.path.join(BASE, "products.json"), [])
    history = load_json(HIST, {})
    today = datetime.now().strftime("%Y-%m-%d")
    rows = []

    for p in products:
        html, source = fetch(p)
        price = parse_price(html, p["price_pattern"])
        if price is None:
            rows.append({**p, "price": None, "source": source})
            continue

        hist = history.setdefault(p["sku"], [])
        hist = [h for h in hist if h["date"] != today]
        hist.append({"date": today, "price": price, "source": source})
        hist.sort(key=lambda h: h["date"])
        history[p["sku"]] = hist

        prev = hist[-2]["price"] if len(hist) >= 2 else None
        d7 = next((h["price"] for h in hist
                   if h["date"] == (datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")), None)
        rows.append({**p, "price": price, "prev": prev, "d7": d7, "source": source})

    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    write_markdown(rows, today)
    write_html(rows, today)
    console(rows)


def pct(cur, old):
    return (cur - old) / old if old else 0.0


def action(cur, old):
    if old is None:
        return "首次入库", "#888"
    d = pct(cur, old)
    if d <= -DROP_ALERT:
        return f"竞品降价{abs(d)*100:.1f}%，评估跟价/加券", "#e03636"
    if d >= DROP_ALERT:
        return "竞品涨价，可维持现价抢量", "#1aad4a"
    return "价格平稳，持续观察", "#888"


def console(rows):
    print("=" * 64)
    print(f"  竞品价格监控日报  {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 64)
    for r in rows:
        if r["price"] is None:
            print(f"  {r['name'][:24]:<26} 解析失败")
            continue
        d = f"{pct(r['price'],r['prev'])*100:+.1f}%" if r.get("prev") else "--"
        print(f"  {r['name'][:24]:<26} ¥{r['price']:<8} 环比{d:>7}  [{r['source']}]")
    print("=" * 64)


def write_markdown(rows, today):
    L = [f"# 跑鞋品类竞品价格监控周报（{today}）\n",
         "| 商品 | 现价 | 上次价 | 7日前 | 环比 | 策略提示 |",
         "|---|---|---|---|---|---|"]
    tips = []
    for r in rows:
        if r["price"] is None:
            continue
        d = f"{pct(r['price'],r['prev'])*100:+.1f}%" if r.get("prev") else "--"
        tip, _ = action(r["price"], r.get("prev"))
        if "降价" in tip:
            tips.append(f"- **{r['name']}** {tip}")
        L.append(f"| {r['name']} | ¥{r['price']} | "
                 f"{'¥'+str(r.get('prev')) if r.get('prev') else '--'} | "
                 f"{'¥'+str(r.get('d7')) if r.get('d7') else '--'} | {d} | {tip} |")
    L += ["", "## 本周结论",
          "\n".join(tips) if tips else "- 监控品类价格整体平稳，无需调价动作。",
          "", "> 数据管道：定时采集→解析→历史库→自动报告；夹具数据仅作演示。"]
    with open(os.path.join(BASE, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def write_html(rows, today):
    trs = []
    for r in rows:
        if r["price"] is None:
            continue
        d = pct(r['price'], r.get('prev')) if r.get('prev') else 0
        dtxt = f"{d*100:+.1f}%" if r.get("prev") else "--"
        tip, color = action(r["price"], r.get("prev"))
        trs.append(f"<tr><td style='text-align:left'>{r['name']}</td><td>¥{r['price']}</td>"
                   f"<td>{'¥'+str(r.get('prev')) if r.get('prev') else '--'}</td>"
                   f"<td style='color:{'#e03636' if d<0 else '#1aad4a' if d>0 else '#888'}'>{dtxt}</td>"
                   f"<td style='color:{color}'>{tip}</td></tr>")
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>竞品价格监控周报</title><style>
body{{font-family:"Microsoft YaHei";background:#f4f5f7;padding:24px;color:#222}}
.box{{max-width:860px;margin:auto;background:#fff;border-radius:12px;padding:28px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
h1{{font-size:19px;margin-bottom:4px}}.s{{color:#999;font-size:12px;margin-bottom:18px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th,td{{padding:11px 8px;border-bottom:1px solid #f0f0f0;text-align:right}}
th{{background:#fafafa;color:#888;font-weight:400}}
</style></head><body><div class="box">
<h1>跑鞋品类竞品价格监控周报</h1><div class="s">报告日期 {today} · 数据管道：采集→解析→历史库→自动报告（演示数据）</div>
<table><thead><tr><th style="text-align:left">商品</th><th>现价</th><th>上次价</th><th>环比</th><th>策略提示</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table></div></body></html>"""
    with open(os.path.join(BASE, "report.html"), "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    run()
