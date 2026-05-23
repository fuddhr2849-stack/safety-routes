import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
import random

KAKAO_API_KEY = st.secrets["KAKAO_API_KEY"]

# 세션 상태 초기화
# Streamlit은 화면이 새로고침될 때마다 변수가 초기화되는 특징이 있음.
# 따라서 검색 결과나 선택된 경로가 날아가지 않도록 세션(캐시)에 저장해두는 역할.
for key in ['search_clicked', 'routes', 'start_coord', 'end_coord', 'facilities', 'selected_route_idx']:
    if key not in st.session_state:
        st.session_state[key] = False if key == 'search_clicked' else ([] if key in ['routes', 'facilities'] else (0 if key == 'selected_route_idx' else None))

# --- 백엔드 로직 ---

def get_location(address):
    # 주소를 위경도로 변환 (카카오 로컬 API - 실제 연동)
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    res = requests.get(url, headers=headers, params={"query": address})
    docs = res.json().get('documents')
    
    if res.status_code == 200 and docs:
        # 검색된 결과 중 첫 번째 장소의 y(위도), x(경도)를 실수형(float)으로 반환
        return float(docs[0]['y']), float(docs[0]['x'])
    return None

def get_routes(start, end):
    # 보행자 다중 경로 탐색 (OSRM API - 가상 경유지 강제 할당 방식)
    routes = []

    # 1. 기본 최단 경로 (Start -> End)
    url_main = f"https://router.project-osrm.org/route/v1/foot/{start[1]},{start[0]};{end[1]},{end[0]}?geometries=geojson"
    res_main = requests.get(url_main)
    if res_main.status_code == 200 and res_main.json().get('routes'):
        routes.append(res_main.json()['routes'][0])

    # 핵심 알고리즘: 출발지와 도착지 사이의 수직(직교) 방향으로 가상의 우회 경유지 2개 계산
    dx = end[1] - start[1]
    dy = end[0] - start[0]
    mid_lat = start[0] + dy / 2
    mid_lon = start[1] + dx / 2

    # 경로가 너무 일직선이 되지 않도록 좌우로 벌리는 우회 비율 (0.3 = 직선거리의 약 30%만큼 우회)
    offset = 0.3 
    
    # 우회 경유지 A 좌표 (진행 방향의 좌측/우측)
    wp1_lat = mid_lat - dx * offset
    wp1_lon = mid_lon + dy * offset

    # 우회 경유지 B 좌표 (진행 방향의 반대쪽)
    wp2_lat = mid_lat + dx * offset
    wp2_lon = mid_lon - dy * offset

    # 2. 대안 경로 1 (경유지 A를 거쳐서 가는 길)
    url_alt1 = f"https://router.project-osrm.org/route/v1/foot/{start[1]},{start[0]};{wp1_lon},{wp1_lat};{end[1]},{end[0]}?geometries=geojson"
    res_alt1 = requests.get(url_alt1)
    if res_alt1.status_code == 200 and res_alt1.json().get('routes'):
        routes.append(res_alt1.json()['routes'][0])

    # 3. 대안 경로 2 (경유지 B를 거쳐서 가는 길)
    url_alt2 = f"https://router.project-osrm.org/route/v1/foot/{start[1]},{start[0]};{wp2_lon},{wp2_lat};{end[1]},{end[0]}?geometries=geojson"
    res_alt2 = requests.get(url_alt2)
    if res_alt2.status_code == 200 and res_alt2.json().get('routes'):
        routes.append(res_alt2.json()['routes'][0])

    return routes
    return []

def get_convenience_stores(lat, lon, radius=1000):
    # [API 연동] 카카오 카테고리 검색을 활용해 반경 1km 이내의 편의점(CS2) 실데이터 수집
    url = "https://dapi.kakao.com/v2/local/search/category.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    params = {"category_group_code": "CS2", "y": lat, "x": lon, "radius": radius}
    try:
        res = requests.get(url, headers=headers, params=params)
        stores = []
        if res.status_code == 200:
            for doc in res.json().get('documents', []):
                # 데이터 포맷을 통일하여 딕셔너리 형태로 리스트에 추가
                stores.append({"type": "store", "lat": float(doc['y']), "lon": float(doc['x']), "name": doc['place_name']})
        return stores
    except: return []


def get_mock_safety_data(lat, lon):
    # [시연용] 경찰서 및 CCTV 가상 데이터 생성 로직 (API 승인에 오랜 시간이 걸리는 관계로 가상 데이터로 대체)
    facilities = []
    
    # 목적지(lat, lon) 주변으로 임의의 좌표에 가상 경찰서 2개 배치
    for i in range(2):
        facilities.append({
            "type": "police",
            "lat": lat + random.uniform(-0.006, 0.006),
            "lon": lon + random.uniform(-0.006, 0.006),
            "name": f"가상 치안센터 {i+1}"
        })
        
    # 목적지 주변으로 임의의 좌표에 가상 CCTV 10개 흩뿌리기
    for i in range(10):
        facilities.append({
            "type": "cctv",
            "lat": lat + random.uniform(-0.008, 0.008),
            "lon": lon + random.uniform(-0.008, 0.008),
            "name": f"가상 방범용 CCTV {i+1}"
        })
        
    return facilities

def calculate_safety_score(route, facilities_data, weights):
    # 경로와 시설물 간의 거리를 계산하여 주변 시설물만 카운트
    base_score = 50
    route_coords = route['geometry']['coordinates'] # 경로를 이루는 [경도, 위도] 리스트
    
    nearby_facilities = []
    
    # 1. 경로 주변 시설물 필터링 (유클리디안 거리 계산 알고리즘)
    for fac in facilities_data:
        fac_lat, fac_lon = fac['lat'], fac['lon']
        
        # 경로의 점들을 순회하며 시설물과의 거리 측정
        # (성능을 위해 [::2]로 징검다리처럼 2칸씩 건너뛰며 검사)
        for lon, lat in route_coords[::2]:
            # 두 좌표 간의 직선 거리 계산 (피타고라스 정리 활용)
            dist = ((lat - fac_lat)**2 + (lon - fac_lon)**2)**0.5
            if dist < 0.002: # 대략 반경 200m 이내에 있으면
                nearby_facilities.append(fac)
                break # 이미 주변 시설물로 인정되었으니 다음 시설물로 넘어감
                
    # 2. 필터링된 주변 시설물만 카운트
    store_count = len([f for f in nearby_facilities if f['type'] == 'store'])
    police_count = len([f for f in nearby_facilities if f['type'] == 'police'])
    cctv_count = len([f for f in nearby_facilities if f['type'] == 'cctv'])
    
    # 3. 총점 계산 및 거리 페널티 적용 (거리가 멀수록 살짝 감점)
    distance_km = route['distance'] / 1000
    penalty = distance_km * 2 # 1km당 2점 감점
    
    total_score = base_score + (store_count * weights['store']) + (police_count * weights['police'] * 2) + (cctv_count * weights['cctv']) - penalty
    return min(100, int(total_score))

# --- 프론트엔드 UI (모바일 앱 최적화) ---

# layout="centered" 로 화면을 중앙으로 모아 앱 느낌 강조
st.set_page_config(page_title="최적의 귀로", page_icon="🚶‍♂️", layout="centered") 

st.header("🚶‍♂️ 최적의 귀로(歸路)")

# 사이드바 대신 아코디언(Expander) 메뉴 사용
with st.expander("⚙️ 안전 요소 가중치 설정 (터치하여 열기)"):
    st.caption("가장 중요하게 생각하는 치안 인프라의 비중을 조절하세요.")
    # 사용자는 0~100으로 편하게 조절하도록 UI 설정
    weight_cctv = st.slider("CCTV 밀집도", 0.0, 100.0, 50.0, 1.0)
    weight_police = st.slider("경찰서/치안센터", 0.0, 100.0, 50.0, 1.0)
    weight_store = st.slider("24시 편의점", 0.0, 100.0, 50.0, 1.0)
    # 내부 알고리즘 계산을 위해 0~100 값을 0.0~2.0 비율로 스케일링 (50으로 나누기)
    user_weights = {
        'cctv': weight_cctv / 50.0, 
        'police': weight_police / 50.0, 
        'store': weight_store / 50.0
    }


# UI 레이아웃 분할: 출발지와 목적지 입력창을 나란히 배치
col1, col2 = st.columns(2)
with col1: start_addr = st.text_input("📍 출발지", placeholder="예시) 혜화역")
with col2: end_addr = st.text_input("🚩 목적지", placeholder="예시) 성균관대")

# use_container_width=True로 버튼을 가로 꽉 차게 만듦
if st.button("🔍 안심 경로 탐색", type="primary", use_container_width=True):
    if not start_addr or not end_addr:
        st.error("출발지와 목적지를 모두 입력해주세요.")
    else:
        with st.spinner('안전 데이터 수집 및 경로 분석 중...'):
            # 좌표 변환
            st.session_state.start_coord = get_location(start_addr)
            st.session_state.end_coord = get_location(end_addr)
            
            if st.session_state.start_coord and st.session_state.end_coord:
                # 다중 경로 수집
                routes = get_routes(st.session_state.start_coord, st.session_state.end_coord)
                
                # 편의점(실제) + 경찰/CCTV(가상) 데이터 합치기
                stores = get_convenience_stores(st.session_state.end_coord[0], st.session_state.end_coord[1])
                mock_data = get_mock_safety_data(st.session_state.end_coord[0], st.session_state.end_coord[1])
                st.session_state.facilities = stores + mock_data
                
                if routes:
                    # 수집된 데이터를 바탕으로 각 경로별 점수 계산
                    for r in routes: r['safety_score'] = calculate_safety_score(r, st.session_state.facilities, user_weights)
                    # 안전도 점수(safety_score)가 높은 순서대로(내림차순, reverse=True)
                    st.session_state.routes = sorted(routes, key=lambda x: x['safety_score'], reverse=True)
                    st.session_state.search_clicked = True
                    st.session_state.selected_route_idx = 0 # 검색 시 항상 첫 번째(최적) 경로가 선택되도록 초기화
                else: st.error("경로를 찾을 수 없습니다.")

# 화면 시각화 (경로 선택 및 지도)
if st.session_state.search_clicked and st.session_state.routes:
    st.divider() # 시각적 분리선
    
    # 경로 선택 라디오 버튼 추가
    route_options = [f"🥇 최적 경로 (점수: {st.session_state.routes[0]['safety_score']}점)"]
    for i in range(1, len(st.session_state.routes)):
        route_options.append(f"🔄 대안 경로 {i} (점수: {st.session_state.routes[i]['safety_score']}점)")

    # 사용자가 라디오 버튼 클릭 시, 해당 텍스트의 리스트 인덱스 번호를 세션에 즉시 저장
    selected_label = st.radio("👇 안내받을 경로를 선택하세요", options=route_options)
    st.session_state.selected_route_idx = route_options.index(selected_label) # 사용자가 선택한 경로의 인덱스 저장
    
    # 지도 이쁘게 만들기 (CartoDB positron 타일 적용)
    m = folium.Map(location=st.session_state.start_coord, zoom_start=15, tiles="CartoDB positron")

    # 출발지/도착지 마커 추가
    folium.Marker(st.session_state.start_coord, popup="출발", icon=folium.Icon(color='blue')).add_to(m)
    folium.Marker(st.session_state.end_coord, popup="도착", icon=folium.Icon(color='red')).add_to(m)

    # 시설물 종류에 따라 알맞은 색상과 아이콘 렌더링
    for fac in st.session_state.facilities:
        if fac['type'] == 'store': icon = folium.Icon(color='green', icon='shopping-cart')
        elif fac['type'] == 'police': icon = folium.Icon(color='darkblue', icon='info-sign')
        else: icon = folium.Icon(color='lightgray', icon='camera')
        folium.Marker([fac['lat'], fac['lon']], popup=fac.get('name', '시설물'), icon=icon).add_to(m)

    # 경로 색상 팔레트
    route_colors = ["#10b981", "#3b82f6", "#f59e0b"]
    
    # 타 경로 블러처리 로직 (선택 안 된 경로 먼저 연하게 그리기)
    for i, route in enumerate(st.session_state.routes):
        if i == st.session_state.selected_route_idx: continue
        coords = [[c[1], c[0]] for c in route['geometry']['coordinates']]
        color = route_colors[i % len(route_colors)]
        folium.PolyLine(locations=coords, color=color, weight=4, opacity=0.3, dash_array="5").add_to(m)
        
    # 선택된 경로를 지도 맨 위에 굵고 진하게 그리기
    sel_route = st.session_state.routes[st.session_state.selected_route_idx]
    sel_coords = [[c[1], c[0]] for c in sel_route['geometry']['coordinates']]
    sel_color = route_colors[st.session_state.selected_route_idx % len(route_colors)]
    folium.PolyLine(locations=sel_coords, color=sel_color, weight=8, opacity=0.9).add_to(m)
    
    # 모바일 최적화 (use_container_width=True 로 가로 폭 꽉 차게)
    # key 값을 동적으로 주어 경로를 클릭할 때마다 지도가 즉시 리렌더링 되도록 설정
    st_folium(m, use_container_width=True, height=500, key=f"map_view_{st.session_state.selected_route_idx}")
