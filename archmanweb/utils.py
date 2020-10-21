import subprocess
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

def mandoc_convert(content, output_type, lang=None):
    if output_type == "html":
        url_pattern = reverse_man_url("", "", "%N", "%S", lang, "")
        cmd = "mandoc -T html -O fragment,man={}".format(url_pattern)
    elif output_type == "txt":
        cmd = "mandoc -T utf8"
    p = subprocess.run(cmd, shell=True, check=True, input=content, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert p.stdout
    return p.stdout

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

def extract_description(text, lang="en"):
    """
    Extracts the "description" from a plain-text version of a manual page.

    The description is taken from the NAME section (or a hard-coded list of
    translations for non-English manuals). At most 2 paragraphs, one of which
    is usually the one-line description of the manual, are taken to keep the
    description short.

    Note that NAME does not have to be the first section, see e.g. syslog.h(0P).
    """
    dictionary = {
        "ar": "الاسم",
        "bn": "নাম",
        "ca": "NOM",
        "cs": "JMÉNO|NÁZEV",
        "da": "NAVN",
        "de": "BEZEICHNUNG",
        "el": "ΌΝΟΜΑ",
        "eo": "NOMO",
        "es": "NOMBRE",
        "et": "NIMI",
        "fi": "NIMI",
        "fr": "NOM",
        "gl": "NOME",
        "hr": "IME",
        "hu": "NÉV",
        "id": "NAMA",
        "it": "NOME",
        "ja": "名前",
        "ko": "이름",
        "lt": "PAVADINIMAS",
        "nb": "NAVN",
        "nl": "NAAM",
        "pl": "NAZWA",
        "pt": "NOME",
        "ro": "NUME",
        "ru": "ИМЯ|НАЗВАНИЕ",
        "sk": "NÁZOV",
        "sl": "IME",
        "sr": "НАЗИВ|ИМЕ|IME",
        "sv": "NAMN",
        "ta": "பெயர்",
        "tr": "İSİM|AD",
        "uk": "НАЗВА|НОМИ|NOMI",
        "vi": "TÊN",
        "zh": "名称|名字|名称|名稱",
    }
    lang = lang.split("_")[0].split("@")[0]
    name = dictionary.get(lang, "NAME")
    if name != "NAME":
        name = "NAME|" + name
    match = re.search(rf"(^{name}$)(?P<description>.+?)(?=^\S)", text, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if match is None:
        return None
    description = match.group("description")
    description = textwrap.dedent(description.strip("\n"))
    # keep max 2 paragraphs separated by a blank line
    # (some pages contain a lot of text in the NAME section, e.g. owncloud(1) or qwtlicense(3))
    description = "\n\n".join(description.split("\n\n")[:2])
    return description
