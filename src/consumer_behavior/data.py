"""Bounded local CSV/JSONL inspection; never loads an entire TaobaoAd file."""
from pathlib import Path
from collections import Counter
import csv
import hashlib
import json
import math

from .features import PROFILE_FIELDS, ITEM_FIELDS, sequence, token


def read_rows(path: str | Path, limit: int = 50000) -> list[dict]:
    if limit <= 0:
        raise ValueError("样本上限必须为正整数")
    path = Path(path)
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if path.suffix.lower() == ".csv":
            reader = csv.DictReader(stream)
        elif path.suffix.lower() == ".jsonl":
            reader = (json.loads(line) for line in stream if line.strip())
        else:
            raise ValueError("当前支持 CSV 和 JSONL 本地文件")
        for row in reader:
            if not isinstance(row, dict):
                raise ValueError("每条数据必须是字段字典")
            rows.append(row)
            if len(rows) >= limit:
                break
    if not rows:
        raise ValueError("数据为空")
    return rows


def label(row):
    value = token(row.get("clk"))
    if value not in ("0", "1"):
        raise ValueError(f"clk 必须是明确的二元点击标签，收到 {value!r}")
    return int(value)


def fingerprint(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def unique_rows(rows):
    seen, result = set(), []
    for row in rows:
        key = fingerprint(row)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def inspect_rows(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("数据为空")
    fields = sorted({key for row in rows for key in row})
    counts = Counter(label(row) for row in rows)
    prices = []
    invalid_price = 0
    for row in rows:
        try:
            price = float(row["price"])
            if not math.isfinite(price) or not 0 <= price <= 1:
                raise ValueError
            prices.append(price)
        except (KeyError, TypeError, ValueError):
            invalid_price += 1
    history_report = {}
    for key in ("cate_his", "brand_his", "btag_his"):
        lengths = [len(sequence(row.get(key))) for row in rows]
        history_report[key] = {"min_length": min(lengths), "max_length": max(lengths),
            "empty": lengths.count(0), "over_50": sum(length > 50 for length in lengths),
            "separator": "^"}
    required = set(PROFILE_FIELDS + ITEM_FIELDS + ("price", "clk"))
    return {"rows": len(rows), "fields": fields, "missing_expected_fields": sorted(required - set(fields)),
        "label_counts": {str(key): value for key, value in counts.items()},
        "click_rate": counts[1] / len(rows),
        "missing_values": {key: sum(token(row.get(key)) is None for row in rows) for key in fields},
        "duplicate_rows": len(rows) - len(unique_rows(rows)),
        "users": len({token(row.get("userid")) for row in rows} - {None}),
        "items": len({token(row.get("adgroup_id")) for row in rows} - {None}),
        "price": {"min": min(prices) if prices else None, "max": max(prices) if prices else None,
                  "invalid_or_missing": invalid_price, "is_cny": False,
                  "encoding": "taobaoad_x1_scaled", "inverse_transform_verified": False},
        "history": history_report, "timestamp_present": "time_stamp" in fields,
        "history_future_leakage_verified": False,
        "sample": rows[0],
        "limitations": ["有界前缀抽样，可能受到文件排序影响；统计不是全量分布",
                        "没有逐条历史时间戳时不能证明序列不含未来信息",
                        "clk 只表示广告曝光后的点击，不能推断购买或政策效果"]}
