_MSG = "This PDF is encrypted with AES, which the browser version of docparse cannot decrypt."


class _Unsupported:
    def __init__(self, *a, **k):
        raise NotImplementedError(_MSG)

    def __getattr__(self, name):
        raise NotImplementedError(_MSG)


Cipher = _Unsupported


class algorithms:  # noqa: N801 - mirrors the real module's API
    AES = _Unsupported


class modes:  # noqa: N801
    CBC = _Unsupported
    ECB = _Unsupported
