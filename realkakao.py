import time   
import re
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from difflib import SequenceMatcher

# 1. 원본 파일 읽기
file_path = "parking_raw_20260628_0108.csv"
print(f"📁 [{file_path}] 파일을 읽어옵니다...")

try:
    df = pd.read_csv(file_path, encoding='utf-8-sig')
except:
    df = pd.read_csv(file_path, encoding='cp949')

# 결과 컬럼 정의
new_cols = ['카카오맵_매칭된_주차장명', '카카오맵_유사도', '카카오맵_별점', '카카오맵_별점참여건수', '카카오맵_URL', '카카오맵_후기_URL']
for col in new_cols:
    if col not in df.columns:
        df[col] = ""

print("🌐 브라우저를 실행합니다...")
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service)

print("\n🔍 [정밀 프로토콜 + 300m 반경 + 버스/화물 사전제외 + 본 작업 모드] 크롤링 시작")
print("=" * 80)

processed_count = 0  

for index, row in df.iterrows():
    # Y열이 '카카오'인 항목만 타겟팅
    if str(row.get('coord_src', '')).strip() != '카카오':
        continue

    raw_name = str(row['pk_name'])
    
    # 🌟 [핵심 추가] 버스 및 화물 전용 주차장 사전 제외 필터링 (시간 단축)
    if "버스" in raw_name or "화물" in raw_name:
        print(f"\n[⏭️ 제외] {raw_name} ➡️ 버스/화물 전용 주차장으로 판단되어 조건 제외 처리합니다.")
        df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
        continue # 카카오맵 창을 켜지 않고 바로 다음 데이터(행)로 넘어갑니다.

    lat = str(row['latitude']).strip()
    lng = str(row['longitude']).strip()
    
    # 괄호/내용물 제거 및 띄어쓰기 제거
    clean_name = re.sub(r'\(.*?\)', '', raw_name).replace(" ", "")
    if "주차" not in clean_name:
        clean_name += "주차장"
        
    print(f"\n[진행률: {processed_count + 1}번째 검색] 원본: {raw_name} ➡️ 타겟명: {clean_name}")

    try:
        driver.get("https://map.kakao.com/")
        
        search_box = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "search.keyword.query"))
        )
        driver.execute_script("arguments[0].click();", search_box)
        
        # ➡️ 1단계: 위경도 검색 (지도 이동)
        search_box.send_keys(Keys.CONTROL + "a")
        search_box.send_keys(Keys.BACKSPACE)
        search_box.send_keys(f"{lat}, {lng}")
        search_box.send_keys(Keys.RETURN)
        print(f"   📍 [1단계] 위경도 좌표 검색 완료 (지도 이동)")
        time.sleep(2.5) 
        
        # 🔥 300m 반경 줌인 무적 로직
        try:
            zoom_success = False
            zoom_selectors = [
                "button[title='확대']", 
                ".btn_zoom_in", 
                ".zoom_in", 
                "div.zoomControl button:nth-child(1)",
                ".ico_plus"
            ]
            
            for selector in zoom_selectors:
                btns = driver.find_elements(By.CSS_SELECTOR, selector)
                if btns and btns[0].is_displayed():
                    driver.execute_script("arguments[0].click();", btns[0])
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", btns[0])
                    print(f"   🔍 [반경 제한] 버튼({selector}) 클릭으로 지도를 확대했습니다.")
                    zoom_success = True
                    break
            
            if not zoom_success:
                print("   🔍 [반경 제한] 화면 중앙에서 마우스 휠을 굴려 지도를 강제 확대합니다.")
                for _ in range(2):
                    driver.execute_script("""
                        var mapLayer = document.querySelector('.view_map') || document.body;
                        var wheelEvent = new WheelEvent('wheel', {
                            deltaY: -500, 
                            clientX: window.innerWidth / 2,
                            clientY: window.innerHeight / 2,
                            bubbles: true
                        });
                        mapLayer.dispatchEvent(wheelEvent);
                    """)
                    time.sleep(0.5)
                    
        except Exception as zoom_err:
            print(f"   ⚠️ [반경 제한 우회] 줌인 에러 패스: {zoom_err}")

        # ➡️ 2단계: '현 지도 내 장소검색' 활성화
        try:
            bound_label = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//label[contains(text(), '현 지도 내 장소검색')]"))
            )
            checkbox_id = bound_label.get_attribute("for")
            bound_checkbox = driver.find_element(By.ID, checkbox_id)
            
            if not bound_checkbox.is_selected():
                driver.execute_script("arguments[0].click();", bound_label)
                print("   📍 [2단계] '현 지도 내 장소검색' 활성화 완료")
                time.sleep(1)
        except:
            print("   📍 [2단계] 장소검색 버튼 활성화 패스 (이미 켜져있음)")

        # ➡️ 3단계: 주차장 이름 최종 검색
        search_box = driver.find_element(By.ID, "search.keyword.query")
        driver.execute_script("arguments[0].click();", search_box)
        search_box.send_keys(Keys.CONTROL + "a")
        search_box.send_keys(Keys.BACKSPACE)
        search_box.send_keys(clean_name)
        search_box.send_keys(Keys.RETURN)
        print("   📍 [3단계] 정제된 이름으로 검색 실행 (300m 필터링 적용 중)")
        time.sleep(2.5) 
        
        # ➡️ 4단계: 매칭 및 데이터 추출
        search_results = driver.find_elements(By.CSS_SELECTOR, "li.PlaceItem.clickArea")
        
        if not search_results:
            print("   ❌ [검색 실패] 현재 300m 지도 반경 내에 해당 주차장이 없습니다.")
            df.at[index, '카카오MAP_별점'] = "조건 불일치 제외"
        else:
            best_element = None
            best_title = ""
            max_ratio = 0.0

            for item in search_results:
                try:
                    title = item.find_element(By.CSS_SELECTOR, "a.link_name").text.strip()
                    compare_title = title.replace(" ", "")
                    ratio = SequenceMatcher(None, clean_name, compare_title).ratio()
                    
                    if ratio > max_ratio:
                        max_ratio = ratio
                        best_element = item
                        best_title = title
                except:
                    continue

            if not best_element or "주차" not in best_title:
                print(f"   ❌ [매칭 실패] 300m 이내 결과 중 주차 키워드 없음 (이름: {best_title})")
                df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
            else:
                try:
                    k_star = best_element.find_element(By.CSS_SELECTOR, "em.num").text.strip()
                    if not k_star or k_star == "0.0": k_star = "평가 없음"
                except: k_star = "평가 없음"
                    
                k_star_count = "0"
                try:
                    rating_zone = best_element.find_element(By.CSS_SELECTOR, ".rating").text
                    match_star_count = re.search(r'([0-9,]+)건', rating_zone)
                    if match_star_count:
                        k_star_count = match_star_count.group(1).replace(',', '')
                except: k_star_count = "0"
                            
                # URL 추출 및 별점 참여건수 조건 판별
                try:
                    k_url = best_element.find_element(By.CSS_SELECTOR, "a.moreview").get_attribute("href")
                    
                    # 참여 건수가 '0'이거나 아예 비어있으면 후기 URL을 공백으로 둠
                    if k_star_count == "0" or k_star_count == "":
                        k_review_url = ""
                    else:
                        k_review_url = k_url + "#review"
                except: 
                    k_url = "없음"
                    k_review_url = ""
                    
                sim_pct = f"{max_ratio * 100:.1f}%"
                print(f"   🟢 [매칭 성공] {best_title} ({sim_pct}) | 별점: {k_star} | 참여건수: {k_star_count}개")
                
                df.at[index, '카카오맵_매칭된_주차장명'] = best_title
                df.at[index, '카카오맵_유사도'] = sim_pct
                df.at[index, '카카오맵_별점'] = k_star
                df.at[index, '카카오맵_별점참여건수'] = k_star_count
                df.at[index, '카카오맵_URL'] = k_url
                df.at[index, '카카오맵_후기_URL'] = k_review_url

        # ➡️ 5단계: 검색 초기화
        try:
            bound_label = driver.find_element(By.XPATH, "//label[contains(text(), '현 지도 내 장소검색')]")
            checkbox_id = bound_label.get_attribute("for")
            bound_checkbox = driver.find_element(By.ID, checkbox_id)
            if bound_checkbox.is_selected():
                driver.execute_script("arguments[0].click();", bound_label)
                time.sleep(0.5)
        except: pass

    except Exception as e:
        print(f"   ❌ [시스템 오류] 에러 발생 (사유: {e})")
        df.at[index, '카카오맵_별점'] = "조건 불일치 제외"
        
    processed_count += 1

print("=" * 80)
print(f"✅ 본 작업 완료! 총 {processed_count}개의 데이터 구역 순회가 완전히 끝났습니다.")
driver.quit()

# 5. 최종 결과 저장 (파일명에서 '테스트' 단어 삭제)
result_file_name = "parking_kakao_crawled_300m_최종.csv"
df.to_csv(result_file_name, index=False, encoding='utf-8-sig')
print(f"\n🎉 대성공! 최종 정제 통합 데이터가 [{result_file_name}] 파일로 안전하게 보존되었습니다!")