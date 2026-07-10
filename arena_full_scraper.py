import csv
import json
import os
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

STATUS_FILE = "web_data.json"
TARGET_FILE = "targets.csv"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def load_target_list():
    targets = {}
    if not os.path.exists(TARGET_FILE): return targets
    with open(TARGET_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            targets[row['iidx_id'].strip()] = {"custom_name": row['player_name'].strip(), "memo": row['memo'].strip()}
    return targets

def parse_arena_ranking(html_content, arena_ranking):
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    if len(tables) == 0: return
    for row in tables[0].find_all('tr'):
        cols = row.find_all('td')
        if len(cols) >= 7:
            try:
                name_id = cols[1].get_text(separator='\n', strip=True).split('\n')
                if len(name_id) < 2: continue
                wins = int(cols[6].get_text(strip=True).replace('勝', '').replace(',', ''))
                arena_ranking[name_id[1].strip()] = {"dj_name": name_id[0].strip(), "wins": wins}
            except Exception: pass

def parse_cube_ranking(html_content, cube_ranking):
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    if len(tables) == 0: return
    for row in tables[0].find_all('tr'):
        cols = row.find_all('td')
        if len(cols) >= 5:
            try:
                cubes = int(cols[4].get_text(strip=True).replace(',', ''))
                cube_ranking[cols[1].get_text(strip=True)] = cubes
            except Exception: pass

def main():
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    now_str = now.strftime(DATE_FORMAT)
    
    target_list = load_target_list()
    if not target_list: 
        print("❌ ターゲットリストが空です。")
        return
        
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            prev_players = json.load(f).get("players", {})
    except FileNotFoundError:
        prev_players = {}
    
    arena_ranking = {}
    cube_ranking = {}
    max_pages = 5 

    with sync_playwright() as p:
        # 本番運用のためにheadless=Trueに戻します
        browser = p.chromium.launch(headless=True) 
        
        # 💡 【対策】ブラウザのコンテキストを作成し、画面サイズをPCサイズ(1280x800)に強制固定
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            # ーー 1. アリーナランキング巡回 ーー
            url_arena = "https://p.eagate.573.jp/game/2dx/33/ranking/arena/top_ranking.html"
            print("🌐 アリーナランキングにアクセス中...")
            page.goto(url_arena, wait_until="networkidle")
            page.wait_for_timeout(1000)
            page.locator("li[data-play-style='0']").click()
            page.wait_for_timeout(1000)
            
            for i in range(max_pages):
                parse_arena_ranking(page.content(), arena_ranking)
                if i < max_pages - 1:
                    # 💡 【対策】万が一ボタンが無くても無限フリーズしないよう、10秒のタイムアウトを設定
                    page.locator("div.page-next").first.click(timeout=10000)
                    page.wait_for_timeout(2000) 
                    
            # ーー 2. キューブランキング巡回 ーー
            url_cube = "https://p.eagate.573.jp/game/2dx/33/ranking/arena/ranking.html?season_id=5&display=1"
            print("🌐 キューブランキングにアクセス中...")
            page.goto(url_cube, wait_until="networkidle")
            page.wait_for_timeout(1000)
            
            for i in range(max_pages):
                parse_cube_ranking(page.content(), cube_ranking)
                if i < max_pages - 1:
                    page.locator("div.page-next").first.click(timeout=10000)
                    page.wait_for_timeout(2000) 
                    
            print("✅ データの抽出が正常に完了しました。")
            
        except Exception as e:
            print(f"⚠️ エラーが発生したため、途中で処理をスキップします: {e}")
        finally:
            browser.close()

    # 3. オンライン判定
    output_players = {}
    for target_id, info in target_list.items():
        if target_id in arena_ranking:
            current_arena = arena_ranking[target_id]
            official_name = current_arena["dj_name"]
            current_wins = current_arena["wins"]
            
            is_cube_known = official_name in cube_ranking
            current_cubes = cube_ranking[official_name] if is_cube_known else None
            
            prev_info = prev_players.get(target_id, {})
            prev_wins = prev_info.get("wins", current_wins)
            prev_cubes = prev_info.get("cubes", current_cubes)
            last_active_str = prev_info.get("last_active", "データなし")
            
            is_active_now = False
            if is_cube_known and current_cubes is not None and prev_cubes is not None:
                if current_cubes > prev_cubes: is_active_now = True
            else:
                if current_wins > prev_wins: is_active_now = True
            
            if is_active_now: last_active_str = now_str
            
            status = "OFFLINE"
            if last_active_str != "データなし":
                try:
                    last_active_dt = datetime.strptime(last_active_str, DATE_FORMAT).replace(tzinfo=JST)
                    diff_minutes = (now - last_active_dt).total_seconds() / 60
                    if diff_minutes <= 15: status = "ONLINE"
                    elif diff_minutes <= 60: status = "RECENT"
                except ValueError: pass
            
            output_players[target_id] = {
                "official_name": official_name, "custom_name": info["custom_name"], "memo": info["memo"],
                "wins": current_wins, "cubes": current_cubes, "status": status, "last_active": last_active_str
            }
        else:
            output_players[target_id] = {
                "official_name": "None", "custom_name": info["custom_name"], "memo": info["memo"],
                "wins": 0, "cubes": None, "status": "UNKNOWN (圏外)", "last_active": "データなし"
            }

    web_data = {"last_updated": now_str, "players": output_players}
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(web_data, f, ensure_ascii=False, indent=4)
    print(f"📝 {STATUS_FILE} の更新が完了しました。")

if __name__ == "__main__":
    main()
