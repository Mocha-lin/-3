import yfinance as yf
import google.generativeai as genai
import json
import os
from datetime import datetime

MY_API_KEY = os.getenv("GEMINI_API_KEY") 
STOCK_LIST = ["2330", "2317", "2454"] 

def run_automated_analysis():
    genai.configure(api_key=MY_API_KEY)
    
    # 找尋可用模型
    valid_model_name = "models/gemini-1.5-flash"
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods and 'flash' in m.name:
                valid_model_name = m.name
                break
    except: pass
    model = genai.GenerativeModel(valid_model_name)

    all_results = {}

    for sid in STOCK_LIST:
        full_id = f"{sid}.TW"
        ticker = yf.Ticker(full_id)
        
        # --- 修正後的 EPS 抓取邏輯 (防護機制) ---
        eps_trend = []
        try:
            earnings = ticker.earnings
            if earnings is not None and not earnings.empty:
                for idx, row in earnings.iterrows():
                    eps_trend.append({"year": str(idx), "eps": row.get('Earnings', 0)})
        except Exception as e:
            print(f"⚠️ 無法抓取 {sid} 的 EPS 歷史數據: {e}")
        # --------------------------------------

        price = ticker.fast_info.get('last_price', 0)
        
        prompt = f"你是分析師 bbb，針對 {full_id} 現價 {price} 提供 JSON 分析：trend_status, calendar, technical。"
        response = model.generate_content(prompt)
        ai_data = json.loads(response.text.replace('```json', '').replace('```', '').strip())

        all_results[sid] = {
            **ai_data,
            "id": sid,
            "name": ticker.info.get('longName', sid),
            "price": round(price, 2),
            "eps_trend": eps_trend,
            "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M")
        }

    # 確保寫入正確的檔名 (data.json)
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    run_automated_analysis()
