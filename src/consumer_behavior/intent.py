from datetime import datetime, timedelta, timezone


def meal_intent(*, now: str, explicit_hunger: bool | None = None,
                last_meal_at: str | None = None, usual_dinner_time: str | None = None) -> dict:
    current = datetime.fromisoformat(now)
    if current.tzinfo is None:
        raise ValueError("当前时间必须带时区")
    if explicit_hunger is not None and type(explicit_hunger) is not bool:
        raise ValueError("饥饿确认必须为布尔值或未知")
    elapsed = None
    if last_meal_at is not None:
        previous = datetime.fromisoformat(last_meal_at)
        if previous.tzinfo is None or previous > current:
            raise ValueError("上次进餐时间必须带时区，且不晚于当前时间")
        elapsed = (current - previous).total_seconds() / 3600
    if explicit_hunger is not None:
        return {"intent": "dining" if explicit_hunger else "none", "confirmed": True,
                "requires_confirmation": False, "source": "user_confirmation", "hours_since_last_meal": elapsed}
    local = current.astimezone(timezone(timedelta(hours=8)))
    expected = usual_dinner_time or "18:00"
    hour, minute = [int(part) for part in expected.split(":")]
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("无效晚餐时间")
    minutes = abs((local.hour * 60 + local.minute) - (hour * 60 + minute))
    minutes = min(minutes, 1440 - minutes)
    suggested = minutes <= 60 and (elapsed is None or elapsed >= 3)
    return {"intent": "possible_dining" if suggested else "none", "confirmed": False,
            "requires_confirmation": suggested, "source": "schedule_heuristic",
            "hours_since_last_meal": elapsed,
            "message": "现在可能到了用餐时间，要看看附近的餐厅吗？" if suggested else None}
