import yfinance as yf
import google.generativeai as genai
import json
import os
import datetime
import time
import argparse
import sys

# --- 設定區 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('models/gemini-1.5-flash')

# 預設清單 (如果 data.json 不存在時使用)
DEFAULT_TARGETS = [
    {"id": "2330", "name": "台積電", "category": "半導體代工"},
    {"id": "2454", "name": "聯發科", "category": "半導體上游"}
]

# AI 分析模板
PROMPT_TEMPLATE = """
你是一位專業分析師。請分析 {name} ({stock_id})。
數據：股價 {price}, 漲跌 {change_pct}%
新聞：
{news_summary}

請回傳嚴格 JSON (無 Markdown)，格式如下：
{{
  "moat": {{ "status": "...", "description": "..." }},
  "technical": {{ "analysis": "...", "marketStatus": "...", "correctionC": "...", "bollinger": {{ "status": "...", "description": "..." }}, "predictions": {{ "entryZone": "..." }} }}
}}
"""

def get_current_list():
    """讀取現有的 data.json 取得目前的股票清單"""
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [{"id": d["id"], "name": d.get("name", d["id"]), "category": d.get("category", "未分類")} for d in data]
        except:
            return DEFAULT_TARGETS
    return DEFAULT_TARGETS

def get_stock_data(target):
    sid = target["id"]
    print(f"🚀 分析中: {sid} ...")
    try:
        ticker = yf.Ticker(f"{sid}.TW")
        
        # --- 股價抓取邏輯 (含備援機制) ---
        price = 0
        change_pct = 0
        
        # 1. 嘗試抓即時股價
        try:
            fast = ticker.fast_info
            price = fast.get('last_price', 0)
            prev_close = fast.get('previous_close', 0)
            if price and prev_close:
                change_pct = ((price - prev_close) / prev_close) * 100
        except:
            pass

        # 2. 如果即時股價失敗 (是 0 或 None)，改抓歷史收盤價 (備援)
        if not price or price == 0:
            print(f"⚠️ {sid} 即時股價為 0 (可能是休市)，改抓歷史收盤價...")
            hist_recent = ticker.history(period="5d")
            if not hist_recent.empty:
                price = hist_recent['Close'].iloc[-1]
                if len(hist_recent) >= 2:
                    prev = hist_recent['Close'].iloc[-2]
                    change_pct = ((price - prev) / prev) * 100
        
        # 若還是 0，真的沒救了
        if price == 0: 
            print(f"❌ 無法取得 {sid} 價格，跳過")
            return None
        
        # 處理新聞
        news_text = ""
        news_list = []
        try:
            for n in ticker.news[:3]:
                title = n.get('title', '')
                ts = n.get('providerPublishTime', 0)
                date_s = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
                news_text += f"- {title}\n"
                news_list.append({"date": date_s, "title": title, "type": "neutral"})
        except:
            pass

        # AI 分析
        ai_data = {}
        if GEMINI_API_KEY:
            try:
                name = target.get('name', ticker.info.get('longName', sid))
                prompt = PROMPT_TEMPLATE.format(name=name, stock_id=sid, price=round(price,2), change_pct=round(change_pct,2), news_summary=news_text)
                res = model.generate_content(prompt)
                ai_data = json.loads(res.text.replace("```json","").replace("```",""))
            except Exception as e:
                print(f"AI Error: {e}")

        # 模擬圖表數據
        hist = ticker.history(period="1y")
        if not hist.empty:
            prices = [round(x, 1) for x in hist['Close'].resample('ME').last().tail(12).tolist()]
            dates = [d.strftime('%Y-%m') for d in hist['Close'].resample('ME').last().tail(12).index]
        else:
            prices = []
            dates = []

        return {
            "id": sid,
            "name": ticker.info.get('longName', target.get('name', sid)),
            "category": target.get('category', "新加入"),
            "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "basicInfo": { "price": f"{price:.2f}", "change": "0.00", "changePercent": f"{change_pct:+.2f}%" },
            "news": news_list,
            "moat": ai_data.get("moat", {"status": "-", "description": "分析中..."}),
            "technical": ai_data.get("technical", {"analysis": "資料不足", "bollinger": {"description": "-"}}),
            "financials": { "revenue": [], "peRiver": {"currentPE": "N/A"} },
            "chartsData": { "peRiverData": { "dates": dates, "price": prices, "pe20": [p*1.1 for p in prices], "pe16": [p*0.9 for p in prices], "pe12": [p*0.7 for p in prices] }, "revenueTrend": [] },
            "dividend": { "info": "-", "projectedReturn": "-" },
            "memo": ""
        }
    except Exception as e:
        print(f"❌ {sid} 嚴重錯誤: {e}")
        return None

if __name__ == "__main__":
    current_list = get_current_list()
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--add', type=str, help='新增股票代號')
    args = parser.parse_args()

    if args.add:
        new_id = args.add.strip()
        if not any(s['id'] == new_id for s in current_list):
            print(f"🆕 收到新增指令: {new_id}")
            current_list.insert(0, {"id": new_id, "name": new_id, "category": "新加入"})
        else:
            print(f"ℹ️ {new_id} 已在清單中")

    final_data = []
    for
