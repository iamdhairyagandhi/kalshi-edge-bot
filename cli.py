"""
CLI for kalshi-edge-bot.

Commands:
  health        Verify API connectivity and key
  scan          Run one scan-and-(paper)-execute pass
  paper-run     Continuous paper-trading loop
  status        Print current paper-portfolio state
  calibration   Print Brier score & reliability buckets
"""

from __future__ import annotations

import asyncio
import logging
import sys

import typer

from src.clients.kalshi import KalshiClient, KalshiAPIError
from src.clients.polymarket import PolymarketClient, PolymarketAPIError
from src.config import settings
from src.jobs.arb_runner import run_loop, run_once
from src.jobs.copy_runner import (
    build_default_executor,
    fetch_candidate_pool,
    fetch_market_snapshot,
    run_once as copy_run_once,
)
from src.paper.executor import PaperExecutor
from src.signals.smart_money import rank_wallets, select_cohort
from src.utils.calibration import CalibrationStore


app = typer.Typer(help="kalshi-edge-bot — deterministic, fee-aware Kalshi trading")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s :: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def health(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Check Kalshi API connectivity and credentials."""
    _setup_logging(verbose)
    async def _go():
        client = KalshiClient()
        try:
            if settings.kalshi_api_key:
                bal = await client.get_balance()
                typer.echo(f"✓ Authenticated. Balance: {bal}")
            else:
                # Hit a public endpoint
                resp = await client.get_markets(limit=1)
                n = len(resp.get("markets", []))
                typer.echo(f"✓ Public API reachable. Got {n} market(s).")
                typer.echo("⚠ No KALSHI_API_KEY set — auth not tested.")
        except KalshiAPIError as e:
            typer.echo(f"✗ {e}", err=True)
            raise typer.Exit(1)
        finally:
            await client.close()
    asyncio.run(_go())


@app.command()
def scan(
    max_markets: int = typer.Option(50, help="Max markets to scan in one pass"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one scan pass and (paper) execute any arb opportunities."""
    _setup_logging(verbose)
    async def _go():
        client = KalshiClient()
        executor = PaperExecutor(settings.db_path, settings.starting_bankroll)
        try:
            summary = await run_once(client, executor, max_markets)
            typer.echo(summary)
        finally:
            await client.close()
    asyncio.run(_go())


@app.command("paper-run")
def paper_run(
    iterations: int = typer.Option(0, help="Stop after N iterations (0 = forever)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Continuous paper-trading loop."""
    _setup_logging(verbose)
    asyncio.run(run_loop(stop_after_iterations=iterations or None))


@app.command()
def status() -> None:
    """Print current paper-portfolio state."""
    executor = PaperExecutor(settings.db_path, settings.starting_bankroll)
    p = executor.portfolio
    typer.echo(f"Bankroll:      ${p.bankroll:,.2f}")
    typer.echo(f"Cash:          ${p.cash:,.2f}")
    typer.echo(f"Realized P&L:  ${p.realized_pnl:,.2f}")
    typer.echo(f"Open positions: {len(p.positions)}")
    for key, pos in p.positions.items():
        typer.echo(f"  {key}  x{pos.contracts} @ ${pos.avg_price:.2f}  (cost ${pos.cost:.2f})")


@app.command()
def calibration(
    strategy: str = typer.Option("", help="Filter by strategy name (default: all)"),
) -> None:
    """Show Brier score and reliability diagram for predictions logged so far."""
    store = CalibrationStore()
    r = store.report(strategy or None)
    typer.echo(f"Strategy:   {r.strategy}")
    typer.echo(f"N resolved: {r.n_resolved}")
    typer.echo(f"Brier:      {r.brier_score:.4f}    (0.25 = random for 50/50; lower is better)")
    if r.buckets:
        typer.echo("Bucket   mean_pred  realized  n")
        for b in r.buckets:
            typer.echo(
                f"  [{b['lo']:.1f},{b['hi']:.1f})  {b['mean_pred']:.3f}      "
                f"{b['realized_rate']:.3f}     {int(b['n'])}"
            )


@app.command("polymarket-leaderboard")
def polymarket_leaderboard(
    top_n: int = typer.Option(20, help="How many cohort wallets to display"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pull the Polymarket leaderboard, re-rank under our criteria, and
    print the cohort that the consensus-copy strategy would use right now."""
    import time as _time
    _setup_logging(verbose)
    client = PolymarketClient()
    try:
        pool = fetch_candidate_pool(client, top_n=100)
        typer.echo(f"Candidate pool: {len(pool)} wallets")
        trades_by_wallet = {w: client.get_wallet_trades(w, limit=500) for w in pool}
        # We don't have a 'resolved markets' enumerator yet, so use the
        # union of condition_ids the candidates have closed positions in.
        # In v1 we approximate: assume any condition_id that appears with
        # both BUY and SELL by the same wallet is "resolved-ish".
        resolved: set = set()
        for trs in trades_by_wallet.values():
            buys = {t.condition_id for t in trs if t.side == "BUY"}
            sells = {t.condition_id for t in trs if t.side == "SELL"}
            resolved |= (buys & sells)
        ranked = rank_wallets(trades_by_wallet, resolved, now_unix=int(_time.time()))
        cohort = select_cohort(ranked, top_n=top_n)
        typer.echo(f"Cohort ({len(cohort)} eligible):")
        typer.echo(f"  {'wallet':<44} {'score':>10} {'realized':>12} {'trades':>7} {'resolved':>9}")
        for s in cohort:
            typer.echo(
                f"  {s.wallet:<44} {s.score:>10.3f} {s.realized_pnl_usd:>12.2f} "
                f"{s.n_trades:>7d} {s.n_resolved:>9d}"
            )
        if not cohort:
            typer.echo("(No wallets met eligibility thresholds.)")
    except PolymarketAPIError as e:
        typer.echo(f"✗ {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()


@app.command("polymarket-scan")
def polymarket_scan(
    candidate_limit: int = typer.Option(100, help="Leaderboard pull size"),
    top_n: int = typer.Option(0, help="Cohort size (0 = use POLYMARKET_TOP_N)"),
    consensus_k: int = typer.Option(0, help="K-of-N agreement (0 = use POLYMARKET_CONSENSUS_K)"),
    notional_usd: float = typer.Option(50.0, help="Notional per paper fill"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one Polymarket consensus-copy scan + (paper) execute pass."""
    import time as _time
    _setup_logging(verbose)
    client = PolymarketClient()
    executor = build_default_executor()
    try:
        pool = fetch_candidate_pool(client, top_n=candidate_limit)
        # Resolved-set approximation (same as leaderboard command above).
        trades_by_wallet = {w: client.get_wallet_trades(w, limit=500) for w in pool}
        resolved: set = set()
        for trs in trades_by_wallet.values():
            buys = {t.condition_id for t in trs if t.side == "BUY"}
            sells = {t.condition_id for t in trs if t.side == "SELL"}
            resolved |= (buys & sells)
        # Markets the cohort actually traded recently — these are the only
        # ones we could meaningfully copy into anyway.
        recent_condition_ids = {
            t.condition_id
            for trs in trades_by_wallet.values()
            for t in trs
            if t.timestamp_unix >= int(_time.time()) - 24 * 3600
        }
        markets = fetch_market_snapshot(client, list(recent_condition_ids))
        summary = copy_run_once(
            client=client, executor=executor, db_path=settings.db_path,
            candidate_wallets=pool,
            resolved_condition_ids=list(resolved),
            markets=markets,
            now_unix=int(_time.time()),
            top_n=top_n or settings.polymarket_top_n,
            consensus_k=consensus_k or settings.polymarket_consensus_k,
            notional_per_signal_usd=notional_usd,
        )
        typer.echo(str(summary))
    except PolymarketAPIError as e:
        typer.echo(f"✗ {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
