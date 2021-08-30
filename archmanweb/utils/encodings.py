import re
import unicodedata

__all__ = ["normalize_html_entities", "safe_escape_attribute", "anchorencode_id", "anchorencode_href"]

def normalize_html_entities(s):
    def repl(match):
        # TODO: add some error checking
        if match.group(1):
            return chr(int(match.group(2), 16))
        return chr(int(match.group(2)))
    return re.sub(r"&#(x?)([0-9a-fA-F]+);", repl, s)

# escape sensitive characters when formatting an element attribute
# https://stackoverflow.com/a/7382028
def safe_escape_attribute(attribute):
    escape_map = {
        "<"  : "&lt;",
        ">"  : "&gt;",
        "\"" : "&quot;",
        "'"  : "&apos;",
        "&"  : "&amp;",
    }
    return "".join(escape_map.get(c, c) for c in attribute)

# function copied from wiki-scripts:
# https://github.com/lahwaacz/wiki-scripts/blob/master/ws/parser_helpers/encodings.py#L81-L98
def _anchor_preprocess(str_):
    """
    Context-sensitive pre-processing for anchor-encoding. See `MediaWiki`_ for
    details.

    .. _`MediaWiki`: https://www.mediawiki.org/wiki/Manual:PAGENAMEE_encoding
    """
    # underscores are treated as spaces during this pre-processing, so they are
    # converted to spaces first (the encoding later converts them back)
    str_ = str_.replace("_", " ")
    # strip leading + trailing whitespace
    str_ = str_.strip()
    # squash *spaces* in the middle (other whitespace is preserved)
    str_ = re.sub("[ ]+", " ", str_)
    # leading colons are stripped, others preserved (colons in the middle preceded by
    # newline are supposed to be fucked up in MediaWiki, but this is pretty safe to ignore)
    str_ = str_.lstrip(":")
    return str_

# adapted from `anchorencode` in wiki-scripts (the "legacy" format was removed):
# https://github.com/lahwaacz/wiki-scripts/blob/master/ws/parser_helpers/encodings.py#L119-L152
def anchorencode_id(str_):
    """
    anchorencode_id avoids percent-encoding to keep the id readable
    """
    str_ = _anchor_preprocess(str_)
    # HTML5 specification says ids must not contain spaces
    str_ = re.sub("[ \t\n\r\f\v]", "_", str_)
    return str_

# adapted from `anchorencode` in wiki-scripts (the "legacy" format was removed):
# https://github.com/lahwaacz/wiki-scripts/blob/master/ws/parser_helpers/encodings.py#L119-L152
def anchorencode_href(str_, *, input_is_already_id=False):
    """
    anchorencode_href does some percent-encoding on top of anchorencode_id to
    increase compatibility (The id can be linked with "#id" as well as with
    "#percent-encoded-id", since the browser does the percent-encoding in the
    former case. But if we used percent-encoded ids in the first place, only
    the links with percent-encoded fragments would work.)
    """
    if input_is_already_id is False:
        str_ = anchorencode_id(str_)
    # encode "%" from percent-encoded octets
    str_ = re.sub(r"%([a-fA-F0-9]{2})", r"%25\g<1>", str_)
    # encode sensitive characters - the output of this function should be usable
    # in various markup languages (MediaWiki, FluxBB, etc.)
    encode_chars = "[]|"

    escape_char = "%"
    charset = "utf-8"
    errors = "strict"
    output = ""
    for char in str_:
        # encode characters from encode_chars and the Separator and Other categories
        # https://en.wikipedia.org/wiki/Unicode#General_Category_property
        if char in encode_chars or unicodedata.category(char)[0] in {"Z", "C"}:
            for byte in bytes(char, charset, errors):
                output += "{}{:02X}".format(escape_char, byte)
        else:
            output += char
    return output
