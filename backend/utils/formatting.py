import datetime


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return ["th", "st", "nd", "rd", "th"][min(n % 10, 4)]


def _format_date_human(local: datetime.datetime, now: datetime.datetime) -> str:
    delta = (local.date() - now.date()).days
    day_num = local.day
    ordinal_day = f"{day_num}{_ordinal(day_num)}"
    month_year = local.strftime("%B %Y")

    if delta == 1:
        date_str = f"tomorrow the {ordinal_day} of {month_year}"
    elif delta == 2:
        date_str = f"day after tomorrow the {ordinal_day} of {month_year}"
    elif delta < 7:
        date_str = f"{local.strftime('%A')} the {ordinal_day} of {month_year}"
    else:
        date_str = f"the {ordinal_day} of {month_year}"

    return f"{date_str} at {local.strftime('%I:%M %p')}"
