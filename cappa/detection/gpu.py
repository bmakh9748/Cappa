"""Which device the neural stages run on.

detector.py and ocr.py both run ONNX sessions through rapidocr; this file
answers two different questions about that. available() is the INSTALL
probe: is the DirectML build of onnxruntime present (any DX12 adapter, no
CUDA toolkit — the wheel requirements.txt names)? session_device() is the
TRUTH: where did a session actually land? They can disagree (the wheel
lists the provider even with no usable adapter) — see each function's
docstring. rapidocr handles the placement itself either way — nothing
here can crash the load path."""


def available():
    """True when the installed onnxruntime BUILD ships the DirectML
    provider. The wheel, not the adapter: a session can still land on CPU
    (see session_device) — never make a final tuning decision on this."""
    try:
        import onnxruntime
        return "DmlExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def session_device(module):
    """'gpu' or 'cpu': where `module`'s ONNX session ACTUALLY landed
    (rapidocr's det/rec modules each hold their engine at .session) — or
    None when rapidocr's internals have shifted and the truth can't be
    read. Callers must treat None as UNKNOWN (report it, tune
    conservatively), never substitute the install probe here: this is the
    one channel that exposes a session that silently fell back to CPU."""
    try:
        provider = module.session.session.get_providers()[0]
        return "gpu" if "Dml" in provider else "cpu"
    except Exception:
        return None
