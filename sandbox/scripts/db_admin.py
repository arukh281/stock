"""
Interactive Supabase paper-ledger admin (positions, pending, journal, runs, cash).
Invoked by database.sh at repo root.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable

ALGO_IDS = ("44ma", "44ma_stacked_2ma", "financially_free", "kali")

TABLES_BY_ALGO = (
    "positions",
    "pending_orders",
    "journal",
    "closed_trades",
    "equity_snapshots",
    "run_logs",
)


def _client():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def _confirm(prompt: str) -> bool:
    ans = input(f"{prompt} [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def _pick_algos() -> list[str]:
    print("\nSelect algo:")
    print("  1) 44ma (full ladder)")
    print("  2) 44ma_stacked_2ma")
    print("  3) financially_free")
    print("  4) kali")
    print("  5) All four")
    choice = input("Choice [1-5]: ").strip()
    if choice == "5":
        return list(ALGO_IDS)
    idx = int(choice) - 1
    if idx < 0 or idx >= len(ALGO_IDS):
        print("Invalid choice.")
        return []
    return [ALGO_IDS[idx]]


def _pick_components() -> dict[str, bool]:
    labels = [
        ("positions", "Open positions"),
        ("pending_orders", "Pending / open orders"),
        ("journal", "Activity log (journal)"),
        ("closed_trades", "Closed trades history"),
        ("equity_snapshots", "Equity snapshots"),
        ("run_logs", "Analyze run history"),
        ("cash", "Renew cash & equity to starting capital"),
    ]
    print("\nToggle components (y/n); default = yes for all:")
    out: dict[str, bool] = {}
    for key, label in labels:
        default = "y"
        ans = input(f"  {label}? [{default}/n]: ").strip().lower()
        out[key] = ans != "n"
    return out


def _get_meta(sb, algo_id: str) -> dict:
    r = sb.table("portfolio_meta").select("*").eq("algo_id", algo_id).single().execute()
    return r.data or {}


def _count(sb, table: str, algo_id: str) -> int:
    r = sb.table(table).select("id", count="exact").eq("algo_id", algo_id).execute()
    return int(r.count or 0)


def show_status(sb) -> None:
    algos = _pick_algos()
    if not algos:
        return
    for algo_id in algos:
        meta = _get_meta(sb, algo_id)
        print(f"\n── {algo_id} ──")
        print(
            f"  Cash ₹{meta.get('cash', 0):,.2f}  |  "
            f"Equity ₹{meta.get('equity', 0):,.2f}  |  "
            f"Starting ₹{meta.get('starting_capital', 0):,.2f}"
        )
        for table in TABLES_BY_ALGO:
            print(f"  {table}: {_count(sb, table, algo_id)}")
        pos = (
            sb.table("positions")
            .select("symbol,qty,entry_px")
            .eq("algo_id", algo_id)
            .execute()
        ).data or []
        if pos:
            print("  Holdings:")
            for p in pos:
                print(
                    f"    {p['symbol']}  qty={p['qty']}  entry={p['entry_px']}"
                )
        pend = (
            sb.table("pending_orders")
            .select("symbol,status,trigger_px")
            .eq("algo_id", algo_id)
            .eq("status", "open")
            .execute()
        ).data or []
        if pend:
            print("  Open pending:")
            for p in pend:
                print(f"    {p['symbol']}  trigger={p.get('trigger_px')}")


def _delete_table(sb, table: str, algo_id: str) -> int:
    before = _count(sb, table, algo_id)
    if before == 0:
        return 0
    sb.table(table).delete().eq("algo_id", algo_id).execute()
    return before


def _renew_cash(sb, algo_id: str, cash: float | None = None, *, clear_paper_start: bool = False) -> None:
    meta = _get_meta(sb, algo_id)
    starting = float(meta.get("starting_capital", 15000))
    amount = float(cash if cash is not None else starting)
    patch: dict = {"cash": amount, "equity": amount}
    if clear_paper_start:
        cfg = meta.get("config") or {}
        if isinstance(cfg, str):
            import json

            cfg = json.loads(cfg)
        cfg = dict(cfg)
        cfg.pop("paper_start_date", None)
        patch["config"] = cfg
    sb.table("portfolio_meta").update(patch).eq("algo_id", algo_id).execute()
    print(f"  {algo_id}: cash & equity → ₹{amount:,.2f}")


def _journal(sb, algo_id: str, symbol: str | None, kind: str, message: str) -> None:
    sb.table("journal").insert(
        {
            "algo_id": algo_id,
            "symbol": symbol,
            "kind": kind,
            "message": message,
        }
    ).execute()


def apply_reset(sb, algo_ids: Iterable[str], components: dict[str, bool]) -> None:
    for algo_id in algo_ids:
        print(f"\nResetting {algo_id}...")
        for table in TABLES_BY_ALGO:
            if components.get(table, False):
                n = _delete_table(sb, table, algo_id)
                print(f"  deleted {n} from {table}")
        if components.get("cash", False):
            _renew_cash(sb, algo_id, clear_paper_start=components.get("equity_snapshots", False))
            _journal(
                sb,
                algo_id,
                None,
                "admin",
                "Cash & equity renewed to starting capital (database.sh)",
            )


def full_reset(sb) -> None:
    algos = _pick_algos()
    if not algos:
        return
    print("\nFull reset clears: positions, pending, journal, trades, snapshots, runs.")
    print("Cash & equity return to each algo's starting_capital.")
    if not _confirm("Proceed?"):
        print("Cancelled.")
        return
    components = {t: True for t in TABLES_BY_ALGO}
    components["cash"] = True
    apply_reset(sb, algos, components)
    print("\nDone.")


def custom_reset(sb) -> None:
    algos = _pick_algos()
    if not algos:
        return
    components = _pick_components()
    if not any(components.values()):
        print("Nothing selected.")
        return
    print("\nConfirm: type y and press Enter (blank = cancel).")
    if not _confirm("Proceed with custom reset?"):
        print("Cancelled — nothing was changed.")
        return
    apply_reset(sb, algos, components)
    print("\nDone.")


def renew_cash_only(sb) -> None:
    algos = _pick_algos()
    if not algos:
        return
    custom = input("Custom cash amount (blank = starting_capital): ").strip()
    amount = float(custom) if custom else None
    print("\nConfirm: type y and press Enter (blank = cancel).")
    if not _confirm("Update cash & equity only (keep positions/history)?"):
        print("Cancelled — nothing was changed.")
        return
    for algo_id in algos:
        _renew_cash(sb, algo_id, amount)
    print("\nDone.")


def close_position(sb) -> None:
    algos = _pick_algos()
    if len(algos) != 1:
        print("Pick exactly one algo.")
        return
    algo_id = algos[0]
    symbol = input("Symbol (e.g. BSE.NS): ").strip().upper()
    if not symbol:
        return
    r = (
        sb.table("positions")
        .select("*")
        .eq("algo_id", algo_id)
        .eq("symbol", symbol)
        .execute()
    )
    if not r.data:
        print(f"No position for {symbol}.")
        return
    row = r.data[0]
    qty, entry = float(row["qty"]), float(row["entry_px"])
    release = qty * entry * 1.001
    meta = _get_meta(sb, algo_id)
    new_cash = float(meta.get("cash", 0)) + release
    if not _confirm(f"Close {symbol} @ cost ~₹{release:,.2f} → cash ₹{new_cash:,.2f}?"):
        print("Cancelled.")
        return
    sb.table("positions").delete().eq("algo_id", algo_id).eq("symbol", symbol).execute()
    sb.table("portfolio_meta").update(
        {"cash": new_cash, "equity": new_cash}
    ).eq("algo_id", algo_id).execute()
    _journal(sb, algo_id, symbol, "admin", f"Position closed manually ({symbol})")
    print("Done.")


def cancel_pending(sb) -> None:
    algos = _pick_algos()
    if not algos:
        return
    symbol = input("Symbol (blank = all open pending for selected algo(s)): ").strip().upper()
    if not _confirm("Cancel pending order(s)?"):
        print("Cancelled.")
        return
    for algo_id in algos:
        q = (
            sb.table("pending_orders")
            .select("id,symbol")
            .eq("algo_id", algo_id)
            .eq("status", "open")
        )
        if symbol:
            q = q.eq("symbol", symbol)
        rows = q.execute().data or []
        for row in rows:
            sb.table("pending_orders").update({"status": "cancelled"}).eq(
                "id", row["id"]
            ).execute()
            _journal(
                sb,
                algo_id,
                row["symbol"],
                "cancel",
                f"Pending cancelled manually ({row['symbol']})",
            )
            print(f"  {algo_id}: cancelled {row['symbol']}")
    print("Done.")


def set_starting_capital(sb) -> None:
    algos = _pick_algos()
    if not algos:
        return
    val = input("New starting_capital (INR): ").strip()
    if not val:
        return
    amount = float(val)
    renew = _confirm("Also set cash & equity to this amount now?")
    for algo_id in algos:
        upd = {"starting_capital": amount}
        if renew:
            upd["cash"] = amount
            upd["equity"] = amount
        sb.table("portfolio_meta").update(upd).eq("algo_id", algo_id).execute()
        print(f"  {algo_id}: starting_capital → ₹{amount:,.2f}")
    print("Done.")


def main_menu() -> None:
    sb = _client()
    while True:
        print("\n════════════════════════════════════════")
        print("  Paper Trading — Database Admin")
        print("════════════════════════════════════════")
        print("  1) Portfolio snapshot")
        print("  2) Full reset (ledger + cash to starting)")
        print("  3) Custom reset (pick tables + cash)")
        print("  4) Close one position (return cash at cost)")
        print("  5) Cancel open pending order(s)")
        print("  6) Renew cash & equity only")
        print("  7) Set starting capital")
        print("  0) Quit")
        choice = input("\nChoice [0-7]: ").strip()
        if choice == "0":
            print("Bye.")
            break
        if choice == "1":
            show_status(sb)
        elif choice == "2":
            full_reset(sb)
        elif choice == "3":
            custom_reset(sb)
        elif choice == "4":
            close_position(sb)
        elif choice == "5":
            cancel_pending(sb)
        elif choice == "6":
            renew_cash_only(sb)
        elif choice == "7":
            set_starting_capital(sb)
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main_menu()
