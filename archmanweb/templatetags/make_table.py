import datetime

from django import template
from django.utils.html import format_html, mark_safe

register = template.Library()

# taken from https://stackoverflow.com/a/30339105
def format_timedelta(value, time_format="{days} days, {hours2}:{minutes2}:{seconds2}"):
    if hasattr(value, 'seconds'):
        seconds = value.seconds + value.days * 24 * 3600
    else:
        seconds = int(value)

    seconds_total = seconds

    minutes = int(seconds / 60)
    minutes_total = minutes
    seconds -= minutes * 60

    hours = int(minutes / 60)
    hours_total = hours
    minutes -= hours * 60

    days = int(hours / 24)
    hours -= days * 24

    return time_format.format(**{
        'seconds': seconds,
        'seconds2': str(seconds).zfill(2),
        'minutes': minutes,
        'minutes2': str(minutes).zfill(2),
        'hours': hours,
        'hours2': str(hours).zfill(2),
        'days': days,
        'seconds_total': seconds_total,
        'minutes_total': minutes_total,
        'hours_total': hours_total,
    })

@register.simple_tag
def make_table(rows, class_=None):
    # we need to access the first row twice, so this way we avoid making separate SQL query
    rows = list(rows)

    if not rows:
        return ""

    config = rows[0].HtmlTableConfig
    columns = config.columns
    descriptions = config.descriptions

    if class_ is None:
        html = '<table>\n'
    else:
        html = format_html('<table class="{}">\n', class_)

    # header
    html += '<thead>\n'
    html += '<tr>\n'
    for desc in descriptions:
        html += format_html('<th>{}</th>\n', desc)
    html += '</tr>\n'
    html += '</thead>\n'

    # body
    html += '<tbody>\n'
    for row in rows:
        html += '<tr>\n'
        for col in columns:
            value = getattr(row, col)
            if isinstance(value, datetime.datetime):
                value = value.strftime("%F %T")
            elif isinstance(value, datetime.timedelta):
                value = format_timedelta(value, time_format="{hours2}:{minutes2}:{seconds2}")
            html += format_html('<td>{}</td>\n', value)
        html += '</tr>\n'
    html += '</tbody>\n'

    html += '</table>\n'
    return mark_safe(html)
