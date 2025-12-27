import yfinance as yf
import google.generativeai as genai
import json
import os
from datetime import datetime

# --- 設定區 (GitHub Secrets 會自動帶入) ---
MY_API_KEY = os.getenv("GEMINI_API_KEY") 
STOCK_LIST = ["2330", "2317", "2454"]  # 你可以列出所有想追蹤的代號

def run_analysis():
    genai.configure(api_key=MY_API_KEY)
    
    # 自動偵測模型邏輯
    valid_model_name = "models/gemini-1.5-flash"
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods and 'flash' in m.name:
                valid_model_name = m.name
                break
    except: pass
    model = genai.GenerativeModel(valid_model_name)

    all_data = {}

    for stock_id in STOCK_LIST:
        print(f"📦 正在處理 {stock_id}...")
        ticker = yf.Ticker(f"{stock_id}.TW")
        
        # 抓取 EPS 歷史數據 (趨勢圖用)
        # 取得最近四年的年度 EPS
        earnings = ticker.earnings
        eps_trend = []
        if not earnings.empty:
            for index, row in earnings.iterrows():
                eps_trend.append({"year": str(index), "eps": row['Earnings']})

        # 基礎資訊與新聞
        price = ticker.fast_info.get('last_price', 0)
        raw_news = ticker.news
        
        prompt = f"你是 bbb 分析師，針對 {stock_id} 現價 {price} 提供 JSON 分析：trend_status, calendar, technical。"
        response = model.generate_content(prompt)
        ai_data = json.loads(response.text.replace('```json', '').replace('```', '').strip())

        # 整合所有資訊
        all_data[stock_id] = {
            **ai_data,
            "id": stock_id,
            "name": ticker.info.get('longName', stock_id),
            "price": round(price, 2),
            "eps_trend": eps_trend,  # 這是給 Chart.js 用的數據
            "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M")
        }

    # 儲存為檔案，讓 GitHub Actions 可以 commit
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print("✅ data.json 更新完成")

if __name__ == "__main__":
    run_analysis()
