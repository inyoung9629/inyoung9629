import time
import re
import math
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from difflib import SequenceMatcher
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
    WebDriverException
)

# ============================================================
# [최적화] 속도 및 안정성 설정
# ============================================================
FILE_PATH = "parking_kakao_crawled_300m_최종.csv"  # 원본 파일명
RESULT_FILE_NAME = "parking_integrated_final_최종.csv" # 최종 저장 파일명

GOOGLE_MAPS_URL = "https://www.google.co.kr/maps?hl=ko"

# 무조건 쉬는 대기 시간 최적화 (결과가 누락되지 않는 가장 안정적인 값)
SEARCH_DELAY = 2.5       # 검색창에 입력 후 결과 로딩 대기 (기존 4.5초 -> 2.5초)
CLICK_DELAY = 2.0        # 리스트에서 항목 클릭 후 상세창 로딩 대기 (기존 4.0초 -> 2.0초)
REVIEW_TAB_DELAY = 1.0   # 리뷰 탭 클릭 후 전환 대기 (기존 1.5초 -> 1.0초)
ROW_DELAY = 0.6          # 데이터 한 행(주차장 1개) 처리 후 다음 행으로 가기 전 대기 (기존 1.0초 -> 0.6초)

# 최대 허용 타임아웃 설정
MAX_DISTANCE_M = 300     # 주소와 이름 검색 위치 사이 최대 허용 거리
REVIEW_TAB_TIMEOUT = 6   # 리뷰 탭 탐색 최대 제한 시간 (기존 8초 -> 6초)

# ============================================================
# 1. 파일 및 컬럼 설정
# ============================================================
print(f"📁 [{FILE_PATH}] 파일을 읽어옵니다...")
try:
    df = pd.read_csv(FILE_PATH, encoding='utf-8-sig')
except:
    df = pd.read_csv(FILE_PATH, encoding='cp949')

target_cols = ['카카오맵_별점', '카카오맵_리뷰수', '카카오맵_URL', '카카오맵_매칭된_주차장명', '카카오맵_유사도']
for col in target_cols:
    if col not in df.columns:
        df[col] = ""
    df[col] = df[col].astype('object')

BG_INDEX = 58
if len(df.columns) > BG_INDEX:
    bg_col = df.columns[BG_INDEX]
else:
    bg_col = "카카오맵_후기_URL"
    df[bg_col] = ""
df[bg_col] = df[bg_col].astype("object")


# ============================================================
# 2. 크롬 브라우저 설정 (속도 최적화 모드)
# ============================================================
print("🌐 구글맵 수집용 속도 최적화 브라우저를 설정합니다...")
options = webdriver.ChromeOptions()

# [핵심] 이미지/디자인 로딩 생략 전략으로 크롤링 속도 극대화
options.page_load_strategy = "eager" 

options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
options.add_argument("--start-maximized")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)
driver.set_page_load_timeout(15)
driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
})


# ============================================================
# 3. 핵심 보조 함수
# ============================================================
def handle_google_popups(driver):
    try:
        popup_buttons = driver.find_elements(By.XPATH, "//span[contains(text(), '동의') or contains(text(), '확인') or contains(text(), '나중에') or contains(text(), '닫기')]")
        if popup_buttons:
            driver.execute_script("arguments[0].click();", popup_buttons[0])
            time.sleep(0.5)
    except: pass
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
    except: pass

def wait_page_ready(driver, timeout=10):
    try:
        WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")
    except: pass

def recover_google_maps_home(driver):
    driver.get(GOOGLE_MAPS_URL)
    wait_page_ready(driver, timeout=10)
    time.sleep(2)
    handle_google_popups(driver)

def find_visible_google_maps_search_box(driver, timeout=10):
    candidates = [
        (By.ID, "searchboxinput"),
        (By.CSS_SELECTOR, "input[aria-label*='Google 지도 검색']"),
        (By.CSS_SELECTOR, "input[placeholder*='Google 지도 검색']"),
        (By.CSS_SELECTOR, "input[role='combobox']"),
    ]
    end_time = time.time() + timeout
    while time.time() < end_time:
        for by, selector in candidates:
            try:
                elements = driver.find_elements(by, selector)
                for el in elements:
                    if el.is_displayed() and el.is_enabled():
                        return el
            except: continue
        time.sleep(0.3)
    raise TimeoutException("검색창을 찾지 못했습니다.")

def search_google_maps_directly(driver, keyword, max_retries=3):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            search_box = find_visible_google_maps_search_box(driver, timeout=8)
            try:
                driver.execute_script("arguments[0].click(); arguments[0].focus();", search_box)
            except:
                ActionChains(driver).move_to_element(search_box).click().perform()
            time.sleep(0.3)

            search_box.send_keys(Keys.CONTROL + "a")
            search_box.send_keys(Keys.BACKSPACE)
            time.sleep(0.2)
            search_box.send_keys(keyword)
            time.sleep(0.3)
            search_box.send_keys(Keys.RETURN)
            
            # [최적화 적용] 검색 후 대기 시간 단축
            time.sleep(SEARCH_DELAY)
            return True
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                recover_google_maps_home(driver)
    raise last_error

def extract_lat_lng_from_url(url):
    try:
        match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
        if match: return float(match.group(1)), float(match.group(2))
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if match: return float(match.group(1)), float(match.group(2))
    except: pass
    return None

def haversine_distance_m(coord1, coord2):
    if not coord1 or not coord2: return None
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

def get_road_address_from_row(row):
    try:
        val = str(row.iloc[1]).strip()
        if val and val.lower() not in ['nan', 'none', '']: return val
    except: pass
    return ""

def wait_place_detail_panel(driver, timeout=4):
    try:
        title = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1.DUwDvf")))
        if title.is_displayed(): return True
    except: pass
    return False

def click_review_tab_if_exists(driver, timeout=6):
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            if not wait_place_detail_panel(driver, timeout=1.5):
                time.sleep(0.2)
                continue
            xpaths = [
                "//h1[contains(@class, 'DUwDvf')]/following::button[@role='tab' and .//*[normalize-space(text())='리뷰']][1]",
                "//h1[contains(@class, 'DUwDvf')]/following::button[@role='tab' and normalize-space(.)='리뷰'][1]",
                "//button[@role='tab' and @aria-label='리뷰']"
            ]
            for xpath in xpaths:
                elements = driver.find_elements(By.XPATH, xpath)
                for el in elements:
                    text = el.text.strip()
                    aria = el.get_attribute("aria-label") or ""
                    if text != "리뷰" and aria.strip() != "리뷰": continue
                    if not el.is_displayed() or not el.is_enabled(): continue
                    
                    try: driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                    except: pass
                    
                    try: driver.execute_script("arguments[0].click();", el)
                    except: ActionChains(driver).move_to_element(el).click().perform()
                    
                    # [최적화 적용] 리뷰 탭 클릭 후 대기 시간 단축
                    time.sleep(REVIEW_TAB_DELAY)
                    return True
        except: pass
        time.sleep(0.2)
    return False


# ============================================================
# 4. 메인 크롤링 (통합 로직)
# ============================================================
print("📍 구글 지도 공식 페이지 접속 중...")
driver.get(GOOGLE_MAPS_URL)
wait_page_ready(driver, timeout=15)
time.sleep(4)
handle_google_popups(driver)

print("\n🔍 [통합 고속 프로토콜] 안전 보장형 하이퍼 크롤링을 시작합니다.")
print("=" * 80)

processed_count = 0

for index, row in df.iterrows():
    current_status = str(row.get('카카오맵_별점', '')).strip()
    if current_status.lower() in ['nan', 'none']:
        current_status = ''
        
    if current_status not in ['조건 불일치 제외', '서울API', '']:
        continue

    raw_name = str(row['pk_name'])
    clean_name = re.sub(r'\(.*?\)', '', raw_name).replace(" ", "")
    if "주차" not in clean_name:
        clean_name += "주차장"

    print(f"\n[구글맵 통합 검색 {processed_count + 1}번째] 원본: {raw_name} ➡️ 타겟: {clean_name} (기존상태: '{current_status if current_status else '빈칸'}')")

    check_name = str(raw_name) + " " + str(clean_name)
    if "버스" in check_name or "화물" in check_name:
        print("   ⏭️ [검색 제외] 이름에 '버스/화물' 포함 -> 조건 불일치 제외")
        df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
        processed_count += 1
        continue

    if "/maps/place/" in driver.current_url:
        recover_google_maps_home(driver)

    try:
        # [단계 1: 주소 선검색]
        road_address = get_road_address_from_row(row)
        address_coord = None
        if road_address:
            try:
                search_google_maps_directly(driver, road_address, max_retries=1)
                address_coord = extract_lat_lng_from_url(driver.current_url)
            except: pass

        # [단계 2: 이름 검색 및 판별]
        search_google_maps_directly(driver, clean_name, max_retries=1)

        is_direct_page = wait_place_detail_panel(driver, timeout=2.5)
        final_element_clicked = False
        best_title = ""
        max_ratio = 0.0

        if is_direct_page:
            best_title = driver.find_element(By.CSS_SELECTOR, "h1.DUwDvf").text.strip()
            max_ratio = SequenceMatcher(None, clean_name, best_title.replace(" ", "")).ratio()
            final_element_clicked = True
        else:
            search_results = driver.find_elements(By.CSS_SELECTOR, "a.hfpxzc")
            if not search_results:
                search_results = driver.find_elements(By.CSS_SELECTOR, "[role='feed'] a[href*='/maps/place/']")
            
            best_item = None
            if search_results:
                for item in search_results:
                    try:
                        title = item.get_attribute("aria-label")
                        if not title: continue
                        title = title.strip()
                        ratio = SequenceMatcher(None, clean_name, title.replace(" ", "")).ratio()
                        if ratio > max_ratio:
                            max_ratio = ratio
                            best_item = item
                            best_title = title
                    except: continue
                
                if best_item:
                    try:
                        driver.execute_script("arguments[0].click();", best_item)
                        # [최적화 적용] 리스트 항목 클릭 후 대기 시간 단축
                        time.sleep(CLICK_DELAY)
                        final_element_clicked = True
                    except: pass

        # [단계 3: 거리 및 폐업 검증]
        name_coord = extract_lat_lng_from_url(driver.current_url) if final_element_clicked else None
        distance_m = haversine_distance_m(address_coord, name_coord)
        
        is_closed = False
        try:
            panel_text = driver.find_element(By.CSS_SELECTOR, "div[role='main']").text
            if "폐업" in panel_text:
                is_closed = True
        except: pass

        # 최종 평가 및 기록
        if not final_element_clicked:
            print("   ❌ [매칭 실패] 검색 결과 없음 또는 클릭 실패")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
            
        elif distance_m is not None and distance_m > MAX_DISTANCE_M:
            print(f"   ❌ [거리 초과] {distance_m:.1f}m로 기준 {MAX_DISTANCE_M}m 초과")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
            
        elif max_ratio < 0.7:
            print(f"   ❌ [유사도 부족] 유사도 {max_ratio * 100:.1f}%로 미달 (이름: {best_title})")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
            
        elif "주차" not in best_title and "주차장" not in best_title:
            print(f"   ❌ [키워드 누락] 구글 결과 중 '주차' 단어 없음 (이름: {best_title})")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"

        elif is_closed:
            print(f"   ❌ [폐업 상태] 검색된 장소가 '폐업' 상태입니다. (이름: {best_title})")
            df.at[index, '카카오맵_별점'] = "조건 불일치 제외"

        else:
            g_star, g_rev = "평가 없음", "0"
            try:
                rating_zone_text = driver.find_element(By.CSS_SELECTOR, "div.F7nice").text.strip()
                rating_match = re.search(r'([0-9\.]+)', rating_zone_text)
                review_match = re.search(r'\(([0-9,]+)\)', rating_zone_text)
                if rating_match: g_star = rating_match.group(1)
                if review_match: g_rev = review_match.group(1).replace(',', '')
            except: pass

            print(f"   🟢 [매칭성공] {best_title} | 별점: {g_star} | 리뷰수: {g_rev}개 | 거리: {distance_m if distance_m else '측정불가'}m")

            place_base_url = driver.current_url
            
            df.at[index, '카카오맵_매칭된_주차장명'] = best_title
            df.at[index, '카카오맵_유사도'] = f"{max_ratio * 100:.1f}%"
            df.at[index, '카카오맵_별점'] = g_star
            df.at[index, '카카오맵_리뷰수'] = g_rev
            df.at[index, '카카오맵_URL'] = place_base_url

            print("   🔗 [리뷰 URL 수집] '리뷰' 탭 전용 링크 수집을 시도합니다...")
            review_clicked = click_review_tab_if_exists(driver, timeout=REVIEW_TAB_TIMEOUT)

            if review_clicked:
                review_url = driver.current_url
                df.at[index, bg_col] = review_url
                print(f"   ✅ [저장 완료] 기본 URL 및 리뷰 URL(BG열) 모두 확보")
            else:
                df.at[index, bg_col] = ""
                print("   ⏭️ [리뷰 탭 없음] 리뷰 탭을 누르지 못해 기본 URL만 저장했습니다.")

    except Exception as e:
        print(f"   ⚠️ [크롤링 지연] 에러 발생 (사유: {e})")
        df.at[index, '카카오맵_별점'] = "조건 불일치 제외"

    processed_count += 1
    
    # [최적화 적용] 행 사이 대기 시간 단축
    time.sleep(ROW_DELAY)

print("=" * 80)
print(f"🎉 고속 통합 크롤링 작업 종료! 총 {processed_count}개의 항목 처리가 완료되었습니다.")

driver.quit()

# ============================================================
# 5. 최종 결과 저장
# ============================================================
df.to_csv(RESULT_FILE_NAME, index=False, encoding='utf-8-sig')
print(f"💾 최종 보완된 데이터가 [{RESULT_FILE_NAME}]으로 안전하게 저장되었습니다.")