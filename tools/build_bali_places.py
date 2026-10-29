"""발리 장소 오픈 데이터 만들기 (GitHub Actions에서 실행)

Overture Maps 'places' GeoParquet → 앱이 읽는 작은 JSON(data/bali-places.json)

사용: python tools/build_bali_places.py <입력.parquet> <출력.json> [overture_release]
데이터 출처: Overture Maps Foundation (CDLA Permissive 2.0 등, 원천: OpenStreetMap·Meta·Microsoft·Foursquare 등)
"""
import datetime
import json
import re
import struct
import sys

CATS = ['식당', '카페', '여행스팟', '숙소']
MIN_CONFIDENCE = 0.45
BBOX = (114.35, -9.0, 115.8, -7.95)  # 서, 남, 동, 북 (발리 섬)

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
}


def tokens(cat):
    return set(re.split(r'[_\s/&-]+', cat.lower())) | {cat.lower()}


def classify(cat_list):
    """카테고리 문자열 목록(주 → 보조) → 앱 분류 인덱스, 대표 카테고리. 해당 없으면 (None, None)"""
    for cat in cat_list:
        if not cat:
            continue
        t = tokens(cat)
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
    return {'name': name, 'lat': lat, 'lng': lng, 'ci': ci, 'kind': kind_label(cat), 'area': str(area)[:40]}


def build(rows, release=''):
    places, seen = [], set()
    stats = {'rows': 0, 'kept': 0}
    for r in rows:
        stats['rows'] += 1
        p = row_to_place(r)
        if not p:
            continue
        sig = (re.sub(r'\W+', '', p['name'].lower()), round(p['lat'], 4), round(p['lng'], 4))
        if sig in seen:
            continue
        seen.add(sig)
        places.append(p)
    places.sort(key=lambda p: (p['ci'], p['name'].lower()))
    kinds = sorted({p['kind'] for p in places})
    kidx = {k: i for i, k in enumerate(kinds)}
    stats['kept'] = len(places)
    out = {
        'v': 1,
        'built': datetime.date.today().isoformat(),
        'release': release,
        'source': 'Overture Maps Foundation places (OpenStreetMap, Meta, Microsoft, Foursquare 등) · CDLA Permissive 2.0',
        'cats': CATS,
        'kinds': kinds,
        'p': [[p['name'], round(p['lat'] * 1e5), round(p['lng'] * 1e5), p['ci'], kidx[p['kind']], p['area']] for p in places],
    }
    return out, stats


def gh(level, msg):
    """GitHub Actions 주석(annotation)으로도 남겨서 API로 확인 가능하게"""
    msg = str(msg).replace('%', '%25').replace('\r', '').replace('\n', '%0A')
    print(f'::{level}::{msg}', flush=True)


def read_parquet_rows(path):
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    names = set(pf.schema_arrow.names)
    gh('notice', f'rows={pf.metadata.num_rows} columns={sorted(names)}')
    for col in ('categories', 'basic_category', 'taxonomy', 'bbox', 'names'):
        if col in names:
            gh('notice', f'{col}: {pf.schema_arrow.field(col).type}'[:900])
    want = [c for c in ('names', 'categories', 'basic_category', 'taxonomy', 'confidence', 'operating_status',
                        'addresses', 'bbox', 'geometry') if c in names]
    for batch in pf.iter_batches(batch_size=20000, columns=want):
        for r in batch.to_pylist():
            yield r


SAMPLES = []


def sampled(rows):
    for r in rows:
        if len(SAMPLES) < 3:
            SAMPLES.append({k: (str(v)[:160] if k != 'geometry' else '<wkb>') for k, v in r.items()})
        yield r


def main():
    src, dst = sys.argv[1], sys.argv[2]
    release = sys.argv[3] if len(sys.argv) > 3 else ''
    try:
        out, stats = build(sampled(read_parquet_rows(src)), release)
    except Exception:
        import traceback
        gh('error', traceback.format_exc()[-1500:])
        gh('notice', f'samples={SAMPLES}'[:1500])
        sys.exit(1)
    by_cat = {c: 0 for c in CATS}
    for r in out['p']:
        by_cat[CATS[r[3]]] += 1
    gh('notice', f"rows={stats['rows']} kept={stats['kept']} by_cat={by_cat}")
    if stats['kept'] < 1000:
        gh('notice', f'samples={SAMPLES}'[:1500])
        gh('error', f'too few places ({stats["kept"]}) - schema change? not writing output')
        sys.exit(1)
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('wrote', dst)


if __name__ == '__main__':
    main()
