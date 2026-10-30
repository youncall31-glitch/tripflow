"""여행지(발리·제주) 장소 오픈 데이터 만들기 (GitHub Actions에서 실행)

여행지는 환경변수 TRIP_AREA=bali|jeju 로 고름 (기본 bali)

Overture Maps 'places' GeoParquet → 앱이 읽는 작은 JSON(data/bali-places.json)

단계 (GitHub Actions에서 순서대로 실행)
  extract <입력.parquet> <places.json>                 Overture 장소 추출 + 인지도 재료(A)
  wiki    <wiki.json>                                  위키데이터 발리 항목(위키백과 언어 수)
  naver   <places.json> <counts.json> [최대호출수]       네이버 블로그 언급 수(B), 캐시에 누적
  final   <places.json> <wiki.json> <counts.json> <out.json> [release]   합산 인기 점수 → 앱용 JSON
데이터 출처: Overture Maps Foundation (CDLA Permissive 2.0 등, 원천: OpenStreetMap·Meta·Microsoft·Foursquare 등)
"""
import datetime
import json
import math
import os
import re
import struct
import sys
import time
import urllib.parse
import urllib.request

CATS = ['식당', '카페', '여행스팟', '숙소']
MIN_CONFIDENCE = 0.45
BBOX = (114.35, -9.0, 115.8, -7.95)  # 서, 남, 동, 북 — configure()가 여행지에 맞게 바꿈

CAFE = {'cafe', 'coffee', 'tea', 'bakery', 'dessert', 'desserts', 'juice', 'ice', 'gelato', 'smoothie', 'patisserie',
        'donut', 'donuts', 'bubble', 'boba', 'cake', 'chocolate', 'creperie'}
FOOD = {'restaurant', 'restaurants', 'food', 'bar', 'bars', 'pub', 'bistro', 'diner', 'eatery', 'warung', 'steakhouse',
        'grill', 'pizza', 'pizzeria', 'burger', 'burgers', 'noodle', 'noodles', 'seafood', 'buffet', 'brewery', 'lounge',
        'gastropub', 'bbq', 'barbecue', 'sushi', 'ramen', 'deli', 'sandwich', 'taco', 'kebab', 'snack', 'brasserie',
        'canteen', 'trattoria', 'winery', 'beach_club', 'club'}
STAY = {'hotel', 'hotels', 'resort', 'villa', 'villas', 'hostel', 'guest', 'guesthouse', 'bed', 'homestay', 'lodging',
        'accommodation', 'holiday', 'inn', 'motel', 'cottage', 'campground', 'glamping', 'bungalow', 'retreat'}
SPOT = {'attraction', 'attractions', 'landmark', 'historical', 'temple', 'shrine', 'church', 'beach', 'park', 'garden',
        'gardens', 'museum', 'gallery', 'waterfall', 'viewpoint', 'lookout', 'monument', 'zoo', 'aquarium', 'market',
        'lake', 'mountain', 'volcano', 'hiking', 'trail', 'rice', 'cliff', 'cave', 'island', 'palace', 'theme',
        'water_park', 'amusement', 'swing', 'surf', 'surfing', 'snorkeling', 'diving', 'monkey', 'forest', 'spring',
        'springs', 'hot', 'plaza', 'cultural', 'heritage', 'scenic', 'sightseeing', 'tourist', 'nature', 'reserve',
        'sanctuary', 'pier', 'harbor', 'bay', 'river'}
# 이 단어가 들어간 분류는 무조건 제외 (예: hardware_home_and_garden_store, food_and_beverage_store)
STRICT = {'store', 'stores', 'shopping', 'supermarket', 'warehouse', 'wholesale', 'hardware',
          'dealer', 'dealership', 'supplier', 'manufacturer', 'distributor', 'service', 'services', 'rental', 'atm'}
SHOP = {'shop', 'shops'}  # coffee_shop·ice_cream_shop·dessert_shop 같은 카페류만 살리고 나머지 가게는 제외
NEVER = {'barber', 'barbers', 'bank', 'atm', 'hospital', 'clinic', 'dentist', 'pharmacy', 'school', 'office',
         'company', 'contractor', 'repair', 'agency', 'insurance', 'lawyer', 'gas', 'parking', 'car', 'motorcycle',
         'store', 'shop', 'shopping', 'supermarket', 'convenience', 'laundry', 'salon', 'spa', 'gym', 'fitness'}

KO = {
    'restaurant': '식당', 'indonesian_restaurant': '인도네시아 음식', 'balinese_restaurant': '발리 음식',
    'seafood_restaurant': '해산물', 'asian_restaurant': '아시안', 'italian_restaurant': '이탈리안',
    'japanese_restaurant': '일식', 'korean_restaurant': '한식', 'chinese_restaurant': '중식',
    'thai_restaurant': '태국 음식', 'indian_restaurant': '인도 음식', 'mexican_restaurant': '멕시칸',
    'vegetarian_restaurant': '채식', 'vegan_restaurant': '비건', 'pizza_restaurant': '피자', 'burger_restaurant': '버거',
    'steakhouse': '스테이크', 'fast_food_restaurant': '패스트푸드', 'bar': '바', 'beach_bar': '비치 바', 'pub': '펍',
    'lounge': '라운지', 'beach_club': '비치클럽', 'cafe': '카페', 'coffee_shop': '커피', 'bakery': '베이커리',
    'dessert_shop': '디저트', 'juice_bar': '주스', 'ice_cream_shop': '아이스크림', 'tea_room': '찻집',
    'hotel': '호텔', 'resort': '리조트', 'villa': '빌라', 'hostel': '호스텔', 'guest_house': '게스트하우스',
    'bed_and_breakfast': 'B&B', 'holiday_rental_home': '숙소', 'homestay': '홈스테이',
    'beach': '해변', 'hindu_temple': '힌두 사원', 'temple': '사원', 'buddhist_temple': '불교 사원',
    'landmark_and_historical_building': '명소', 'tourist_attraction': '관광명소', 'park': '공원', 'garden': '정원',
    'museum': '박물관', 'art_gallery': '갤러리', 'waterfall': '폭포', 'scenic_lookout': '전망대',
    'market': '시장', 'farmers_market': '전통시장', 'zoo': '동물원', 'water_park': '워터파크',
    'amusement_park': '놀이공원', 'palace': '궁전', 'rice_field': '논', 'hiking_trail': '트레킹',
    'cultural_and_historic': '문화·역사 명소', 'lodging': '숙소', 'private_lodging': '개인 숙소',
    'casual_eatery': '캐주얼 식당', 'food_and_drink': '음식·음료', 'river': '강', 'mountain': '산',
    'scuba_diving_center': '다이빙', 'food_truck_stand': '푸드트럭', 'campground': '캠핑장', 'eat_and_drink': '음식·음료',
    'fine_dining_restaurant': '파인다이닝', 'breakfast_and_brunch_restaurant': '브런치', 'night_club': '나이트클럽',
    'waterfall': '폭포', 'lake': '호수', 'island': '섬', 'nature_reserve': '자연보호구역', 'landmark': '명소',
    'religious_site': '종교 명소', 'place_of_worship': '사원', 'tourist_information': '관광 안내', 'viewpoint': '전망대',
}


def tokens(cat):
    return set(re.split(r'[_\s/&-]+', cat.lower())) | {cat.lower()}


def classify(cat_list):
    """카테고리 문자열 목록(주 → 보조) → 앱 분류 인덱스, 대표 카테고리. 해당 없으면 (None, None)"""
    for cat in cat_list:
        if not cat:
            continue
        t = tokens(cat)
        if t & STRICT and 'holiday_rental_home' not in cat.lower():
            continue
        if t & SHOP and not t & CAFE:
            continue
        if t & NEVER and not (t & (FOOD | CAFE | SPOT | STAY) - {'bar'}):
            continue
        if t & CAFE:
            return 1, cat
        if t & FOOD:
            return 0, cat
        if t & STAY:
            return 3, cat
        if t & SPOT:
            return 2, cat
    return None, None


def kind_label(cat):
    if not cat:
        return ''
    return KO.get(cat.lower()) or cat.replace('_', ' ').capitalize()


def wkb_point(b):
    """WKB Point → (lng, lat). 실패하면 None"""
    try:
        b = bytes(b)
        le = b[0] == 1
        fmt = '<' if le else '>'
        gtype = struct.unpack(fmt + 'I', b[1:5])[0] & 0xFF
        if gtype != 1:
            return None
        return struct.unpack(fmt + 'dd', b[5:21])
    except Exception:
        return None


def as_obj(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


# 여행 지역 (대략적인 직사각형 경계: 서, 남, 동, 북). 앱의 TRIPS[*].regions와 같은 순서·id
BALI_REGIONS = [
    ('seminyak', '스미냑·르기안', '스미냑', 'Seminyak·Legian', (115.145, -8.715, 115.182, -8.665)),
    ('kuta', '꾸따', '꾸따', 'Kuta', (115.160, -8.760, 115.190, -8.715)),
    ('canggu', '짱구', '짱구', 'Canggu', (115.110, -8.665, 115.160, -8.615)),
    ('jimbaran', '짐바란', '짐바란', 'Jimbaran', (115.145, -8.805, 115.195, -8.760)),
    ('uluwatu', '울루와뚜·빙인', '울루와뚜', 'Uluwatu·Bingin', (115.070, -8.850, 115.150, -8.790)),
    ('ungasan', '멜라스티·판다와', '멜라스티', 'Melasti·Pandawa', (115.150, -8.860, 115.215, -8.805)),
    ('nusadua', '누사두아', '누사두아', 'Nusa Dua·Benoa', (115.195, -8.830, 115.245, -8.750)),
    ('sanur', '사누르', '사누르', 'Sanur', (115.240, -8.725, 115.275, -8.660)),
    ('denpasar', '덴파사르', '덴파사르', 'Denpasar', (115.182, -8.700, 115.240, -8.610)),
    ('ubud', '우붓', '우붓', 'Ubud', (115.235, -8.545, 115.295, -8.475)),
    ('tegallalang', '뜨갈랄랑·띠르따엠풀', '뜨갈랄랑', 'Tegallalang·Tampaksiring', (115.255, -8.475, 115.335, -8.385)),
    ('kintamani', '킨타마니', '킨타마니', 'Kintamani·Batur', (115.300, -8.330, 115.420, -8.200)),
    ('bedugul', '브두굴', '브두굴', 'Bedugul', (115.120, -8.320, 115.200, -8.240)),
    ('tanahlot', '따나롯', '따나롯', 'Tanah Lot', (115.060, -8.640, 115.110, -8.590)),
    ('east', '아메드·동부', '아메드', 'Amed·East Bali', (115.480, -8.560, 115.720, -8.300)),
    ('lovina', '로비나', '로비나', 'Lovina', (114.950, -8.200, 115.100, -8.130)),
    ('nusapenida', '누사페니다', '누사페니다', 'Nusa Penida·Lembongan', (115.420, -8.850, 115.630, -8.660)),
]


JEJU_REGIONS = [
    ('jejusi', '제주시내·공항', '제주시', 'Jeju City', (126.450, 33.470, 126.580, 33.530)),
    ('jocheon', '조천·함덕', '함덕', 'Jocheon·Hamdeok', (126.580, 33.480, 126.730, 33.560)),
    ('gujwa', '구좌·월정·세화', '월정리', 'Gujwa·Woljeong', (126.730, 33.450, 126.900, 33.570)),
    ('seongsan', '성산·우도', '성산', 'Seongsan·Udo', (126.850, 33.380, 126.980, 33.530)),
    ('pyoseon', '표선', '표선', 'Pyoseon', (126.750, 33.300, 126.850, 33.420)),
    ('namwon', '남원·위미', '남원', 'Namwon·Wimi', (126.620, 33.240, 126.750, 33.330)),
    ('seogwipo', '서귀포시내', '서귀포', 'Seogwipo', (126.500, 33.220, 126.620, 33.270)),
    ('jungmun', '중문', '중문', 'Jungmun', (126.380, 33.220, 126.500, 33.270)),
    ('andeok', '안덕·산방산', '산방산', 'Andeok·Sanbangsan', (126.250, 33.200, 126.380, 33.300)),
    ('daejeong', '대정·모슬포', '모슬포', 'Daejeong·Moseulpo', (126.150, 33.190, 126.250, 33.300)),
    ('hangyeong', '한경·고산', '고산', 'Hangyeong·Gosan', (126.150, 33.300, 126.230, 33.380)),
    ('hallim', '한림·협재', '협재', 'Hallim·Hyeopjae', (126.230, 33.360, 126.320, 33.430)),
    ('aewol', '애월', '애월', 'Aewol', (126.300, 33.420, 126.450, 33.500)),
    ('hallasan', '한라산·중산간', '한라산', 'Hallasan', (126.400, 33.300, 126.700, 33.420)),
]
AREAS = {
    'bali': {'bbox': (114.35, -9.0, 115.8, -7.95), 'regions': BALI_REGIONS, 'suffix': '발리', 'min_places': 1000},
    'jeju': {'bbox': (126.10, 33.10, 127.00, 33.60), 'regions': JEJU_REGIONS, 'suffix': '제주', 'min_places': 300},
}
AREA = 'bali'
REGIONS = BALI_REGIONS
NAVER_SUFFIX = '발리'
MIN_PLACES = 1000


def configure(area):
    """여행지 설정을 전역값으로 적용"""
    global AREA, BBOX, REGIONS, NAVER_SUFFIX, MIN_PLACES
    cfg = AREAS[area]
    AREA, BBOX, REGIONS, NAVER_SUFFIX, MIN_PLACES = area, cfg['bbox'], cfg['regions'], cfg['suffix'], cfg['min_places']


configure(os.environ.get('TRIP_AREA', 'bali'))


def region_of(lat, lng):
    for i, (_, _, _, _, (w, s, e, n)) in enumerate(REGIONS):
        if w <= lng <= e and s <= lat <= n:
            return i
    return -1


def row_to_place(r):
    names = as_obj(r.get('names')) or {}
    name = (names.get('primary') if isinstance(names, dict) else None) or ''
    name = ' '.join(str(name).split())[:80]
    if not name:
        return None
    conf = r.get('confidence')
    if conf is not None and conf < MIN_CONFIDENCE:
        return None
    ops = r.get('operating_status')
    if ops and str(ops).lower() not in ('open', 'operating'):
        return None

    cat_list = []
    basic = r.get('basic_category')
    cats = as_obj(r.get('categories')) or {}
    if isinstance(cats, dict):
        cat_list.append(cats.get('primary'))
    if basic:
        cat_list.append(basic)
    if isinstance(cats, dict):
        cat_list.extend(cats.get('alternate') or [])
    tax = as_obj(r.get('taxonomy')) or {}
    if isinstance(tax, dict):
        cat_list.append(tax.get('primary'))
        cat_list.extend(reversed(tax.get('hierarchy') or []))
    ci, cat = classify([c for c in cat_list if isinstance(c, str)])
    if ci is None:
        return None

    lng = lat = None
    bb = as_obj(r.get('bbox'))
    if isinstance(bb, dict) and bb.get('xmin') is not None:
        lng = (bb['xmin'] + bb['xmax']) / 2
        lat = (bb['ymin'] + bb['ymax']) / 2
    elif r.get('geometry') is not None:
        pt = wkb_point(r['geometry'])
        if pt:
            lng, lat = pt
    if lat is None or not (BBOX[1] < lat < BBOX[3] and BBOX[0] < lng < BBOX[2]):
        return None

    area = ''
    addrs = as_obj(r.get('addresses')) or []
    if isinstance(addrs, list) and addrs:
        a = addrs[0] or {}
        area = (a.get('locality') or a.get('region') or '') if isinstance(a, dict) else ''

    # 인지도 재료(A): 몇 개 지도 서비스에 등록됐나, 웹사이트·SNS·전화·브랜드, 신뢰도
    srcs = as_obj(r.get('sources')) or []
    datasets = {str(x.get('dataset', '')).lower() for x in srcs if isinstance(x, dict) and x.get('dataset')} if isinstance(srcs, list) else set()
    nonempty = lambda v: bool(as_obj(v)) if v is not None else False
    brand = as_obj(r.get('brand'))
    return {'name': name, 'lat': lat, 'lng': lng, 'ci': ci, 'kind': kind_label(cat), 'area': str(area)[:40],
            'src_n': max(1, len(datasets)), 'web': nonempty(r.get('websites')), 'soc': nonempty(r.get('socials')),
            'phone': nonempty(r.get('phones')), 'brand': bool(brand), 'conf': float(conf) if conf is not None else 0.5}


def place_key(p):
    return f"{p['name']}|{p['lat']:.3f}|{p['lng']:.3f}"


def extract(rows):
    places, seen = [], set()
    n = 0
    for r in rows:
        n += 1
        p = row_to_place(r)
        if not p:
            continue
        sig = (re.sub(r'\W+', '', p['name'].lower()), round(p['lat'], 4), round(p['lng'], 4))
        if sig in seen:
            continue
        seen.add(sig)
        p['region'] = region_of(p['lat'], p['lng'])
        places.append(p)
    return places, n


# ---------------- 위키데이터 (관광지 인지도 보강) ----------------
WIKI_SPARQL = """SELECT ?item ?label ?coord ?sl WHERE {
  SERVICE wikibase:box { ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point(%s %s)"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerNorthEast "Point(%s %s)"^^geo:wktLiteral . }
  ?item wikibase:sitelinks ?sl . FILTER(?sl > 0)
  ?item rdfs:label ?label . FILTER(LANG(?label) IN ("en", "id"))
}"""
UA = 'TripFlow-data-builder/1.0 (https://github.com/youncall31-glitch/tripflow)'


def fetch_wiki(tiles=4, tries=3):
    """발리를 tiles×tiles 칸으로 나눠 조회 (한 번에 받으면 응답이 끊기는 경우가 있어서)"""
    w0, s0, e0, n0 = BBOX
    out = {}
    for i in range(tiles):
        for j in range(tiles):
            w, e = w0 + (e0 - w0) * i / tiles, w0 + (e0 - w0) * (i + 1) / tiles
            so, n = s0 + (n0 - s0) * j / tiles, s0 + (n0 - s0) * (j + 1) / tiles
            q = WIKI_SPARQL % (f'{w:.4f}', f'{so:.4f}', f'{e:.4f}', f'{n:.4f}')
            body = urllib.parse.urlencode({'query': q, 'format': 'json'}).encode()
            for t in range(tries):
                try:
                    req = urllib.request.Request('https://query.wikidata.org/sparql', data=body,
                                                 headers={'User-Agent': UA, 'Accept': 'application/sparql-results+json'})
                    with urllib.request.urlopen(req, timeout=120) as res:
                        d = json.loads(res.read())
                    break
                except Exception:
                    if t == tries - 1:
                        raise
                    time.sleep(5 * (t + 1))
            for b in d['results']['bindings']:
                m = re.match(r'Point\(([-\d.]+) ([-\d.]+)\)', b['coord']['value'])
                if not m:
                    continue
                qid = b['item']['value'].rsplit('/', 1)[-1]
                out.setdefault(qid, [float(m.group(2)), float(m.group(1)), int(b['sl']['value']), set()])[3].add(b['label']['value'])
    return [[sorted(v[3]), v[0], v[1], v[2]] for v in out.values()]


# ---------------- 이름 비교 ----------------
GENERIC = {'warung', 'cafe', 'coffee', 'kopi', 'restaurant', 'resto', 'bar', 'kitchen', 'bali', 'the', 'and', 'villa',
           'villas', 'house', 'beach', 'club', 'spa', 'shop', 'store', 'food', 'makan', 'rumah', 'bakery', 'grill',
           'pura', 'temple', 'hotel', 'resort', 'homestay', 'guest', 'inn', 'bistro', 'lounge', 'eatery', 'babi',
           'nasi', 'ayam', 'ikan', 'mie', 'sate', 'seafood', 'pizza', 'burger', 'juice', 'tea', 'co', 'by', 'de',
           'la', 'le', 'di', 'jl', 'jalan', 'kuta', 'ubud', 'canggu', 'seminyak', 'sanur', 'denpasar', 'jimbaran',
           'pantai', 'objek', 'wisata', 'kawasan', 'luar', 'tempio', 'tempel', 'desa', 'adat', 'dalem', 'agung',
           'jeju', 'seogwipo', '제주', '제주도', '제주점', '서귀포', '식당', '카페', '맛집', '횟집', '본점', '해수욕장',
           '해변', '공원', '흑돼지', '고기국수', '해장국', '펜션', '호텔', '리조트', '게스트하우스', '편의점'}


def words(name):
    return [w for w in re.split(r'[^0-9a-z가-힣]+', name.lower()) if w]


def distinctive(name):
    # 한글은 두 글자도 고유한 이름이 많아서(예: 우진, 명진) 2자 이상, 영문은 3자 이상
    return [w for w in words(name) if w not in GENERIC and (len(w) >= 3 or (len(w) == 2 and re.match('[가-힣]', w)))]


def hav_m(a_lat, a_lng, b_lat, b_lng):
    r = math.pi / 180
    h = math.sin((b_lat - a_lat) * r / 2) ** 2 + math.cos(a_lat * r) * math.cos(b_lat * r) * math.sin((b_lng - a_lng) * r / 2) ** 2
    return 2 * 6371000 * math.asin(min(1, math.sqrt(h)))


def match_wiki(places, wiki):
    grid = {}
    for labels, lat, lng, sl in wiki:
        grid.setdefault((round(lat, 2), round(lng, 2)), []).append((labels, lat, lng, sl))
    hits = 0
    for p in places:
        best = 0
        pw = set(distinctive(p['name']))
        if not pw:
            continue
        gl, gn = round(p['lat'], 2), round(p['lng'], 2)
        for dl in (-0.01, 0, 0.01):
            for dn in (-0.01, 0, 0.01):
                for labels, lat, lng, sl in grid.get((round(gl + dl, 2), round(gn + dn, 2)), []):
                    if sl <= best or hav_m(p['lat'], p['lng'], lat, lng) > 500:
                        continue
                    if any(pw & set(distinctive(l)) for l in labels):
                        best = sl
        if best:
            p['wiki'] = best
            hits += 1
    return hits


# ---------------- 네이버 블로그 언급 수 (B) ----------------
def naver_query(p):
    return f'"{p["name"]}" {NAVER_SUFFIX}'


def naver_total(query, cid, secret, mode):
    if mode == 'hub':
        url = 'https://naverapihub.apigw.ntruss.com/search/v1/blog?' + urllib.parse.urlencode({'query': query, 'display': 1})
        headers = {'X-NCP-APIGW-API-KEY-ID': cid, 'X-NCP-APIGW-API-KEY': secret}
    else:
        url = 'https://openapi.naver.com/v1/search/blog.json?' + urllib.parse.urlencode({'query': query, 'display': 1})
        headers = {'X-Naver-Client-Id': cid, 'X-Naver-Client-Secret': secret}
    req = urllib.request.Request(url, headers={**headers, 'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=15) as res:
        return int(json.load(res).get('total', 0))


def naver_candidates(places, counts, today, stale_days=60):
    """식당·카페·관광지 중 이름이 흔하지 않은 곳. 여행 지역 → 관광지 → 나머지 순, 오래된 값부터"""
    out = []
    for p in places:
        if p['ci'] == 3 or not distinctive(p['name']):
            continue
        c = counts.get(place_key(p))
        if c and (today - datetime.date.fromisoformat(c[1])).days < stale_days:
            continue
        prio = (0 if p['region'] >= 0 else 1, 0 if p['ci'] == 2 else 1, c[1] if c else '0000')
        out.append((prio, p))
    out.sort(key=lambda x: x[0])
    return [p for _, p in out]


def update_naver(places, counts, limit, cid, secret, fetch=naver_total, sleep=0.11, log=print):
    today = datetime.date.today()
    todo = naver_candidates(places, counts, today)[:limit]
    modes = ['open', 'hub']
    if os.environ.get('NAVER_API', '').lower() == 'hub':
        modes.reverse()
    mode, done, fails = None, 0, 0
    for p in todo:
        q = naver_query(p)
        try:
            if mode is None:  # 처음 한 번은 두 방식 다 시도해서 되는 쪽으로
                for m in modes:
                    try:
                        total = fetch(q, cid, secret, m)
                        mode = m
                        log(f'naver api mode: {m}')
                        break
                    except Exception as e:
                        log(f'naver api mode {m} failed: {e}')
                if mode is None:
                    raise RuntimeError('naver auth failed in both modes')
            else:
                total = fetch(q, cid, secret, mode)
        except RuntimeError:
            raise
        except Exception as e:
            fails += 1
            if '429' in str(e) or fails > 200:  # 하루 한도 초과 등 → 여기까지 저장하고 종료
                log(f'stop: {e}')
                break
            continue
        counts[place_key(p)] = [total, today.isoformat()]
        done += 1
        if sleep:
            time.sleep(sleep)
    return done, len(todo), fails


# ---------------- 합산 점수 ----------------
CHAINS = re.compile(r"\b(mcd|mcdonald'?s?|kfc|burger king|starbucks|domino'?s|pizza hut|j\.?\s?co|black canyon|subway|a&w|"
                    r"texas chicken|ack fried|solaria|hokben|chatime|circle k|indomaret|alfamart|upnormal|richeese|"
                    r"wendy'?s|baskin|dunkin|janji jiwa|kopi kenangan|point coffee|fore coffee|excelso|gloria jean'?s|"
                    r"스타벅스|이디야|투썸|맥도날드|롯데리아|버거킹|파리바게뜨|뚜레쥬르|메가커피|메가mgc|빽다방|컴포즈|"
                    r"할리스|탐앤탐스|폴바셋|공차|배스킨|던킨|맘스터치|bbq|bhc|교촌|굽네|서브웨이)", re.I)


def a_raw(p):
    # 체인 브랜드(맥도날드·스타벅스 등)는 '여행 맛집'이 아니라 가산점 대신 감점
    chain = p['ci'] in (0, 1) and (p['brand'] or CHAINS.search(p['name']))
    brand = -3.0 if chain else 0.5 * p['brand']
    return (1.2 * (p['src_n'] - 1) + 1.0 * p['web'] + 0.8 * p['soc'] + 0.3 * p['phone'] + brand
            + 1.0 * p['conf'] + 2.0 * math.log2(1 + p.get('wiki', 0)))


def merge_dup_spots(places):
    """같은 관광지가 언어별로 여러 번 등록된 경우(Pura Uluwatu / Uluwatu Temple / Tempio di Uluwatu) 하나만 남김"""
    spots = sorted([p for p in places if p['ci'] == 2], key=lambda p: -a_raw(p))
    keep, grid, drop = [], {}, set()
    for p in spots:
        pw = set(distinctive(p['name']))
        cell = (round(p['lat'], 2), round(p['lng'], 2))
        dup = False
        for dl in (-0.01, 0, 0.01):
            for dn in (-0.01, 0, 0.01):
                for q in grid.get((round(cell[0] + dl, 2), round(cell[1] + dn, 2)), []):
                    if pw and pw & q[1] and hav_m(p['lat'], p['lng'], q[0]['lat'], q[0]['lng']) < 300:
                        dup = True
                        q[0]['wiki'] = max(q[0].get('wiki', 0), p.get('wiki', 0))
                        break
                if dup:
                    break
            if dup:
                break
        if dup:
            drop.add(id(p))
        else:
            grid.setdefault(cell, []).append((p, pw))
    return [p for p in places if id(p) not in drop], len(drop)


def pct_ranks(vals):
    """값 → 0~1 백분위 (같은 값은 평균 순위)"""
    if len(vals) == 1:
        return [0.5]
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    n = max(1, len(vals) - 1)
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        r = (i + j) / 2 / n
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def finalize(places, wiki, counts, release=''):
    match_wiki(places, wiki)
    places[:], merged = merge_dup_spots(places)
    print(f'merged duplicate spots: {merged}')
    for ci in range(len(CATS)):
        grp = [p for p in places if p['ci'] == ci]
        if not grp:
            continue
        pa = pct_ranks([a_raw(p) for p in grp])
        withb = [(i, counts[place_key(p)][0]) for i, p in enumerate(grp) if place_key(p) in counts]
        pb = {}
        if withb:
            ranks = pct_ranks([math.log10(1 + c) for _, c in withb])
            pb = {i: r for (i, _), r in zip(withb, ranks)}
        for i, p in enumerate(grp):
            score = 0.5 * pa[i] + 0.5 * pb[i] if i in pb else pa[i]
            p['pop'] = min(999, int(score * 1000))  # 0~999 (동점 줄이기)
            p['blog'] = counts[place_key(p)][0] if place_key(p) in counts else None
    places.sort(key=lambda p: (p['ci'], -p['pop'], p['name'].lower()))
    kinds = sorted({p['kind'] for p in places})
    kidx = {k: i for i, k in enumerate(kinds)}
    return {
        'v': 2,
        'built': datetime.date.today().isoformat(),
        'release': release,
        'source': 'Overture Maps Foundation places (OpenStreetMap, Meta, Microsoft, Foursquare 등) · CDLA Permissive 2.0; Wikidata (CC0); 네이버 블로그 검색 결과 수',
        'score': '인기 점수 = 공개 데이터 인지도(등록 서비스 수·웹/SNS·위키백과) 백분위 50% + 네이버 블로그 언급 수 백분위 50% (분류별)',
        'cats': CATS,
        'kinds': kinds,
        'regions': [{'id': r[0], 'ko': r[1], 'q': r[2], 'en': r[3], 'b': list(r[4])} for r in REGIONS],
        # [이름, 위도e5, 경도e5, 분류, 종류, 동네, 인기(0-999), 지역(-1=없음), 블로그 글 수(null=미조사)]
        'p': [[p['name'], round(p['lat'] * 1e5), round(p['lng'] * 1e5), p['ci'], kidx[p['kind']], p['area'],
               p['pop'], p['region'], p['blog']] for p in places],
    }


def gh(level, msg):
    """GitHub Actions 주석(annotation)으로도 남겨서 API로 확인 가능하게"""
    msg = str(msg).replace('%', '%25').replace('\r', '').replace('\n', '%0A')
    print(f'::{level}::{msg}', flush=True)


def read_parquet_rows(path):
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    names = set(pf.schema_arrow.names)
    gh('notice', f'rows={pf.metadata.num_rows} columns={sorted(names)}')
    want = [c for c in ('names', 'categories', 'basic_category', 'taxonomy', 'confidence', 'operating_status',
                        'addresses', 'bbox', 'geometry', 'sources', 'websites', 'socials', 'phones', 'brand') if c in names]
    for batch in pf.iter_batches(batch_size=20000, columns=want):
        for r in batch.to_pylist():
            yield r


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj, compact=True):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':') if compact else None, indent=None if compact else 0)


def main():
    cmd = sys.argv[1]
    if cmd == 'extract':
        places, n = extract(read_parquet_rows(sys.argv[2]))
        by_cat = {c: sum(1 for p in places if p['ci'] == i) for i, c in enumerate(CATS)}
        in_regions = sum(1 for p in places if p['region'] >= 0)
        gh('notice', f'[{AREA}] rows={n} kept={len(places)} by_cat={by_cat} in_regions={in_regions}')
        if len(places) < MIN_PLACES:
            gh('error', f'too few places ({len(places)}) - schema change?')
            sys.exit(1)
        save_json(sys.argv[3], places)
    elif cmd == 'wiki':
        try:
            wiki = fetch_wiki()
            gh('notice', f'[{AREA}] wikidata items={len(wiki)}')
        except Exception as e:
            gh('warning', f'wikidata skipped: {e}')
            wiki = []
        save_json(sys.argv[2], wiki)
    elif cmd == 'naver':
        cid, secret = os.environ.get('NAVER_CLIENT_ID', ''), os.environ.get('NAVER_CLIENT_SECRET', '')
        if not cid or not secret:
            gh('notice', 'NAVER_CLIENT_ID/SECRET 비밀값이 없어 블로그 언급 수(B)는 건너뜀')
            return
        places = load_json(sys.argv[2], [])
        counts = load_json(sys.argv[3], {})
        limit = int(sys.argv[4]) if len(sys.argv) > 4 else 20000
        try:
            done, todo, fails = update_naver(places, counts, limit, cid, secret)
        except RuntimeError as e:
            gh('error', f'네이버 검색 API 인증 실패 - 키를 확인해 주세요 ({e})')
            sys.exit(1)
        finally:
            save_json(sys.argv[3], counts, compact=False)
        gh('notice', f'[{AREA}] naver: queried={done}/{todo} fails={fails} cached={len(counts)}')
    elif cmd == 'final':
        places = load_json(sys.argv[2], [])
        if len(places) < MIN_PLACES:
            gh('error', '추출된 장소가 없어 앱용 JSON을 만들지 않음 (기존 파일 유지)')
            sys.exit(1)
        wiki = load_json(sys.argv[3], [])
        counts = load_json(sys.argv[4], {})
        out = finalize(places, wiki, counts, sys.argv[6] if len(sys.argv) > 6 else '')
        wiki_hits = sum(1 for p in places if p.get('wiki'))
        with_blog = sum(1 for r in out['p'] if r[8] is not None)
        save_json(sys.argv[5], out)
        gh('notice', f'[{AREA}] final: places={len(out["p"])} wiki_matched={wiki_hits} with_blog_counts={with_blog} '
                     f'size={os.path.getsize(sys.argv[5]) // 1024}KB')
    else:
        sys.exit(f'unknown command {cmd}')


if __name__ == '__main__':
    main()
