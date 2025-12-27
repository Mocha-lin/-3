import yfinance as yf
import google.generativeai as genai
import json
import os
from datetime import datetime

# 從 GitHub Secrets 安全讀取金鑰
MY_API_KEY = os.getenv("GEMINI_API_KEY") 
# 你想要自動追蹤的股票清單
STOCK_LIST = ["2330", "2317", "2454"] 

def run_automated_analysis():
    genai.configure(api_key=MY_API_KEY)
    
    # 自動尋找可用模型
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
        
        # 抓取數據與 EPS 趨勢
        price = ticker.fast_info.get('last_price', 0)
        # 抓取年度盈餘數據
        earnings = ticker.earnings
        eps_trend = [{"year": str(idx), "eps": row['Earnings']} for idx, row in earnings.iterrows()] if not earnings.empty else []

        # 叫 AI 產生分析
        prompt = f"你是分析師 bbb，針對 {full_id} 現價 {price} 提供 JSON 分析：trend_status, calendar, technical。"
        response = model.generate_content(prompt)
        ai_data = json.loads(response.text.replace('```json', '').replace('```', '').strip())

        # 整合資料
        all_results[sid] = {
            **ai_data,
            "id": sid,
            "name": ticker.info.get('longName', sid),
            "price": round(price, 2),
            "eps_trend": eps_trend,
            "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M")
        }

    # 關鍵步驟：直接寫入檔案
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    run_automated_analysis()
