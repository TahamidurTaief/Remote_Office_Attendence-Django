import threading


_state = threading.local()


def set_current_request(request):
    _state.request = request


def get_current_request():
    return getattr(_state, "request", None)


def clear_current_request():
    if hasattr(_state, "request"):
        delattr(_state, "request")

