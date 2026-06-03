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
from src.config import settings
from src.jobs.arb_runner import run_loop, run_once
from src.paper.executor import PaperExecutor
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


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
