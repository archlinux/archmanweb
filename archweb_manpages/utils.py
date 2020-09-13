import re
import textwrap

from django.urls import reverse
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

def reverse_man_url(repo, pkgname, man_name, man_section, man_lang, content_type):
    # django's reverse function can't reverse our regexes, so we're doing it the old way
    url = reverse("index") + "man/"
    if repo:
        url += repo + "/"
    if pkgname:
        url += pkgname + "/"
    url += man_name
    if man_section:
        url += "." + man_section
    if man_lang:
        url += "." + man_lang
    if content_type:
        url += "." + content_type
    return url

def paginate(request, url_param, query, limit):
    paginator = Paginator(query, limit)
    page = request.GET.get(url_param)
    try:
        query = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver the first page.
        query = paginator.page(1)
    except EmptyPage:
        # If page is out of range, deliver the last page.
        query = paginator.page(paginator.num_pages)
    return query

def postprocess(text, content_type, lang):
    assert content_type in {"html", "txt"}
    if content_type == "html":
        # replace references with links
        xref_pattern = re.compile(r"\<(?P<tag>b|i|strong|em|mark)\>"
                                  r"(?P<man_name>[A-Za-z0-9@._+\-:\[\]]+)"
                                  r"\<\/\1\>"
                                  r"\((?P<section>\d[a-z]{,3})\)")
        text = xref_pattern.sub("<a href='" + reverse("index") + "man/" + r"\g<man_name>.\g<section>." + lang +
                                        "'>\g<man_name>(\g<section>)</a>",
                                text)

        # remove empty tags
        text = re.sub(r"\<(?P<tag>[^ >]+)[^>]*\>(\s|&nbsp;)*\</(?P=tag)\>\n?", "", text)

        # strip leading and trailing newlines and remove common indentation
        # from the text inside <pre> tags
        _pre_tag_pattern = re.compile(r"\<pre\>(.+?)\</pre\>", flags=re.DOTALL)
        text = _pre_tag_pattern.sub(lambda match: "<pre>" + textwrap.dedent(match.group(1).strip("\n")) + "</pre>", text)

        # remove <br/> tags following a <pre> or <div> tag
        text = re.sub(r"(?<=\</(pre|div)\>)\n?<br/>", "", text)

        # replace URLs in plain-text with <a> links
        def repl_url(match):
            url = match.group("url")
            if not url:
                return match.group(0)
            return f"<a href='{url}'>{url}</a>"
        skip_tags_pattern = r"\<(?P<skip_tag>a|pre)[^>]*\>.*?\</(?P=skip_tag)\>"
        url_pattern = r"(?P<url>https?://[^\s<>&]+(?<=[\w/]))"
        surrounding_tag_begin = r"(?P<tag_begin>\<(?P<tag>b|i|strong|em|mark)[^>]*\>\s*)?"
        surrounding_tag_end = r"(?(tag_begin)\s*\</(?P=tag)\>|)"
        surrounding_angle_begin = r"(?P<angle>&lt;)?"
        surrounding_angle_end = r"(?(angle)&gt;|)"
        text = re.sub(f"{skip_tags_pattern}|{surrounding_angle_begin}{surrounding_tag_begin}{url_pattern}{surrounding_tag_end}{surrounding_angle_end}",
                      repl_url, text, flags=re.DOTALL)
        # if the URL is the only text in <pre> tags, it gets replaced
        text = re.sub(f"<pre>\s*{url_pattern}\s*</pre>",
                      repl_url, text, flags=re.DOTALL)

        return text

    elif content_type == "txt":
        # strip mandoc's back-spaced encoding
        return re.sub(".\b", "", text, flags=re.DOTALL)

def normalize_html_entities(s):
    def repl(match):
        # TODO: add some error checking
        if match.group(1):
            return chr(int(match.group(2), 16))
        return chr(int(match.group(2)))
    return re.sub(r"&#(x?)([0-9a-fA-F]+);", repl, s)

def extract_headings(html):
    def normalize(title):
        return re.sub(r"\s+", " ", title)
    result = []
    headings_pattern = re.compile(r"\<h1[^\>]*\>[^\<\>]*"
                                  r"\<a class=(\"|\')permalink(\"|\') href=(\"|\')#(?P<id>\S+)(\"|\')\>"
                                  r"(?P<title>.+?)"
                                  r"\<\/a\>[^\<\>]*"
                                  r"\<\/h1\>", re.DOTALL)
    for match in headings_pattern.finditer(html):
        id = normalize_html_entities(match.group("id"))
        title = normalize_html_entities(normalize(match.group("title")))
        result.append(dict(id=id, title=title))
    return result

def extract_description(text):
    desc_gen = re.finditer(r"(?<=^NAME$)(?P<description>.+?)(?=^\w)", text, flags=re.MULTILINE | re.DOTALL)
    try:
        description = next(desc_gen).group("description")
    except StopIteration:
        return None
    return textwrap.dedent(description.strip("\n"))
