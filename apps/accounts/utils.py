"""
Utility functions for accounts app.
"""

def parse_user_agent(ua_string):
    """
    Lightweight, pure-Python User-Agent parser to extract OS and Browser name.
    """
    if not ua_string or not isinstance(ua_string, str):
        return "Unknown Device"

    ua = ua_string.lower()

    # Browser detection (order matters for overlapping keywords)
    browser = None
    if 'edg/' in ua or 'edge/' in ua:
        browser = "Microsoft Edge"
    elif 'opera' in ua or 'opr/' in ua:
        browser = "Opera"
    elif 'chrome/' in ua and 'chromium' not in ua:
        browser = "Chrome"
    elif 'firefox/' in ua:
        browser = "Firefox"
    elif 'safari/' in ua and 'chrome' not in ua and 'chromium' not in ua:
        browser = "Safari"
    elif 'trident/' in ua or 'msie' in ua:
        browser = "Internet Explorer"

    # OS detection
    os_name = None
    if 'windows nt 10' in ua or 'windows nt 11' in ua or 'windows' in ua:
        os_name = "Windows"
    elif 'iphone' in ua or 'ipad' in ua or 'ipod' in ua:
        os_name = "iOS"
    elif 'mac os x' in ua or 'macintosh' in ua:
        os_name = "macOS"
    elif 'android' in ua:
        os_name = "Android"
    elif 'linux' in ua:
        os_name = "Linux"

    if browser and os_name:
        return f"{browser} on {os_name}"
    elif browser:
        return browser
    elif os_name:
        return os_name
    return "Web Session"
