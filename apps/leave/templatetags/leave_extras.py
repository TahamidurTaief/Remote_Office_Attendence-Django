from django import template

register = template.Library()

@register.filter(name='dictget')
def dictget(dictionary, key):
    if not isinstance(dictionary, dict):
        return None
    return dictionary.get(key)
