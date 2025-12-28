import yfinance as yf
import google.generativeai as genai
import json
import os
import datetime
import time
import argparse
import sys
import pandas as pd

# --- 設定區 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

# 聰明選擇最強模型 (實驗版優先)
def get_best_models():
    default_models = ["models/gemini-1.5-pro", "models/gemini-1.5-flash"]
    try:
        all_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods and 'gemini' in m.name:
                all_models.append(m.name)
        all_models.sort(reverse=True)
        # Exp > Pro > Flash
        exp = [m for m in all_models if 'exp' in m]
        pro = [m for m in all_models if 'pro' in m and 'exp' not in m]
        flash = [m for m in all_models if 'flash' in m and 'exp' not in m]
        final = exp + pro + flash
        return final if final else default_models
    except: return default_models

MODEL_PRIORITY = get_best_models()

# Prompt 需配合新版 JSON 結構 (Data Guard)
PROMPT_TEMPLATE = """
你是 bbb 專業投資人。請根據以下【歷史事實數據】填補分析。
股票：{name} ({stock_id})
現價：{price} (漲跌 {change_pct}%)
新聞事實：
{news_summary}

請回傳 **嚴格的 JSON**，不要改動事實，只需推估未來與質性分析。
必須包含以下欄位 (Schema)：
{{
  "industry": {{ "moat_status": "...", "position_map": "...", "competitors": "..." }},
  "financials": {{
    "eps_table": [
       {{"period": "2024", "gross_margin": "-", "net_margin": "-", "eps": "-", "cumulative": "-", "is_estimate": false}} 
       // 請搜尋券商預估補上 2025/2026 年預估值 (is_estimate: true)
    ],
    "revenue_trend": [], 
    "valuation": {{ "pe_status": "...", "pb": "...", "roe": "...", "pe_river_data": {{ "dates": [], "price": [], "pe20": [], "pe16": [], "pe12": [] }} }}
  }},
  "technical": {{
    "status": "...", "signal_light": "red_flash/green_flash/stable", 
    "analysis_text": "...", 
    "predictions": {{ "days30": "...", "days180": "...", "days360": "...", "entry_zone": "..." }},
    "correction_c": "...",
    "bollinger": {{ "status": "...", "description": "..." }}
  }},
  "news_events": {{ "news": [], "calendar": [] }},
  "dividend": {{ "yield": "...", "history_roi": "...", "future_roi": "..." }},
  "memo": ""
}}
"""

def get_current_list():
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list): return data
        except: pass
    # 若為空回傳空陣列
    return []

def get_stock_data(target_id, old_data=None):
    # 支援傳入舊資料以保留 Memo
    stock_id = target_id.replace(".TW", "")
    print(f"🚀 分析: {stock_id} ...")
    
    try:
        ticker = yf.Ticker(f"{stock_id}.TW")
        
        # 1. 事實數據抓取 (Facts)
        price = 0; change_str = "0"; change_pct = "0%"
        try:
            fast = ticker.fast_info
            price = fast.get('last_price', 0)
            prev = fast.get('previous_close', 0)
            if price == 0: # 備援：抓不到即時抓收盤
                hist5 = ticker.history(period="5d")
                if not hist5.empty:
                    price = hist5['Close'].iloc[-1]
                    prev = hist5['Close'].iloc[-2]
            
            if price and prev:
                chg = price - prev
                pct = (chg / prev) * 100
                change_str = f"{chg:+.2f}"
                change_pct = f"{pct:+.2f}%"
        except: pass

        if price == 0: 
            print("❌ 抓無股價，跳過")
            return None

        # 2. 新聞事實
        news_text = ""
        try:
            for n in ticker.news[:3]:
                t = n.get('title', ''); d = n.get('providerPublishTime', 0)
                dt = datetime.datetime.fromtimestamp(d).strftime('%Y-%m-%d')
                news_text += f"- {dt}: {t}\n"
        except: pass

        # 3. K 線事實 (繪製河流圖基礎)
        hist1y = ticker.history(period="1y")
        dates = []; prices = []
        if not hist1y.empty:
            res = hist1y['Close'].resample('ME').last().tail(12)
            prices = [round(x, 2) for x in res.tolist()]
            dates = [d.strftime('%Y-%m') for d in res.index]

        # 4. AI 補完計畫
        ai_part = {}
        model_name = "N/A"
        
        if GEMINI_API_KEY:
            name = ticker.info.get('longName', stock_id)
            prompt = PROMPT_TEMPLATE.format(name=name, stock_id=stock_id, price=price, change_pct=change_pct, news_summary=news_text)
            
            for m in MODEL_PRIORITY:
                try:
                    model = genai.GenerativeModel(m)
                    resp = model.generate_content(prompt)
                    clean_json = resp.text.replace("```json","").replace("```","")
                    ai_part = json.loads(clean_json)
                    model_name = m.split('/')[-1]
                    break
                except Exception as e:
                    # print(f"Retry {m}...")
                    continue

        # 5. 資料合併 (Merge Logic) - 最重要的部分
        # 確保 AI 回傳結構若缺失，程式不會壞掉，而是用預設值補上
        
        def safe_get(d, keys, default):
            for k in keys:
                if isinstance(d, dict): d = d.get(k, {})
                else: return default
            return d if d else default

        # 建構河流圖數據 (混合 Python 真實股價 + AI 預估 PE)
        pe_river = {
            "dates": dates,
            "price": prices,
            "pe20": ai_part.get("financials", {}).get("valuation", {}).get("pe_river_data", {}).get("pe20", [p*1.2 for p in prices]), # 若AI失敗則用假數據防止圖表空白
            "pe16": ai_part.get("financials", {}).get("valuation", {}).get("pe_river_data", {}).get("pe16", [p*1.0 for p in prices]),
            "pe12": ai_part.get("financials", {}).get("valuation", {}).get("pe_river_data", {}).get("pe12", [p*0.8 for p in prices])
        }

        final_data = {
            "id": stock_id,
            "name": name if 'name' in locals() else stock_id,
            "category": old_data.get('category', '未分類') if old_data else '新加入',
            "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "ai_model": model_name,
            "memo": old_data.get('memo', '') if old_data else '',
            
            "basicInfo": {
                "price": f"{price:.2f}",
                "change": change_str,
                "changePercent": change_pct,
                "note": ""
            },
            
            "industry": ai_part.get("industry", { "moat_status": "-", "position_map": "-", "competitors": "-" }),
            
            "news_events": {
                "news": ai_part.get("news_events", {}).get("news", []),
                "calendar": ai_part.get("news_events", {}).get("calendar", [])
            },
            
            "financials": {
                "eps_table": ai_part.get("financials", {}).get("eps_table", []),
                "revenue_trend": ai_part.get("financials", {}).get("revenue_trend", []),
                "valuation": {
                    "pe_status": ai_part.get("financials", {}).get("valuation", {}).get("pe_status", "-"),
                    "pb": str(ticker.info.get('priceToBook', '-')),
                    "roe": ai_part.get("financials", {}).get("valuation", {}).get("roe", "-"),
                    "pe_river_data": pe_river
                }
            },
            
            "technical": ai_part.get("technical", {
                "status": "觀察", "signal_light": "stable", 
                "analysis_text": "資料分析中...", 
                "predictions": {"days30": "-", "entry_zone": "-"}, 
                "correction_c": "-", "bollinger": {"status": "-", "description": "-"}
            }),
            
            "dividend": ai_part.get("dividend", { "yield": "-", "history_roi": "-", "future_roi": "-" })
        }
        
        return final_data

    except Exception as e:
        print(f"❌ {stock_id} 處理失敗: {e}")
        return None

if __name__ == "__main__":
    current_data = get_current_list()
    # 建立舊資料的查找表 (Map) 以保留分類和備忘錄
    old_map = {item['id']: item for item in current_data}
    
    # 處理指令
    parser = argparse.ArgumentParser()
    parser.add_argument('--add', type=str, help='add stock')
    args = parser.parse_args()

    target_list = list(old_map.keys()) # 預設跑全部
    
    # 如果有新增指令，插到最前面
    if args.add:
        new_id = args.add.strip().upper()
        if new_id not in target_list:
            target_list.insert(0, new_id) # 新股票放第一
            # 初始化一個空的 dummy 防止 map 報錯
            old_map[new_id] = {"id": new_id, "category": "新加入", "memo": ""}

    final_results = []
    
    # 執行迴圈更新
    for sid in target_list:
        old_info = old_map.get(sid)
        result = get_stock_data(sid, old_data=old_info)
        
        if result:
            final_results.append(result)
        else:
            # 如果抓失敗了，為了不讓資料消失，把舊資料塞回去
            if old_info and 'name' in old_info: # 確保不是空的 dummy
                print(f"⚠️ {sid} 更新失敗，保留舊資料")
                final_results.append(old_info)
        
        time.sleep(2) # 休息一下避免被擋

    # 存檔
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(final_results, f, ensure_ascii=False, indent=2)
