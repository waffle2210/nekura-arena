import csv
import json
import os
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

STATUS_FILE = "web_data.json"
TARGET_FILE = "targets.csv"

def load_target_list():
    """CSVからターゲットリストを読み込む"""
    targets = {}
    if not os.path.exists(TARGET_FILE):
        print(f"【警告】{TARGET_FILE} が見つかりません。")
        return targets
        
    with open(TARGET_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # IIDX IDをキーにして、名前と備考を保持
            targets[row['iidx_id'].strip()] = {
                "custom_name": row['player_name'].strip(),
                "memo": row['memo'].strip()
            }
    return targets

def parse_html_content(html_content, current_ranking):
    """HTMLから最新の戦績を抽出"""
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    if len(tables) == 0:
        return

    sp_table = tables[0] 
    rows = sp_table.find_all('tr')
    
    for row in rows:
        cols = row.find_all('td')
        if len(cols) >= 7:
            try:
                name_id_text = cols[1].get_text(separator='\n', strip=True).split('\n')
                if len(name_id_text) < 2:
                    continue
                dj_name = name_id_text[0]
                iidx_id = name_id_text[1]
                
                wins_str = cols[6].get_text(strip=True)
                wins = int(wins_str.replace('勝', '').replace(',', ''))

                current_ranking[iidx_id] = {
                    "dj_name": dj_name,
                    "wins": wins
                }
            except Exception:
                pass

def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 監視バッチを開始します...")
    
    # 1. 外部CSVファイルと前回の結果のロード
    target_list = load_target_list()
    if not target_list:
        print("監視対象のIDがありません。処理を終了します。")
        return
        
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            prev_players = json.load(f).get("players", {})
    except FileNotFoundError:
        prev_players = {}
    
    # 2. 最新ランキングのスクレイピング (5ページ分)
    current_ranking = {}
    max_pages = 5 

    with sync_playwright() as p:
        # GitHub Actions上で動かすため headless=True に固定
        browser = p.chromium.launch(headless=True) 
        page = browser.new_page()
        
        url = "https://p.eagate.573.jp/game/2dx/33/ranking/arena/top_ranking.html"
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(1000)
        
        page.locator("li[data-play-style='0']").click()
        page.wait_for_timeout(1000) 
        
        for current_page in range(1, max_pages + 1):
            html_content = page.content()
            parse_html_content(html_content, current_ranking)
            
            if current_page < max_pages:
                page.locator("div.page-next").first.click()
                page.wait_for_timeout(2000) 
        
        browser.close()

    # 3. 差分チェックとデータ統合
    output_players = {}
    
    for target_id, info in target_list.items():
        if target_id in current_ranking:
            current_info = current_ranking[target_id]
            current_wins = current_info["wins"]
            official_name = current_info["dj_name"]
            
            prev_info = prev_players.get(target_id, {})
            prev_wins = prev_info.get("wins", current_wins)
            prev_status = prev_info.get("status", "OFFLINE")
            last_active = prev_info.get("last_active", "データなし")
            
            if current_wins > prev_wins:
                status = "ONLINE"
                last_active = now_str
            else:
                status = prev_status 
            
            output_players[target_id] = {
                "official_name": official_name,          # 公式のDJ NAME
                "custom_name": info["custom_name"],      # CSVで設定した名前
                "memo": info["memo"],                    # CSVで設定した備考
                "wins": current_wins,
                "status": status,
                "last_active": last_active
            }
        else:
            output_players[target_id] = {
                "official_name": "圏外",
                "custom_name": info["custom_name"],
                "memo": info["memo"],
                "wins": 0,
                "status": "UNKNOWN (圏外)",
                "last_active": "データなし"
            }

    # 4. Web用JSON出力
    web_data = {
        "last_updated": now_str,
        "players": output_players
    }
    
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(web_data, f, ensure_ascii=False, indent=4)
        
    print(f"[{now_str}] 更新完了。")

if __name__ == "__main__":
    main()