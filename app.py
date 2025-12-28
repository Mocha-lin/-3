import yfinance as yf
import google.generativeai as genai
import json
import os
from datetime import datetime

# 從 GitHub Secrets 讀取金鑰
MY_API_KEY = os.getenv("GEMINI_API_KEY") 
# 你想要自動追蹤的股票清單
STOCK_LIST = ["2330", "2317", "2454"] 

def run_automated_analysis():
    # 檢查 API KEY 是否存在
    if not MY_API_KEY:
        print("❌ 錯誤: 找不到 GEMINI_API_KEY，請檢查 GitHub Secrets 設定。")
        return

    genai.configure(api_key=MY_API_KEY)
    
    # 尋找可用模型 (自動偵測)
    valid_model_name = "models/gemini-1.5-flash"
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods and 'flash' in m.name:
                valid_model_name = m.name
                break
    except Exception:
        pass
    
    model = genai.GenerativeModel(valid_model_name)
    all_results = {}

    for sid in STOCK_LIST:
        print(f"📦 正在分析 {sid}...")
        full_id = f"{sid}.TW"
        ticker = yf.Ticker(full_id)
        
        # --- 增強版 EPS 抓取 (防止 NoneType 錯誤導致程式崩潰) ---
        eps_trend = []
        try:
            # 使用更穩定的方式檢查數據
            earnings = getattr(ticker, 'earnings', None)
            if earnings is not None and hasattr(earnings, 'empty') and not earnings.empty:
                for idx, row in earnings.iterrows():
                    eps_trend.append({"year": str(idx), "eps": row.get('Earnings', 0)})
            else:
                print(f"ℹ️ {sid} 目前沒有可用的 EPS 歷史數據。")
        except Exception as e:
            print(f"⚠️ 抓取 {sid} EPS 時發生跳過: {e}")
        # -----------------------------------------------------

        # 抓取現價
        price = 0
        try:
            price = ticker.fast_info.get('last_price', 0)
        except:
            price = 0
        
        # 呼叫 AI 產出戰情室分析
        try:
            prompt = f"你是分析師 bbb，針對 {full_id} 現價 {price} 提供 JSON 分析，包含：trend_status, calendar(未來三個月事件), technical(技術簡評)。格式請嚴格遵守 JSON。"
            response = model.generate_content(prompt)
            # 清洗 AI 回傳的字串
            clean_json = response.text.replace('```json', '').replace('```', '').strip()
            ai_data = json.loads(clean_json)

            all_results[sid] = {
                **ai_data,
                "id": sid,
                "name": ticker.info.get('longName', sid),
                "price": round(price, 2),
                "eps_trend": eps_trend,
                "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
        except Exception as e:
            print(f"❌ AI 分析 {sid} 時出錯: {e}")

    # 確保寫入正確命名的 data.json
    print(f"💾 正在儲存數據到 data.json...")
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print("✅ 全部完成！")

if __name__ == "__main__":
    run_automated_analysis()
