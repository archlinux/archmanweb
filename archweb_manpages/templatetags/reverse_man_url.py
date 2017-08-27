from django import template

from ..utils import reverse_man_url as _reverse

register = template.Library()

@register.simple_tag
def reverse_man_url(repo, pkgname, man_name, man_section, man_lang, output_type):
    return _reverse(repo, pkgname, man_name, man_section, man_lang, output_type)
