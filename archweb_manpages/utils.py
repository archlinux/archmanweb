import re

from django.urls import reverse

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

# very slow on long pages, but we keep it if we need to validate the regex-only version below
def postprocess_bs(html, lang):
    import bs4

#    soup = bs4.BeautifulSoup(html, "html.parser")
    soup = bs4.BeautifulSoup(html, "lxml")

    # TODO: bind to database query?
    section_names = {"0", "0p", "1", "1p", "2", "3", "3p", "4", "5", "6", "7", "8", "9", "n"}
    section_pattern = r"^\((?P<section>" + "|".join(section_names) + r")\)"

    def replace_link(tag, following, prefix, man_section):
        man_name = tag.string

        # change tag into <a>
        tag.name = "a"
        tag["href"] = reverse_man_url("", "", man_name, man_section, lang, "")
#        tag["class"] = "Xr"
        tag.string = "{}({})".format(man_name, man_section)

        # strip section from the following text
        text = following[len(prefix):]
        following.replaceWith(text)

    for tag in soup.find_all(["b", "i", "strong", "em", "mark"]):
        following = tag.next_sibling
        # check if followed by plain text
        if following is not None and following.name is None:
            # check if the following text looks like a section
            match = re.match(section_pattern, following.string)
            if match:
                prefix = match.group(0)
                section = match.group("section")
                replace_link(tag, following, prefix, section)

    # return the content of the <body> tag
    return soup.body.decode_contents(formatter="html")

_xref_pattern = re.compile(r"\<(?P<tag>b|i|strong|em|mark)\>"
                           r"(?P<man_name>[A-Za-z0-9@._+\-:\[\]]+)"
                           r"\<\/\1\>"
                           r"\((?P<section>\d[a-z]{,3})\)")
def postprocess(html, lang):
    new_html = _xref_pattern.sub("<a href='" + reverse("index") + "man/" + r"\g<man_name>.\g<section>." + lang +
                                        "'>\g<man_name>(\g<section>)</a>",
                                html)
    return new_html

_norm_pattern = re.compile(r"\s+")
_headings_pattern = re.compile(r"\<h1[^\>]*\>[^\<\>]*"
                               r"\<a class=(\"|\')selflink(\"|\') href=(\"|\')#(?P<id>[A-Za-z0-9\-_\.]+)(\"|\')\>"
                               r"(?P<title>.+?)"
                               r"\<\/a\>[^\<\>]*"
                               r"\<\/h1\>", re.DOTALL)
def extract_headings(html):
    def normalize(title):
        return _norm_pattern.sub(" ", title)
    result = []
    for match in _headings_pattern.finditer(html):
        result.append(dict(id=match.group("id"), title=normalize(match.group("title"))))
    return result
