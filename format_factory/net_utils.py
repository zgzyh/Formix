import os
import ssl
import urllib.request

try:
    import certifi  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    certifi = None


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUE_VALUES


def is_tls_related_error(exc: Exception | str) -> bool:
    text = str(exc or "").lower()
    return isinstance(exc, ssl.SSLError) or any(token in text for token in (
        "certificate verify failed",
        "unable to get local issuer certificate",
        "self-signed certificate",
        "self signed certificate",
        "hostname mismatch",
        "ssl:",
        "tlsv1",
        "wrong version number",
    ))


def _verified_contexts() -> list[ssl.SSLContext]:
    contexts: list[ssl.SSLContext] = []

    if certifi is not None:
        try:
            contexts.append(ssl.create_default_context(cafile=certifi.where()))
        except Exception:
            pass

    try:
        system_ctx = ssl.create_default_context()
        try:
            system_ctx.load_default_certs()
        except Exception:
            pass
        contexts.append(system_ctx)
    except Exception:
        pass

    return contexts or [ssl.create_default_context()]


def open_url(req, timeout: int, *, allow_insecure_retry: bool = False):
    contexts = list(_verified_contexts())
    if allow_insecure_retry or _env_truthy("FORMIX_ALLOW_INSECURE_TLS"):
        insecure = ssl.create_default_context()
        insecure.check_hostname = False
        insecure.verify_mode = ssl.CERT_NONE
        contexts.append(insecure)

    last_exc = None
    for idx, ctx in enumerate(contexts):
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except Exception as exc:
            last_exc = exc
            if idx + 1 < len(contexts) and is_tls_related_error(exc):
                continue
            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("network_open_failed")
