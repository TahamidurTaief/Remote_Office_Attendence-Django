from django import template
from apps.accounts.engine import PermissionEngine
from apps.accounts.permissions import has_perm_cached

register = template.Library()


@register.simple_tag(takes_context=True)
def has_permission(context, codename, required_scope=None, action_type='view'):
    """
    Template tag to check if current user has permission via PermissionEngine.
    Usage:
        {% has_permission 'projects.view' as can_view_projects %}
        {% if can_view_projects %} ... {% endif %}
    """
    request = context.get('request')
    if not request or not getattr(request, 'user', None) or not request.user.is_authenticated:
        return False
    return PermissionEngine.evaluate(
        user=request.user,
        codename=codename,
        required_scope=required_scope,
        action_type=action_type
    ).allowed


@register.filter(name='has_perm')
def has_perm_filter(user, codename):
    """
    Template filter: {{ request.user|has_perm:'projects.edit' }}
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return has_perm_cached(user, codename)


@register.filter(name='user_can')
def user_can_filter(user, codename):
    """
    Alias filter: {{ request.user|user_can:'projects.delete' }}
    """
    return has_perm_filter(user, codename)
