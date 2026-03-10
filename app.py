from flask import Flask, render_template, jsonify, request
import math
import random
import logging
import requests
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 기존 파노라마 샘플 (백업/예제용)
PANORAMAS = [
    {
        "id": "YJ767qz2dZIIUi8zt2wjPw",
        "image_url": "https://panorama.pstatic.net/imageV3/YJ767qz2dZIIUi8zt2wjPw/P",
        "lat": 37.5665,
        "lng": 126.9780,
        "hint": "서울 시청 근처",
    },
]


def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def calc_score(distance_km):
    max_score = 5000
    # 0km = 5000점, 500km+ = 0점
    return max(0, int(max_score * math.exp(-distance_km / 100)))


# South Korea mainland + Jeju simplified polygon coordinates
# (longitude, latitude) pairs
SOUTH_KOREA_POLYGONS = [
    # Mainland (very rough simplified polygon)
    [
        (126.10, 38.61), (126.94, 38.61), (127.78, 38.31), (128.37, 38.61),
        (128.66, 38.37), (129.07, 38.07), (129.41, 37.07), (129.58, 36.05),
        (129.43, 35.50), (129.35, 35.16), (129.08, 35.10), (128.57, 34.88),
        (128.04, 35.05), (127.57, 34.62), (127.30, 34.55), (126.53, 34.34),
        (126.12, 34.54), (126.26, 34.82), (126.43, 35.13), (126.15, 35.10),
        (125.95, 35.33), (126.05, 35.57), (126.34, 35.68), (126.48, 36.02),
        (126.05, 36.07), (125.99, 36.41), (126.05, 36.58), (126.13, 36.69),
        (126.19, 36.79), (126.33, 36.88), (126.05, 37.02), (126.05, 37.25),
        (126.39, 37.39), (126.22, 37.59), (126.55, 37.69), (126.35, 37.77),
        (126.60, 37.94), (126.10, 38.05), (126.10, 38.61),
    ],
    # Jeju Island
    [
        (126.16, 33.19), (126.26, 33.14), (126.57, 33.22),
        (126.88, 33.28), (126.96, 33.42), (126.72, 33.56),
        (126.36, 33.52), (126.16, 33.35), (126.16, 33.19),
    ],
]


_polygons = [Polygon(coords) for coords in SOUTH_KOREA_POLYGONS]
KOREA_SHAPE = unary_union(_polygons)
BOUNDS = KOREA_SHAPE.bounds  # (minx, miny, maxx, maxy)


def random_point_in_korea() -> tuple[float, float]:
    """Generate a random point within South Korea's borders.
    Returns (lat, lon)
    """
    minx, miny, maxx, maxy = BOUNDS
    while True:
        lon = random.uniform(minx, maxx)
        lat = random.uniform(miny, maxy)
        if KOREA_SHAPE.contains(Point(lon, lat)):
            return round(lat, 6), round(lon, 6)


@app.route("/")
def index():
    return render_template("index.html")


NAVER_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "ko-KR,ko;q=0.8,en-US;q=0.6,en;q=0.4",
    "cache-control": "no-cache",
    "referer": "https://map.naver.com/p?c=9.65,0,0,0,adh",
}

MAX_RETRIES = 10


def fetch_panorama(lon: float, lat: float) -> dict | None:
    """Fetch nearby panorama for given coordinates. Returns dict with keys:
    id, image_url, description, lat, lng
    or None if not found.
    """
    naver_url = f"https://map.naver.com/p/api/panorama/nearby/{lon}/{lat}"
    logger.info(f"[Panorama] Requesting: {naver_url}")
    try:
        resp = requests.get(naver_url, headers=NAVER_HEADERS, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            features = data.get("features", [])
            if features:
                feat = features[0]
                props = feat.get("properties", {})
                geom = feat.get("geometry", {})
                coords = geom.get("coordinates") if geom else None
                if coords and len(coords) >= 2:
                    pano_lon, pano_lat = coords[0], coords[1]
                else:
                    pano_lat, pano_lon = lat, lon

                pid = props.get("id")
                if not pid:
                    return None

                logger.info(f"[Panorama] Found: {pid} - {props.get('description')}")
                return {
                    "id": pid,
                    "image_url": f"https://panorama.pstatic.net/image/{pid}/512/P",
                    "description": props.get("description", ""),
                    "lat": pano_lat,
                    "lng": pano_lon,
                }
        logger.info(f"[Panorama] No panorama at {lat}, {lon}")
    except Exception as e:
        logger.error(f"[Panorama] Error: {e}")
    return None


@app.route("/api/round")
def get_round():
    """Return a random panorama (try N attempts). When found, register it
    in PANORAMAS so `/api/guess` can resolve it later.
    """
    for attempt in range(MAX_RETRIES):
        lat, lon = random_point_in_korea()
        props = fetch_panorama(lon, lat)
        if props:
            # register panorama for guessing
            PANORAMAS.append({
                "id": props["id"],
                "image_url": props["image_url"],
                "lat": props["lat"],
                "lng": props["lng"],
                "hint": props.get("description", ""),
            })
            return jsonify({"id": props["id"], "image_url": props["image_url"]})
        logger.info(f"[Panorama] Retry {attempt + 1}/{MAX_RETRIES}")

    # Fallback: return a sample pano
    pano = random.choice(PANORAMAS)
    return jsonify({"id": pano["id"], "image_url": pano["image_url"]})


@app.route("/api/guess", methods=["POST"])
def guess():
    data = request.json
    pano_id = data.get("pano_id")
    guess_lat = data.get("lat")
    guess_lng = data.get("lng")

    pano = next((p for p in PANORAMAS if p["id"] == pano_id), None)
    if not pano:
        return jsonify({"error": "invalid panorama"}), 400

    distance = haversine(guess_lat, guess_lng, pano["lat"], pano["lng"])
    score = calc_score(distance)

    return jsonify(
        {
            "actual_lat": pano["lat"],
            "actual_lng": pano["lng"],
            "distance_km": round(distance, 1),
            "score": score,
            "hint": pano.get("hint", ""),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
