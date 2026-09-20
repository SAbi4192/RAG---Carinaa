"""
The offline guarantee.

THE CLAIM
---------
Spec section 15: "Offline mode must never silently switch to an online provider."

WHY A TEST AND NOT A PROMISE
----------------------------
"Never goes online" is an absolute claim, and absolute claims decay. Somebody adds
a retry, or a helper that happens to import an HTTP client, and the guarantee is
quietly gone - with no visible symptom, because the answer still arrives. The only
thing that keeps an absolute claim true is a test that fails the moment it stops
being true.

FOUR INDEPENDENT PROOFS
-----------------------
1. Structural (AST): the offline method contains no reference to any online
   provider. It is not that we remember not to call them - there is nothing there
   to call.
2. Static (imports): the local provider module imports no HTTP client at all.
3. Runtime (sockets): offline generation runs while every socket operation raises.
4. Runtime (tripwire): the online providers are replaced with objects that fail the
   test if they are ever touched.

Proofs 1 and 2 are about the code we ship. Proofs 3 and 4 are about the code that
actually runs. A refactor that defeats one of them will usually be caught by
another.
"""

from __future__ import annotations

import ast
import asyncio
import socket
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.core.errors import LocalModelUnavailable
from app.llm.base import LLMResponse, ProviderStatus
from app.llm.local import get_local_provider

# Modules that would indicate a network dependency if the local provider imported
# them. `socket` is included deliberately: even opening a socket is a network
# dependency, and there is no reason for a llama.cpp wrapper to need one.
_FORBIDDEN_IMPORTS = {
    "requests",
    "httpx",
    "urllib",
    "urllib3",
    "aiohttp",
    "socket",
    "http",
    "ftplib",
    "smtplib",
    "telnetlib",
    "websockets",
    "grpc",
}


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class _StubLocal:
    """A local provider that answers instantly: no model, no disk, no network."""

    name = "local"
    label = "Local model (stub)"
    role = "offline"
    model_present = True
    _load_error = None

    def load(self) -> bool:
        return True

    async def generate(self, messages, *, temperature=None, max_tokens=None) -> LLMResponse:
        return LLMResponse(
            text="An answer produced entirely on this machine.",
            provider="local",
            model="stub",
            latency_ms=1,
        )

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            label=self.label,
            role=self.role,
            configured=True,
            available=True,
            model="stub",
            modes=("offline",),
        )

    def info(self) -> dict:
        return {"offline": True, "makes_network_calls": False}


class _MissingLocal:
    """A local provider with no model file on disk."""

    name = "local"
    label = "Local model (missing)"
    role = "offline"
    model_present = False
    _load_error = None

    def load(self) -> bool:
        return False

    async def generate(self, messages, **kwargs):  # pragma: no cover - never reached
        raise AssertionError("generate() must not be called when no model is present.")

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            label=self.label,
            role=self.role,
            configured=False,
            available=False,
            model="",
            reason="no model file",
            modes=("offline",),
        )


class _ExplodingProvider:
    """Tripwire. If the offline path reaches this, the test fails loudly."""

    name = "tripwire"
    label = "Tripwire"
    role = "online"
    configured = True
    model = "tripwire"

    async def generate(self, *args, **kwargs):
        raise AssertionError(
            "The offline path called an ONLINE provider. This violates the offline "
            "guarantee (spec section 15)."
        )

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            label=self.label,
            role=self.role,
            configured=True,
            available=True,
            model=self.model,
        )


@contextmanager
def _network_disabled():
    """Make every outbound network operation raise, recording each attempt.

    Patches DNS resolution as well as connection, because a DNS lookup is itself a
    network call and would prove the code tried to reach a host.
    """
    attempts: list[str] = []
    originals = {
        "connect": socket.socket.connect,
        "create_connection": socket.create_connection,
        "getaddrinfo": socket.getaddrinfo,
    }

    def _blocked(label: str):
        def _raise(*_args, **_kwargs):
            attempts.append(label)
            raise OSError("Network access is disabled for this test.")

        return _raise

    socket.socket.connect = _blocked("socket.connect")  # type: ignore[assignment]
    socket.create_connection = _blocked("socket.create_connection")  # type: ignore[assignment]
    socket.getaddrinfo = _blocked("socket.getaddrinfo")  # type: ignore[assignment]

    try:
        yield attempts
    finally:
        socket.socket.connect = originals["connect"]  # type: ignore[assignment]
        socket.create_connection = originals["create_connection"]  # type: ignore[assignment]
        socket.getaddrinfo = originals["getaddrinfo"]  # type: ignore[assignment]


@contextmanager
def _offline_loop():
    """A fresh event loop with networking disabled, plus the attempt log.

    ORDER MATTERS. The loop is created BEFORE the sockets are patched, because on
    Windows creating a proactor event loop opens a loopback socketpair internally.
    Patching first breaks asyncio's own bootstrap, and the test then fails for a
    reason that has nothing to do with the code under test.

    Yields ``(loop, attempts)``. The loop is closed after the patch is lifted.
    """
    loop = asyncio.new_event_loop()
    try:
        with _network_disabled() as attempts:
            yield loop, attempts
    finally:
        # `asyncio.run` would do this for us; a hand-built loop has to do it
        # itself, otherwise the default executor's threads outlive the test.
        try:
            loop.run_until_complete(loop.shutdown_default_executor())
        except Exception:  # pragma: no cover
            pass
        loop.close()


def _build_adapter():
    """An adapter whose online providers are tripwires and whose local is a stub."""
    from app.llm.adapter import LLMAdapter

    adapter = LLMAdapter()
    adapter.local = _StubLocal()
    adapter.gemini = _ExplodingProvider()
    adapter.groq = _ExplodingProvider()
    return adapter


# ---------------------------------------------------------------------------
# 1. Structural proof
# ---------------------------------------------------------------------------
def test_offline_method_contains_no_reference_to_online_providers():
    """The offline code path must have nothing online to call."""
    from app.llm import adapter as adapter_module

    tree = ast.parse(Path(adapter_module.__file__).read_text(encoding="utf-8"))

    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_generate_offline"
    )

    referenced: set[str] = set()
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)

    for forbidden in ("gemini", "groq", "_generate_online", "AllProvidersFailed"):
        assert forbidden not in referenced, (
            f"`_generate_offline` references '{forbidden}'. The offline path must have "
            f"no route to an online provider."
        )


# ---------------------------------------------------------------------------
# 2. Static proof
# ---------------------------------------------------------------------------
def test_local_provider_imports_no_http_client():
    """A llama.cpp wrapper has no business importing anything network-capable."""
    from app.llm import local as local_module

    tree = ast.parse(Path(local_module.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    offending = imported & _FORBIDDEN_IMPORTS
    assert not offending, f"The local provider imports network-capable modules: {offending}"


def test_local_provider_declares_that_it_makes_no_network_calls():
    info = get_local_provider().info()
    assert info["offline"] is True
    assert info["makes_network_calls"] is False


def test_adapter_documents_the_offline_guarantee():
    from app.llm.adapter import LLMAdapter

    mode_status = LLMAdapter().mode_status()
    assert mode_status["offline"]["available"] in (True, False)  # a real answer, either way
    assert "never contacts an online provider" in mode_status["offline"]["guarantee"]


# ---------------------------------------------------------------------------
# 3 & 4. Runtime proof
# ---------------------------------------------------------------------------
def test_offline_generation_touches_no_network_and_no_online_provider():
    """The whole guarantee in one test: sockets blocked, providers tripwired."""
    adapter = _build_adapter()

    with _offline_loop() as (loop, attempts):
        response = loop.run_until_complete(
            adapter.generate(
                [{"role": "user", "content": "Does this reach the internet?"}],
                mode="offline",
            )
        )

    assert response.provider == "local"
    assert response.used_fallback is False
    assert attempts == [], f"The offline path attempted network access: {attempts}"


def test_offline_without_a_model_refuses_rather_than_going_online():
    """The failure mode is a clear error, never a silent switch to the internet."""
    from app.llm.adapter import LLMAdapter

    adapter = LLMAdapter()
    adapter.local = _MissingLocal()
    adapter.gemini = _ExplodingProvider()
    adapter.groq = _ExplodingProvider()

    with _offline_loop() as (loop, attempts):
        with pytest.raises(LocalModelUnavailable) as caught:
            loop.run_until_complete(
                adapter.generate([{"role": "user", "content": "hello"}], mode="offline")
            )

    assert attempts == [], f"The offline path attempted network access: {attempts}"
    # The message must tell the user what to do, not just fail.
    assert "will not use an online provider" in str(caught.value)


def test_offline_never_falls_back_even_when_online_providers_are_configured():
    """Configured online providers must not tempt the offline path."""
    adapter = _build_adapter()
    assert adapter.gemini.configured is True
    assert adapter.groq.configured is True

    with _offline_loop() as (loop, attempts):
        response = loop.run_until_complete(
            adapter.generate([{"role": "user", "content": "answer me"}], mode="offline")
        )

    assert response.provider == "local"
    assert attempts == []


def test_unknown_mode_is_rejected_rather_than_defaulting_to_online():
    """A typo must not silently become 'online'."""
    adapter = _build_adapter()

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(ValueError):
            loop.run_until_complete(
                adapter.generate([{"role": "user", "content": "hi"}], mode="off-line")
            )
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# The real model, if it is on disk
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_real_local_model_generates_with_networking_disabled():
    """End-to-end: the actual GGUF produces text while every socket raises."""
    from app.llm.adapter import LLMAdapter

    adapter = LLMAdapter()
    if not adapter.local.model_present:
        pytest.skip("No local GGUF model on disk (models/llm-model.gguf).")

    with _offline_loop() as (loop, attempts):
        response = loop.run_until_complete(
            adapter.generate(
                [{"role": "user", "content": "Reply with the single word: ready"}],
                mode="offline",
                max_tokens=16,
            )
        )

    assert response.text.strip(), "The local model returned nothing."
    assert response.provider == "local"
    assert attempts == [], f"The local model attempted network access: {attempts}"
