import re

__all__ = ["normalize_html_entities"]

def normalize_html_entities(s):
    def repl(match):
        # TODO: add some error checking
        if match.group(1):
            return chr(int(match.group(2), 16))
        return chr(int(match.group(2)))
    return re.sub(r"&#(x?)([0-9a-fA-F]+);", repl, s)
