from app.config import settings
from app.core import tracing


class FakeObs:
    def __init__(self, kind, kw):
        self.kind, self.kw = kind, kw


class FakeTrace:
    def __init__(self, kw):
        self.kw, self.children = kw, []

    def span(self, **kw):
        o = FakeObs("span", kw)
        self.children.append(o)
        return o

    def generation(self, **kw):
        o = FakeObs("generation", kw)
        self.children.append(o)
        return o


class FakeLangfuse:
    def __init__(self):
        self.traces = []

    def trace(self, **kw):
        t = FakeTrace(kw)
        self.traces.append(t)
        return t


def _enable(monkeypatch, client, capture=True):
    monkeypatch.setattr(settings, "langfuse_enabled", True)
    monkeypatch.setattr(settings, "langfuse_capture_content", capture)
    monkeypatch.setattr(tracing, "_client", client)


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_enabled", False)
    monkeypatch.setattr(tracing, "_client", None)
    t = tracing.begin("s1", "hello")
    t.note(outcome="answered")
    t.finish("hi", "answered")  # must not raise or create a client
    assert tracing._client is None


def test_enabled_without_keys_stays_off(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_enabled", True)
    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    monkeypatch.setattr(tracing, "_client", None)
    assert tracing._get_client() is None


def test_full_trace_sent(monkeypatch):
    fake = FakeLangfuse()
    _enable(monkeypatch, fake)
    t = tracing.begin("sess-1", "What is the refund window?")
    t.note(standalone="What is the refund window?", category=None)
    t.mark("retrieval_start")
    t.mark("retrieval_end")
    t.retrieval([{"doc": "FAQ", "section": "Refunds", "score": 0.03, "preview": "30 days"}], 0.71)
    t.generation("openai/gpt-oss-20b", "fast", 900)
    t.mark("llm_start")
    t.mark("llm_end")
    t.finish("30 days.", "answered")

    assert len(fake.traces) == 1
    tr = fake.traces[0]
    assert tr.kw["session_id"] == "sess-1" and tr.kw["input"] == "What is the refund window?"
    assert tr.kw["output"] == "30 days." and "answered" in tr.kw["tags"]
    assert tr.kw["metadata"]["confidence"] == 0.71
    kinds = [c.kind for c in tr.children]
    assert kinds == ["span", "generation"]
    gen = tr.children[1].kw
    assert gen["model"] == "openai/gpt-oss-20b" and gen["usage"]["input"] == 900


def test_capture_content_false_sends_no_text(monkeypatch):
    fake = FakeLangfuse()
    _enable(monkeypatch, fake, capture=False)
    t = tracing.begin("s", "my secret question")
    t.note(standalone="my secret question")
    t.retrieval([], 0.5)
    t.generation("m", "fast", 10)
    t.finish("secret answer", "answered")
    tr = fake.traces[0]
    assert tr.kw["input"] is None and tr.kw["output"] is None
    assert all(c.kw.get("output") in (None, []) and c.kw.get("input") is None for c in tr.children)


def test_client_failure_never_raises(monkeypatch):
    class Boom:
        def trace(self, **kw):
            raise RuntimeError("langfuse down")

    _enable(monkeypatch, Boom())
    tracing.begin("s", "q").finish("a", "answered")  # no exception


def test_long_text_is_clipped(monkeypatch):
    fake = FakeLangfuse()
    _enable(monkeypatch, fake)
    tracing.begin("s", "x" * 9000).finish("y" * 9000, "answered")
    assert len(fake.traces[0].kw["input"]) < 2100
