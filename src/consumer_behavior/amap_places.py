"""Opt-in nearby POI lookup through Amap Web Service.

Browser geolocation supplies WGS84/GPS coordinates. They are converted by
Amap before nearby search. Coordinates and POIs are returned for this request
only; callers do not put them in the research session or model memory.
"""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


AMAP_ORIGIN = "https://restapi.amap.com"
TYPE_CODES = {"restaurant": "050000", "shopping": "060000"}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


class AmapNearbyProvider:
    def __init__(self, api_key, *, http_get=None):
        self.api_key = _text(api_key)
        self.http_get = http_get or self._http_get

    @classmethod
    def from_config(cls, config, root):
        settings = config.get("amap", {})
        key = _text(os.environ.get(settings.get("api_key_env", "AMAP_WEB_SERVICE_KEY")))
        if not key:
            path = Path(settings.get("api_key_file", "config/secrets.local.json"))
            try:
                values = json.loads((path if path.is_absolute() else Path(root) / path).read_text(encoding="utf-8-sig"))
                key = _text(values.get(settings.get("api_key_field", "amap_web_service_key")))
            except (OSError, ValueError, TypeError):
                pass
        return cls(key)

    @staticmethod
    def _http_get(url):
        try:
            with urlopen(url, timeout=8) as response:
                raw = response.read(1_000_001)
        except Exception as error:
            # urllib errors may contain the URL, including the key.
            raise RuntimeError("高德地点服务暂时无法连接") from error
        if len(raw) > 1_000_000:
            raise RuntimeError("高德地点服务返回内容过大")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise RuntimeError("高德地点服务返回格式无效") from error

    def _request(self, path, params):
        url = AMAP_ORIGIN + path + "?" + urlencode({**params, "key": self.api_key, "output": "JSON"})
        result = self.http_get(url)
        if not isinstance(result, dict) or str(result.get("status")) != "1":
            raise RuntimeError("高德地点查询失败，请检查 Web 服务 Key、额度和权限")
        return result

    def search(self, *, longitude, latitude, radius=2000, category="restaurant"):
        if not self.api_key:
            raise RuntimeError("请先在本机 config/secrets.local.json 配置 amap_web_service_key")
        if type(longitude) not in (int, float) or type(latitude) not in (int, float):
            raise ValueError("定位坐标无效")
        if not math.isfinite(longitude) or not math.isfinite(latitude) or not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("定位坐标超出范围")
        if type(radius) is not int or not 300 <= radius <= 5000:
            raise ValueError("搜索半径必须为300至5000米")
        if category not in TYPE_CODES:
            raise ValueError("地点类别未定义")

        gps = f"{longitude:.6f},{latitude:.6f}"
        converted = self._request("/v3/assistant/coordinate/convert", {"locations": gps, "coordsys": "gps"})
        location = _text(converted.get("locations")).split(";")[0]
        parts = location.split(",")
        try:
            converted_lng, converted_lat = (float(part) for part in parts)
        except (TypeError, ValueError):
            raise RuntimeError("高德坐标转换失败") from None
        if not (math.isfinite(converted_lng) and math.isfinite(converted_lat)
                and -180 <= converted_lng <= 180 and -90 <= converted_lat <= 90):
            raise RuntimeError("高德坐标转换失败")
        found = self._request("/v3/place/around", {"location": location, "radius": radius,
            "types": TYPE_CODES[category], "sortrule": "distance", "offset": 20,
            "page": 1, "extensions": "all"})
        pois = found.get("pois", [])
        if not isinstance(pois, list):
            raise RuntimeError("高德地点数据格式无效")
        items = []
        for poi in pois[:20]:
            if not isinstance(poi, dict) or not _text(poi.get("id")) or not _text(poi.get("name")):
                continue
            extension = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
            items.append({"id": _text(poi["id"]), "name": _text(poi["name"]),
                "address": _text(poi.get("address")) or "地址未提供",
                "distance_meters": _number(poi.get("distance")),
                "type": _text(poi.get("type")),
                "average_cost_cny": _number(extension.get("cost")),
                "source": "amap_poi", "price_basis": "per_person_estimate",
                "opening_status": "unknown", "ingredients_verified": False})
        return {"source": "amap_poi", "category": category, "radius_meters": radius,
            "fetched_at": datetime.now(timezone.utc).isoformat(), "items": items,
            "notice": "地点与距离来自高德；人均消费可能缺失或变化。营业、菜单、食材及消费券适用性未经核验。"}
