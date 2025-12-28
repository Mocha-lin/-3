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
                # 提取 id, name, category 欄位即可
                return [{"id": d["id"], "name": d.get("name", d["id"]), "category": d.get("category", "未分類")} for d in data]
        except:
            return DEFAULT_TARGETS
    return DEFAULT_TARGETS

def get_stock_data(target):
    sid = target["id"]
    print(f"🚀 分析中: {sid} ...")
    try:
        ticker = yf.Ticker(f"{sid}.TW")
        fast = ticker.fast_info
        price = fast.get('last_price', 0)
        
        # 若抓不到價格，可能代號錯誤或下市
        if price == 0: 
            print(f"⚠️ 找不到 {sid} 的價格，跳過")
            return None

        change_pct = ((price - fast.get('previous_close', 0)) / fast.get('previous_close', 1)) * 100
        
        # 處理新聞
        news_text = ""
        news_list = []
        for n in ticker.news[:3]:
            title = n.get('title', '')
            ts = n.get('providerPublishTime', 0)
            date_s = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
            news_text += f"- {title}\n"
            news_list.append({"date": date_s, "title": title, "type": "neutral"})

        # AI 分析
        ai_data = {}
        if GEMINI_API_KEY:
            try:
                # 簡單獲取名稱，若無則用代號
                name = target.get('name', ticker.info.get('longName', sid))
                
                prompt = PROMPT_TEMPLATE.format(name=name, stock_id=sid, price=round(price,2), change_pct=round(change_pct,2), news_summary=news_text)
                res = model.generate_content(prompt)
                ai_data = json.loads(res.text.replace("```json","").replace("```",""))
            except Exception as e:
                print(f"AI Error: {e}")

        # 模擬圖表數據 (為了前端不壞掉，維持結構)
        hist = ticker.history(period="1y")
        # 簡單取樣
        prices = [round(x, 1) for x in hist['Close'].resample('ME').last().tail(12).tolist()]
        dates = [d.strftime('%Y-%m') for d in hist['Close'].resample('ME').last().tail(12).index]

        return {
            "id": sid,
            "name": ticker.info.get('longName', target.get('name', sid)), # 更新為正確名稱
            "category": target.get('category', "新加入"),
            "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "basicInfo": { "price": f"{price:.2f}", "change": f"{price - fast.get('previous_close', 0):+.2f}", "changePercent": f"{change_pct:+.2f}%" },
            "news": news_list,
            "moat": ai_data.get("moat", {"status": "-", "description": "分析中..."}),
            "technical": ai_data.get("technical", {"analysis": "資料不足", "bollinger": {"description": "-"}}),
            "financials": { "revenue": [], "peRiver": {"currentPE": "N/A"} }, # 簡化結構
            "chartsData": { "peRiverData": { "dates": dates, "price": prices, "pe20": [p*1.1 for p in prices], "pe16": [p*0.9 for p in prices], "pe12": [p*0.7 for p in prices] }, "revenueTrend": [] },
            "dividend": { "info": "-", "projectedReturn": "-" },
            "memo": ""
        }
    except Exception as e:
        print(f"❌ {sid} 錯誤: {e}")
        return None

if __name__ == "__main__":
    # 1. 讀取目前清單
    current_list = get_current_list()
    
    # 2. 檢查是否有外部傳入的新增指令 (GitHub Actions 傳入)
    # 格式預期: python app.py --add 2330
    parser = argparse.ArgumentParser()
    parser.add_argument('--add', type=str, help='新增股票代號')
    args = parser.parse_args()

    if args.add:
        new_id = args.add.strip()
        # 檢查是否已存在
        if not any(s['id'] == new_id for s in current_list):
            print(f"🆕 收到新增指令: {new_id}")
            current_list.insert(0, {"id": new_id, "name": new_id, "category": "新加入"})
        else:
            print(f"ℹ️ {new_id} 已在清單中")

    # 3. 執行更新
    final_data = []
    for target in current_list:
        data = get_stock_data(target)
        if data:
            final_data.append(data)
        time.sleep(2) # 避免 API 限制

    # 4. 存檔 (這會覆寫 data.json，下次讀取時就會包含新股票)
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        print("✅ data.json 更新完成")
