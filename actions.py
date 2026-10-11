from datetime import datetime


class InvalidAction(ValueError):
    pass


def validate_action(value):
    if not isinstance(value, dict):
        raise InvalidAction("Mesajı anlayamadım. Tarih ve saati açıkça yazar mısın?")
    result = dict(value)
    action = result.get("action")
    if not isinstance(action, str) or action not in {"add_event", "add_multi_event", "update_event", "delete_event", "list_events", "chat"}:
        raise InvalidAction("Bu işlemi anlayamadım.")
    if action == "chat":
        if not isinstance(result.get("reply"), str) or not result["reply"].strip():
            raise InvalidAction("Mesajı anlayamadım.")
        result["reply"] = result["reply"][:3500]
        return result
    if action == "list_events":
        days = result.get("days_ahead", 7)
        if type(days) is not int or not 0 <= days <= 365:
            raise InvalidAction("Listeleme aralığı 0–365 gün olmalı.")
        result["days_ahead"] = days
        return result
    title = result.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise InvalidAction("Etkinlik başlığını açıkça yazar mısın?")
    result["title"] = title.strip()
    if action == "delete_event":
        return result
    for field in ("description", "new_title"):
        if field in result and (not isinstance(result[field], str) or len(result[field]) > 2000):
            raise InvalidAction("Başlık ve açıklama metin olmalı.")
    if "all_day" in result and type(result["all_day"]) is not bool:
        raise InvalidAction("Saat bilgisini anlayamadım. Tekrar yazar mısın?")
    if action.startswith("add_"):
        if "all_day" not in result:
            raise InvalidAction("Etkinliğin saatini belirt veya 'tüm gün' yaz.")
        result.setdefault("description", "")
    all_day = result.get("all_day", False)
    if action == "add_multi_event":
        dates = result.get("dates")
        if not isinstance(dates, list) or not 1 <= len(dates) <= 31:
            raise InvalidAction("Bir seferde 1–31 gün ekleyebilirsin.")
        for date in dates:
            _date(date, "%Y-%m-%d")
        result["dates"] = list(dict.fromkeys(dates))
        if not all_day:
            _date(result.get("time"), "%H:%M")
        if result.get("recurrence"):
            raise InvalidAction("Tekrarlayan etkinliği tek bir başlangıç tarihiyle belirt.")
    elif action == "add_event" or "datetime" in result:
        fmt = "%Y-%m-%d" if all_day else "%Y-%m-%d %H:%M"
        date_value = result.get("datetime")
        if all_day and isinstance(date_value, str):
            date_value = date_value.split(" ")[0]
        _date(date_value, fmt)
        result["datetime"] = date_value
    if "reminder_minutes" in result:
        minutes = result["reminder_minutes"]
        if not isinstance(minutes, list) or len(minutes) > 5 or any(
            type(m) is not int or not 0 <= m <= 10080 for m in minutes
        ):
            raise InvalidAction("En fazla 5 bildirim seçebilirsin; süreler 0–10080 dakika olmalı.")
        if all_day and minutes:
            raise InvalidAction("Bildirim için etkinliğe bir saat eklemelisin.")
        result["reminder_minutes"] = sorted(set(minutes))
    elif action.startswith("add_"):
        result["reminder_minutes"] = [] if all_day else [0]
    if "recurrence" in result:
        rule = result["recurrence"]
        if rule is not None:
            if not isinstance(rule, dict) or set(rule) - {"freq", "interval", "byday", "until", "count"}:
                raise InvalidAction("Tekrar kuralını anlayamadım.")
            if not isinstance(rule.get("freq"), str) or rule.get("freq") not in {"DAILY", "WEEKLY", "MONTHLY"}:
                raise InvalidAction("Günlük, haftalık veya aylık tekrar kullanabilirsin.")
            if type(rule.get("interval", 1)) is not int or not 1 <= rule.get("interval", 1) <= 365:
                raise InvalidAction("Tekrar aralığı 1–365 olmalı.")
            if "byday" in rule and (
                rule["freq"] != "WEEKLY" or not isinstance(rule["byday"], list)
                or not rule["byday"] or len(rule["byday"]) > 7
                or any(not isinstance(d, str) or d not in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"} for d in rule["byday"])
            ):
                raise InvalidAction("Haftalık tekrarın günlerini anlayamadım.")
            if "count" in rule and (type(rule["count"]) is not int or not 1 <= rule["count"] <= 1000):
                raise InvalidAction("Tekrar sayısı 1–1000 olmalı.")
            if "until" in rule:
                _date(rule["until"], "%Y-%m-%d")
            if "until" in rule and "count" in rule:
                raise InvalidAction("Tekrar için bitiş tarihi veya tekrar sayısı seç.")
    if action == "update_event":
        fields = {"new_title", "description", "datetime", "recurrence", "reminder_minutes"}
        if not fields.intersection(result):
            raise InvalidAction("Etkinlikte neyi değiştireceğini belirtir misin?")
        if "all_day" in result and "datetime" not in result:
            raise InvalidAction("Tüm gün/saat değişikliği için yeni tarih de gerekli.")
        if "new_title" in result and (not result["new_title"].strip() or len(result["new_title"]) > 200):
            raise InvalidAction("Yeni başlık 1–200 karakter olmalı.")
    return result


def _date(value, fmt):
    try:
        parsed = datetime.strptime(value, fmt)
        if parsed.strftime(fmt) != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise InvalidAction("Geçerli bir tarih/saat belirtir misin? Örnek: yarın saat 15:00.") from None
