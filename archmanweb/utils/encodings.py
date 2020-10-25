import re
import unicodedata

__all__ = ["normalize_html_entities", "anchorencode"]

def normalize_html_entities(s):
    def repl(match):
        # TODO: add some error checking
        if match.group(1):
            return chr(int(match.group(2), 16))
        return chr(int(match.group(2)))
    return re.sub(r"&#(x?)([0-9a-fA-F]+);", repl, s)

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

# function copied from wiki-scripts (and the "legacy" format was removed):
# https://github.com/lahwaacz/wiki-scripts/blob/master/ws/parser_helpers/encodings.py#L119-L152
def anchorencode(str_):
    """
    Function corresponding to the ``{{anchorencode:}}`` `magic word`_.

    Note that the algorithm corresponds to the ``"html5"`` MediaWiki mode,
    see `$wgFragmentMode`_.

    :param str_: the string to be encoded

    .. _`magic word`: https://www.mediawiki.org/wiki/Help:Magic_words
    .. _`$wgFragmentMode`: https://www.mediawiki.org/wiki/Manual:$wgFragmentMode
    """
    str_ = _anchor_preprocess(str_)
    special_map = {" ": "_"}
    escape_char = "."
    charset = "utf-8"
    errors = "strict"
    # below is the code from the encode function, but without the encode_chars
    # and skip_chars parameters, and adjusted for unicode categories
    output = ""
    for char in str_:
        # encode only characters from the Separator and Other categories
        # https://en.wikipedia.org/wiki/Unicode#General_Category_property
        if unicodedata.category(char)[0] in {"Z", "C"}:
            if special_map is not None and char in special_map:
                output += special_map[char]
            else:
                for byte in bytes(char, charset, errors):
                    output += "{}{:02X}".format(escape_char, byte)
        else:
            output += char
    return output
