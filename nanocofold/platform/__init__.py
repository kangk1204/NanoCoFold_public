"""Personal browser platform; model inference remains in the existing CLI."""

def create_app(*args, **kwargs):
    from .app import create_app as factory
    return factory(*args, **kwargs)


def serve(*args, **kwargs):
    from .app import serve as launch
    return launch(*args, **kwargs)
