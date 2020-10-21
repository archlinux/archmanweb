from django import template
from django.utils.html import format_html, mark_safe

register = template.Library()

@register.simple_tag
def pagination_links(request, paginator, query_string_param):
    html = '<nav class="pagination">\n'

    # link to the first page
    if paginator.number > 1:
        q = request.GET.copy()
        q[query_string_param] = "1"
        html += format_html('<a href="?{}">first</a>\n', q.urlencode())

    # link to the previous page
    if paginator.has_previous():
        q = request.GET.copy()
        q[query_string_param] = str(paginator.previous_page_number())
        html += format_html('<a href="?{}">previous</a>\n', q.urlencode())

    # current/total page numbers
    html += format_html('<span class="current">page {} of {}</span>\n', paginator.number, paginator.paginator.num_pages)

    # link to the next page
    if paginator.has_next():
        q = request.GET.copy()
        q[query_string_param] = str(paginator.next_page_number())
        html += format_html('<a href="?{}">next</a>\n', q.urlencode())

    # link to the last page
    if paginator.number < paginator.paginator.num_pages:
        q = request.GET.copy()
        q[query_string_param] = str(paginator.paginator.num_pages)
        html += format_html('<a href="?{}">last</a>\n', q.urlencode())

    html += '</nav>\n'
    return mark_safe(html)
