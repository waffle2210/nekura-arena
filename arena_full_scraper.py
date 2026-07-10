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
            targets[row['iidx_id'].strip()] = {"player_name": row['player_name'].strip(), "memo": row['memo'].strip()}
    return targets

def parse_arena_ranking(html_content, arena_ranking):
    """1. アリーナランキングから ID -> [DJ NAME, エリア, 段位, 勝利数] を抽出"""
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    if len(tables) == 0: return
    for row in tables[0].find_all('tr'):
        cols = row.find_all('td')
        if len(cols) >= 7:
            try:
                name_id = cols[1].get_text(separator='\n', strip=True).split('\n')
                if len(name_id) < 2: continue
                dj_name = name_id[0].strip()
                iidx_id = name_id[1].strip()
                
                # ★エリアと段位（SP/DP）を抽出して整形
                area = cols[2].get_text(strip=True)
                rank = cols[3].get_text(separator='/', strip=True) # 例: "中伝/十段" 
                
                wins = int(cols[6].get_text(strip=True).replace('勝', '').replace(',', ''))
                
                arena_ranking[iidx_id] = {
                    "dj_name": dj_name, 
                    "area": area,
                    "rank": rank,
                    "wins": wins
                }
            except Exception: pass

def parse_cube_ranking(html_content, cube_ranking):
    """2. キューブランキングから [DJ NAME + エリア + 段位] の複合キー -> キューブ数を抽出"""
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    if len(tables) == 0: return
    for row in tables[0].find_all('tr'):
        cols = row.find_all('td')
        if len(cols) >= 5:
            try:
                dj_name = cols[1].get_text(strip=True)
                area = cols[2].get_text(strip=True)
                rank = cols[3].get_text(separator='/', strip=True) # 画像の改行をスラッシュに変換
                cubes = int(cols[4].get_text(strip=True).replace(',', ''))
                
                # ★「名前＿エリア＿段位」で絶対に被らない複合キーを作成
                match_key = f"{dj_name}_{area}_{rank}"
                
                if match_key not in cube_ranking:
                    cube_ranking[match_key] = {"cubes": cubes, "is_duplicate": False}
                else:
                    # 万が一、エリアも段位も名前もすべて丸被りした人がいたらロックをかける
                    cube_ranking[match_key]["is_duplicate"] = True
            except Exception: pass

def main():
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    now_str = now.strftime(DATE_FORMAT)
    
    target_list = load_target_list()
    if not target_list: return
        
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            prev_players = json.load(f).get("players", {})
    except FileNotFoundError:
        prev_players = {}
    
    arena_ranking = {}
    cube_ranking = {}
    max_pages = 5 

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        
        try:
            # ーー 巡回1: アリーナランキング ーー
            url_arena = "https://p.eagate.573.jp/game/2dx/33/ranking/arena/top_ranking.html"
            page.goto(url_arena, wait_until="networkidle")
            page.wait_for_timeout(1000)
            page.locator("li[data-play-style='0']").click()
            page.wait_for_timeout(1000)
            for i in range(max_pages):
                parse_arena_ranking(page.content(), arena_ranking)
                if i < max_pages - 1:
                    page.locator("div.page-next").first.click(timeout=10000)
                    page.wait_for_timeout(2000) 
                    
            # ーー 巡回2: キューブランキング ーー
            url_cube = "https://p.eagate.573.jp/game/2dx/33/ranking/arena/ranking.html?season_id=5&display=1"
            page.goto(url_cube, wait_until="networkidle")
            page.wait_for_timeout(1000)
            for i in range(max_pages):
                parse_cube_ranking(page.content(), cube_ranking)
                if i < max_pages - 1:
                    page.locator("div.page-next").first.click(timeout=10000)
                    page.wait_for_timeout(2000) 
        except Exception as e:
            print(f"⚠️ 巡回エラー: {e}")
        finally:
            browser.close()

    # 3. オンライン判定（3連複合キー突合）
    output_players = {}
    for target_id, info in target_list.items():
        if target_id in arena_ranking:
            current_arena = arena_ranking[target_id]
            official_name = current_arena["dj_name"]
            area = current_arena["area"]
            rank = current_arena["rank"]
            current_wins = current_arena["wins"]
            
            # ★ アリーナ側から作ったキーで、キューブランキング側を検索
            match_key = f"{official_name}_{area}_{rank}"
            cube_info = cube_ranking.get(match_key)
            
            # キーが完全に一致し、かつそのキーが重複していない場合のみキューブ数を採用
            is_cube_valid = cube_info is not None and not cube_info["is_duplicate"]
            current_cubes = cube_info["cubes"] if is_cube_valid else None
            
            prev_info = prev_players.get(target_id, {})
            prev_wins = prev_info.get("wins", current_wins)
            prev_cubes = prev_info.get("cubes", current_cubes)
            last_active_str = prev_info.get("last_active", "データなし")
            
            is_active_now = False
            
            if is_cube_valid and current_cubes is not None and prev_cubes is not None:
                # 3項目が完全一致した本人のキューブ数変化で判定
                if current_cubes > prev_cubes: 
                    is_active_now = True
                    print(f"🔥 キューブ変動検知: {official_name}({area}/{rank}) がプレイしました！")
            else:
                # キューブ側でまだマッチしない（開催回で未プレイ・圏外）場合は、100%安全なID直結の勝利数で判定
                if current_wins > prev_wins: 
                    is_active_now = True
                    print(f"🔥 勝利数変動検知: {official_name}({area}/{rank}) がプレイしました！")
            
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
                "official_name": official_name, "custom_name": info["player_name"], "memo": info["memo"],
                "wins": current_wins, "cubes": current_cubes, "status": status, "last_active": last_active_str
            }
        else:
            output_players[target_id] = {
                "official_name": "None", "custom_name": info["player_name"], "memo": info["memo"],
                "wins": 0, "cubes": None, "status": "UNKNOWN (圏外)", "last_active": "データなし"
            }

    web_data = {"last_updated": now_str, "players": output_players}
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(web_data, f, ensure_ascii=False, indent=4)
    print(f"📝 3項目複合マッチング版で {STATUS_FILE} を更新しました。")

if __name__ == "__main__":
    main()
