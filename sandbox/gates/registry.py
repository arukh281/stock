from __future__ import annotations

from typing import Any, Callable

from sandbox.gates import ff_gates, kali_gates, ma44_gates


def _ma44_compare(algo_id: str) -> Callable[..., dict[str, Any]]:
    def fn(*, start: str = "2018-01-01", **_kwargs: Any) -> dict[str, Any]:
        return ma44_gates.run_compare(start=start, algo_id=algo_id)

    return fn


_ALGO_GATE: dict[str, Callable[[str], dict[str, Any]]] = {
    "44ma": lambda s: ma44_gates.gate_breakdown(s, algo_id="44ma"),
    "44ma_stacked_2ma": lambda s: ma44_gates.gate_breakdown(s, algo_id="44ma_stacked_2ma"),
    "financially_free": ff_gates.gate_breakdown,
    "kali": kali_gates.gate_breakdown,
}

_ALGO_COMPARE: dict[str, Callable[..., dict[str, Any]]] = {
    "44ma": _ma44_compare("44ma"),
    "44ma_stacked_2ma": _ma44_compare("44ma_stacked_2ma"),
    "financially_free": ff_gates.run_compare,
    "kali": kali_gates.run_compare,
}


def list_algos() -> list[str]:
    return sorted(_ALGO_GATE.keys())


def gate_breakdown(algo_id: str, symbol: str) -> dict[str, Any]:
    fn = _ALGO_GATE.get(algo_id)
    if fn is None:
        return {"error": f"Unknown algo_id: {algo_id}", "algos": list_algos()}
    return fn(symbol)


def run_compare(algo_id: str, **kwargs: Any) -> dict[str, Any]:
    fn = _ALGO_COMPARE.get(algo_id)
    if fn is None:
        return {"error": f"Unknown algo_id: {algo_id}", "algos": list_algos()}
    import inspect

    sig = inspect.signature(fn)
    filtered = {k: v for k, v in kwargs.items() if k in sig.parameters and v is not None}
    return fn(**filtered)
