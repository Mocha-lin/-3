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

# 1. 智慧模型選擇器
def get_best_models():
    defaults = ["models/gemini-1.5-pro", "models/gemini-1.5-flash"]
    try:
        all_m = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods and 'gemini' in m.name]
        all_m.sort(reverse=True) # 版本號降序
        exp = [m for m in all_m if 'exp' in m]
        pro = [m for m in all_m if 'pro' in m and 'exp' not in m]
        return exp + pro + [m for m in all_m if 'flash' in m]
    except: return defaults

MODEL_PRIORITY = get_best_models()

# 2. 資料結構指令 (Schema)
PROMPT_TEMPLATE = """
你是 bbb 專業投資人。請基於以下【絕對事實】補完分析報告。

【鎖定事實 (API Data)】- **嚴禁修改數值**：
股票：{name} ({stock_id})
現價：{price} (漲跌 {change_pct}%)
歷史股價(近一年)：用於繪製 PE 河流圖的實線基礎。

【你的任務 (需聯網搜尋)】：
1. **財務補完**：
   - 營收：若本月/下月尚未公布，請搜尋預估值補上，標記 `is_estimate: true`。
   - EPS：保留已知歷史 EPS，在後面補上 2025/2026 年預估值，標記 `is_estimate: true`。
   - 估值：計算 PE 倍數，繪製河流圖的虛線區間。
2. **質性分析**：產業護城河、競爭者。
3. **技術判讀**：給出操作策略。

請回傳 **純 JSON**，格式如下：
{{
  "industry": {{ "moat_status": "...", "position_map": "...", "competitors": "..." }},
  "financials": {{
    "eps_table": [
       {{ "period": "2024Q3", "gross_margin": "...", "net_margin": "...", "eps": "事實值", "cumulative": "...", "is_estimate": false }},
       {{ "period": "2025Q1", "gross_margin": "...", "net_margin": "...", "eps": "預估值", "cumulative": "...", "is_estimate": true }}
    ],
    "revenue_trend": [
       {{ "month": "2024-11", "revenue": "事實", "mom": "..", "yoy": "..", "is_estimate": false }}
    ],
    "valuation": {{
        "pe_status": "...", "pb": "...", "roe": "...",
        "pe_river_data": {{
            "dates": ["跟隨歷史股價日期"...],
            "price": ["跟隨歷史股價實數"...],
            "pe20": [], "pe16": [], "pe12": [] 
        }}
    }}
  }},
  "technical": {{
    "status": "...", "signal_light": "red_flash (起漲)/green_flash (起跌)/stable", 
    "analysis_text": "...",
    "predictions": {{ "days30": "..", "days180": "..", "days360": "..", "entry_zone": ".." }},
    "correction_c": "0.XX",
    "bollinger": {{ "status": "..", "description": ".." }}
  }},
  "news_events": {{
    "news": [ {{ "date": "YYYY-MM-DD", "title": "..", "type": "positive/neutral/negative", "is_new": true }} ],
    "calendar": [ {{ "date": "YYYY-MM-DD", "event": ".." }} ]
  }},
  "dividend": {{ "yield": "..", "history_roi": "..", "future_roi": ".." }},
  "memo": ""
}}
"""

def get_current_list():
    if os.path.exists('data.json'):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, list) else []
        except: pass
    return []

def get_stock_data(target_id, old_data=None):
    stock_id = target_id.replace(".TW", "")
    print(f"🚀 分析: {stock_id} ...")
    
    try:
        ticker = yf.Ticker(f"{stock_id}.TW")
        
        # A. 抓取事實 (Facts)
        price = 0; change_pct = "0%"
        change_str = "0"
        
        try:
            fast = ticker.fast_info
            price = fast.get('last_price', 0)
            prev = fast.get('previous_close', 0)
            if price == 0: # 備援
                h = ticker.history(period="5d")
                price = h['Close'].iloc[-1]
                prev = h['Close'].iloc[-2]
            
            if price and prev:
                change = price - prev
                change_str = f"{change:+.2f}"
                change_pct = f"{((change/prev)*100):+.2f}%"
        except: pass

        if price == 0: return None

        # B. 新聞事實
        news_summary = ""
        try:
            for n in ticker.news[:3]:
                t = n.get('title'); d = datetime.datetime.fromtimestamp(n.get('providerPublishTime', 0)).strftime('%Y-%m-%d')
                news_summary += f"- {d}: {t}\n"
        except: pass

        # C. 歷史股價 (For Chart)
        hist = ticker.history(period="1y")
        dates = []; prices = []
        if not hist.empty:
            res = hist['Close'].resample('ME').last().tail(12)
            prices = [round(x,2) for x in res.tolist()]
            dates = [d.strftime('%Y-%m') for d in res.index]

        # D. AI 分析
        ai_res = {}
        model_used = "N/A"
        if GEMINI_API_KEY:
            name = ticker.info.get('longName', stock_id)
            prompt = PROMPT_TEMPLATE.format(name=name, stock_id=stock_id, price=price, change_pct=change_pct, news_summary=news_summary)
            
            for m in MODEL_PRIORITY:
                try:
                    mod = genai.GenerativeModel(m)
                    resp = mod.generate_content(prompt)
                    ai_res = json.loads(resp.text.replace("```json","").replace("```",""))
                    model_used = m.split("/")[-1]
                    break
                except: continue

        # E. 合併資料
        # 防止 AI 沒給數據導致報錯，設定預設結構
        fin = ai_res.get("financials", {})
        val = fin.get("valuation", {})
        riv = val.get("pe_river_data", {})
        
        # 確保河流圖至少有實線 (事實)
        final_river = {
            "dates": dates,
            "price": prices,
            "pe20": riv.get("pe20", [p*1.2 for p in prices]),
            "pe16": riv.get("pe16", [p*1.0 for p in prices]),
            "pe12": riv.get("pe12", [p*0.8 for p in prices])
        }

        return {
            "id": stock_id,
            "name": name if 'name' in locals() else stock_id,
            "category": old_data.get('category', '新加入') if old_data else '新加入',
            "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "ai_model": model_used,
            "memo": old_data.get('memo', '') if old_data else '',
            "basicInfo": { "price": f"{price:.2f}", "change": change_str, "changePercent": change_pct },
            "industry": ai_res.get("industry", {"moat_status":"-", "position_map":"-", "competitors":"-"}),
            "news_events": ai_res.get("news_events", {"news":[], "calendar":[]}),
            "financials": {
                "eps_table": fin.get("eps_table", []),
                "revenue_trend": fin.get("revenue_trend", []),
                "valuation": { "pe_status": val.get("pe_status", "-"), "pb": str(ticker.info.get("priceToBook","-")), "roe": val.get("roe","-"), "pe_river_data": final_river }
            },
            "technical": ai_res.get("technical", { "status":"-", "signal_light":"stable", "analysis_text":"待分析", "predictions":{}, "correction_c":"-", "bollinger":{} }),
            "dividend": ai_res.get("dividend", { "yield":"-", "history_roi":"-", "future_roi":"-" })
        }

    except Exception as e:
        print(f"❌ {stock_id} Error: {e}")
        return None

if __name__ == "__main__":
    current = get_current_list()
    # 建立 Map 保留舊分類
    old_map = {item['id']: item for item in current}
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--add', type=str)
    args = parser.parse_args()

    targets = list(old_map.keys())
    if args.add:
        nid = args.add.strip().upper()
        if nid not in targets:
            targets.insert(0, nid)
            old_map[nid] = {"category": "新加入"} # Dummy placeholder

    final = []
    for sid in targets:
        d = get_stock_data(sid, old_map.get(sid))
        if d: final.append(d)
        else:
            if sid in old_map and 'name' in old_map[sid]: final.append(old_map[sid]) # 失敗回退舊資料
        time.sleep(2)

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
