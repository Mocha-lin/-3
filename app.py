import yfinance as yf
import google.generativeai as genai
import json
import os
import datetime
import time
import argparse
import sys
import re

# --- 設定區 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

# --- 🧠 智慧模型選擇器 (2026 Ready) ---
def get_best_models():
    """
    自動偵測 Google 目前所有可用模型，並依強弱排序：
    1. Experimental/Preview (實驗版/最新黑科技)
    2. Pro (最強穩定版)
    3. Flash (快速版)
    """
    default_models = ["models/gemini-1.5-pro", "models/gemini-1.5-flash"]
    try:
        print("🧠 正在掃描 Google 最新 AI 模型庫...")
        all_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                name = m.name
                if 'gemini' in name:
                    all_models.append(name)
        
        # 排序邏輯：字串反向排序確保版本號較高的在前面 (例: 1.5 > 1.0)
        # 這樣當 gemini-2.0 出現時，自然會排在 1.5 前面
        all_models.sort(reverse=True)

        # 分類篩選
        # 用戶偏好：優先嘗試 Experimental (預覽/最新資訊)
        exp_list = [m for m in all_models if 'exp' in m]
        pro_list = [m for m in all_models if 'pro' in m and 'exp' not in m]
        flash_list = [m for m in all_models if 'flash' in m and 'exp' not in m]
        
        # 組合最終優先順序：實驗版 > Pro版 > Flash版 > 其他
        final_list = exp_list + pro_list + flash_list
        
        if not final_list: return default_models
        
        # print(f"  📝 偵測到優先順序: {final_list[:3]}...") # 顯示前三名
        return final_list

    except Exception as e:
        print(f"⚠️ 無法自動偵測模型 ({e})，使用預設值")
        return default_models

# --- 全域初始化 ---
# 程式啟動時，自動建立當下最強的模型清單
MODEL_PRIORITY = get_best_models()


# --- 資料結構 ---
DEFAULT_TARGETS = [
    {"id": "2330", "name": "台積電", "category": "半導體代工"},
    {"id": "2454", "name": "聯發科", "category": "半導體上游"}
]

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
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [{"id": d["id"], "name": d.get("name", d["id"]), "category": d.get("category", "未分類")} for d in data]
        except: pass
    return DEFAULT_TARGETS

def get_stock_data(target):
    sid = target["id"]
    print(f"🚀 分析中: {sid} ...")
    try:
        ticker = yf.Ticker(f"{sid}.TW")
        
        # 1. 價格備援機制
        price = 0; change_pct = 0
        try:
            fast = ticker.fast_info
            price = fast.get('last_price', 0)
            prev_close = fast.get('previous_close', 0)
            if price and prev_close:
                change_pct = ((price - prev_close) / prev_close) * 100
        except: pass

        if not price or price == 0:
            hist5 = ticker.history(period="5d")
            if not hist5.empty:
                price = hist5['Close'].iloc[-1]
                if len(hist5) >= 2:
                    prev = hist5['Close'].iloc[-2]
                    change_pct = ((price - prev) / prev) * 100
        
        if price == 0:
            print(f"❌ {sid} 無法取得價格，跳過")
            return None

        # 2. 新聞
        news_text = ""
        news_list = []
        try:
            for n in ticker.news[:3]:
                title = n.get('title', '')
                ts = n.get('providerPublishTime', 0)
                d = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
                news_text += f"- {title}\n"
                news_list.append({"date": d, "title": title, "type": "neutral"})
        except: pass

        # 3. AI 智能輪詢 (Smart Polling)
        ai_data = {}
        used_model_name = "AI Busy"
        
        if GEMINI_API_KEY:
            name = target.get('name', ticker.info.get('longName', sid))
            prompt = PROMPT_TEMPLATE.format(name=name, stock_id=sid, price=round(price,2), change_pct=round(change_pct,2), news_summary=news_text)
            
            # 從最強的模型開始試，如果失敗(429 Limit)自動換下一個
            for model_name in MODEL_PRIORITY:
                try:
                    model = genai.GenerativeModel(model_name)
                    res = model.generate_content(prompt)
                    ai_data = json.loads(res.text.replace("```json","").replace("```",""))
                    used_model_name = model_name.replace("models/", "").replace("gemini-", "") # 簡化名稱顯示
                    print(f"  ✅ {used_model_name} 分析完成")
                    break 
                except Exception as e:
                    # print(f"  ⚠️ {model_name} 忙碌中，切換下一順位...")
                    continue
        
        # 4. 圖表數據
        hist1y = ticker.history(period="1y")
        prices = []; dates = []
        if not hist1y.empty:
            resampled = hist1y['Close'].resample('ME').last().tail(12)
            prices = [round(x, 1) for x in resampled.tolist()]
            dates = [d.strftime('%Y-%m') for d in resampled.index]

        return {
            "id": sid,
            "name": ticker.info.get('longName', target.get('name', sid)),
            "category": target.get('category', "新加入"),
            "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "ai_model": used_model_name,
            "basicInfo": { "price": f"{price:.2f}", "change": "0.00", "changePercent": f"{change_pct:+.2f}%" },
            "news": news_list,
            "moat": ai_data.get("moat", {"status": "-", "description": "AI 暫無回應"}),
            "technical": ai_data.get("technical", {"analysis": "無資料", "bollinger": {"description": "-"}}),
            "financials": { "revenue": [], "peRiver": {"currentPE": "N/A"} },
            "chartsData": { 
                "peRiverData": { "dates": dates, "price": prices, "pe20": [p*1.1 for p in prices], "pe16": [p*0.9 for p in prices], "pe12": [p*0.7 for p in prices] }, 
                "revenueTrend": [] 
            },
            "dividend": { "info": "-", "projectedReturn": "-" },
            "memo": ""
        }
    except Exception as e:
        print(f"❌ {sid} Error: {e}")
        return None

if __name__ == "__main__":
    current = get_current_list()
    parser = argparse.ArgumentParser()
    parser.add_argument('--add', type=str)
    args = parser.parse_args()

    if args.add:
        nid = args.add.strip()
        if not any(s['id'] == nid for s in current):
            current.insert(0, {"id": nid, "name": nid, "category": "新加入"})

    results = []
    for t in current:
        d = get_stock_data(t)
        if d: results.append(d)
        time.sleep(2)

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
