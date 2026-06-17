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
from src.clients.nws import NWSClient, NWSAPIError
from src.clients.open_meteo import OpenMeteoClient, OpenMeteoAPIError
from src.clients.polymarket import PolymarketClient, PolymarketAPIError
from src.config import settings
from src.jobs.arb_runner import run_loop, run_once
from src.jobs.copy_runner import (
    build_default_executor,
    fetch_candidate_pool,
    fetch_candidate_pool_with_pnl,
    run_once as copy_run_once,
)
from src.jobs.cross_venue_runner import run_once as cross_venue_run_once
from src.jobs.exit_runner import (
    build_default_executor as build_exit_executor,
    run_once as exit_run_once,
)
from src.jobs.momentum_runner import (
    build_default_executor as build_momentum_executor,
    run_once as momentum_run_once,
)
from src.jobs.weather_runner import build_weather_ai_client, run_once as weather_run_once
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
    logging.getLogger("httpx").setLevel(logging.INFO if verbose else logging.WARNING)


@app.command()
def health(
    venue: str = typer.Option(
        "all",
        "--venue",
        "-V",
        help="Venue to check: all, kalshi, or polymarket",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Check venue API connectivity and credentials."""
    _setup_logging(verbose)
    selected = venue.lower()
    if selected not in {"all", "kalshi", "polymarket"}:
        typer.echo("venue must be one of: all, kalshi, polymarket", err=True)
        raise typer.Exit(2)

    async def _go():
        failures = 0
        if selected in {"all", "kalshi"}:
            client = KalshiClient()
            try:
                if settings.kalshi_api_key:
                    bal = await client.get_balance()
                    typer.echo(f"✓ Kalshi authenticated. Balance: {bal}")
                else:
                    resp = await client.get_markets(limit=1)
                    n = len(resp.get("markets", []))
                    typer.echo(f"✓ Kalshi public API reachable. Got {n} market(s).")
                    typer.echo("⚠ No KALSHI_API_KEY set — Kalshi auth not tested.")
            except KalshiAPIError as e:
                failures += 1
                typer.echo(f"✗ Kalshi: {e}", err=True)
            finally:
                await client.close()

        if selected in {"all", "polymarket"}:
            client = PolymarketClient()
            try:
                markets = client.get_markets(limit=1)
                leaderboard = client.get_leaderboard(limit=1)
                typer.echo(
                    "✓ Polymarket public APIs reachable. "
                    f"Got {len(markets)} market(s), {len(leaderboard)} leaderboard row(s)."
                )
            except PolymarketAPIError as e:
                failures += 1
                typer.echo(f"✗ Polymarket: {e}", err=True)
            finally:
                client.close()

        if failures:
            raise typer.Exit(1)

    asyncio.run(_go())


@app.command("kalshi-health")
def kalshi_health(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Compatibility alias for checking Kalshi connectivity."""
    health(venue="kalshi", verbose=verbose)


@app.command("polymarket-health")
def polymarket_health(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Check Polymarket public API connectivity."""
    _setup_logging(verbose)
    client = PolymarketClient()
    try:
        markets = client.get_markets(limit=1)
        leaderboard = client.get_leaderboard(limit=1)
        typer.echo(
            "✓ Polymarket public APIs reachable. "
            f"Got {len(markets)} market(s), {len(leaderboard)} leaderboard row(s)."
        )
    except PolymarketAPIError as e:
        typer.echo(f"✗ Polymarket: {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()


@app.command()
def scan(
    max_markets: int = typer.Option(50, help="Max markets to scan in one pass"),
    min_net_edge: float = typer.Option(settings.kalshi_arb_min_net_edge, help="Minimum net arb edge per contract"),
    safety_margin: float = typer.Option(settings.kalshi_arb_safety_margin, help="Arb safety margin per contract"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one scan pass and (paper) execute any arb opportunities."""
    _setup_logging(verbose)
    async def _go():
        client = KalshiClient()
        executor = PaperExecutor(settings.db_path, settings.starting_bankroll)
        try:
            summary = await run_once(
                client,
                executor,
                max_markets,
                min_net_edge_per_contract=min_net_edge,
                safety_margin=safety_margin,
            )
            typer.echo(summary)
        finally:
            await client.close()
    asyncio.run(_go())


@app.command("paper-run")
def paper_run(
    iterations: int = typer.Option(0, help="Stop after N iterations (0 = forever)"),
    max_markets: int = typer.Option(settings.max_markets_per_scan, help="Max markets per scan"),
    min_net_edge: float = typer.Option(settings.kalshi_arb_min_net_edge, help="Minimum net arb edge per contract"),
    safety_margin: float = typer.Option(settings.kalshi_arb_safety_margin, help="Arb safety margin per contract"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Continuous paper-trading loop."""
    _setup_logging(verbose)
    asyncio.run(
        run_loop(
            stop_after_iterations=iterations or None,
            max_markets=max_markets,
            min_net_edge_per_contract=min_net_edge,
            safety_margin=safety_margin,
        )
    )


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
    top_n: int = typer.Option(75, help="How many cohort wallets to display"),
    candidate_limit: int = typer.Option(250, help="How many unique leaderboard wallets to inspect"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pull the Polymarket leaderboard, re-rank under our criteria, and
    print the cohort that the consensus-copy strategy would use right now."""
    import time as _time
    _setup_logging(verbose)
    client = PolymarketClient()
    try:
        pool, leaderboard_pnl = fetch_candidate_pool_with_pnl(client, top_n=candidate_limit)
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
        if len(cohort) < top_n:
            from src.jobs.copy_runner import _fill_with_leaderboard_profitable_wallets
            cohort = _fill_with_leaderboard_profitable_wallets(
                cohort, ranked, leaderboard_pnl, top_n=top_n, now_unix=int(_time.time())
            )
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
    candidate_limit: int = typer.Option(250, help="Leaderboard candidate pool size"),
    top_n: int = typer.Option(0, help="Cohort size (0 = use POLYMARKET_TOP_N)"),
    probe_k: int = typer.Option(0, help="Probe agreement threshold (0 = use POLYMARKET_PROBE_K)"),
    consensus_k: int = typer.Option(0, help="K-of-N agreement (0 = use POLYMARKET_CONSENSUS_K)"),
    lookback_hours: int = typer.Option(0, help="Consensus lookback window (0 = use POLYMARKET_LOOKBACK_HOURS)"),
    fresh_minutes: int = typer.Option(0, help="Only act on signals with a cohort trade this many minutes old (0 = env)"),
    probe_slippage_cents: float = typer.Option(-1.0, help="Max probe slippage vs wallet avg (-1 = use env)"),
    max_slippage_cents: float = typer.Option(-1.0, help="Max live ask slippage vs wallet avg (-1 = use env)"),
    probe_notional_usd: float = typer.Option(-1.0, help="Notional for probe paper fill (-1 = use env)"),
    notional_usd: float = typer.Option(50.0, help="Notional per paper fill"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one Polymarket consensus-copy scan + (paper) execute pass."""
    import time as _time
    _setup_logging(verbose)
    client = PolymarketClient()
    executor = build_default_executor()
    try:
        pool, leaderboard_pnl = fetch_candidate_pool_with_pnl(client, top_n=candidate_limit)
        typer.echo(f"Candidate pool: {len(pool)} wallets")
        # Fetch each candidate once. The runner reuses this data for ranking
        # and signal detection instead of calling the trades endpoint again.
        trades_by_wallet = {w: client.get_wallet_trades(w, limit=500) for w in pool}
        typer.echo(
            "Fetched trades: "
            f"{sum(1 for trs in trades_by_wallet.values() if trs)}/{len(pool)} wallets"
        )
        # Resolved-set approximation (same as leaderboard command above).
        resolved: set = set()
        for trs in trades_by_wallet.values():
            buys = {t.condition_id for t in trs if t.side == "BUY"}
            sells = {t.condition_id for t in trs if t.side == "SELL"}
            resolved |= (buys & sells)
        summary = copy_run_once(
            client=client, executor=executor, db_path=settings.db_path,
            candidate_wallets=pool,
            resolved_condition_ids=list(resolved),
            markets=None,
            now_unix=int(_time.time()),
            candidate_trades=trades_by_wallet,
            leaderboard_pnl_by_wallet=leaderboard_pnl,
            top_n=top_n or settings.polymarket_top_n,
            probe_k=probe_k or settings.polymarket_probe_k,
            consensus_k=consensus_k or settings.polymarket_consensus_k,
            lookback_hours=lookback_hours or settings.polymarket_lookback_hours,
            fresh_signal_minutes=fresh_minutes or settings.polymarket_fresh_signal_minutes,
            probe_max_slippage_cents=(
                settings.polymarket_probe_max_slippage_cents
                if probe_slippage_cents < 0
                else probe_slippage_cents
            ),
            max_slippage_cents=(
                settings.polymarket_max_slippage_cents
                if max_slippage_cents < 0
                else max_slippage_cents
            ),
            probe_notional_usd=(
                settings.polymarket_probe_notional_usd
                if probe_notional_usd < 0
                else probe_notional_usd
            ),
            notional_per_signal_usd=notional_usd,
        )
        typer.echo(str(summary))
    except PolymarketAPIError as e:
        typer.echo(f"✗ {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()


@app.command("cross-venue-scan")
def cross_venue_scan(
    max_kalshi: int = typer.Option(200, help="Max open Kalshi markets to scan"),
    max_polymarket: int = typer.Option(200, help="Max active Polymarket markets to scan"),
    min_match: float = typer.Option(settings.cross_venue_min_match_score, help="Minimum title/question match score"),
    min_spread: float = typer.Option(settings.cross_venue_min_spread, help="Minimum executable spread in dollars"),
    max_matches: int = typer.Option(50, help="Max matched market pairs to price"),
    show: int = typer.Option(10, help="Rows to print"),
    include_mve: bool = typer.Option(False, help="Include noisy Kalshi MVE combo markets"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Compare likely-equivalent Kalshi and Polymarket binary markets.

    This records real scan diagnostics to the dashboard database. It does
    not execute trades.
    """
    _setup_logging(verbose)

    async def _go():
        kalshi = KalshiClient()
        poly = PolymarketClient()
        try:
            summary, spreads = await cross_venue_run_once(
                kalshi,
                poly,
                db_path=settings.db_path,
                max_kalshi_markets=max_kalshi,
                max_polymarket_markets=max_polymarket,
                min_match_score=min_match,
                min_spread=min_spread,
                max_matches=max_matches,
                include_mve=include_mve,
            )
            typer.echo(str(summary))
            if not spreads:
                if summary.kalshi_eligible_markets == 0 and summary.kalshi_excluded_mve:
                    typer.echo(
                        "No eligible Kalshi single-market contracts found. "
                        "The scanned Kalshi page is all MVE combo markets; use --include-mve only for diagnostics."
                    )
                else:
                    typer.echo("No matched books were priceable. Try lowering --min-match or increasing market limits.")
                return
            typer.echo(
                "  spread  exec   match  direction              Kalshi YES     Poly YES      market"
            )
            for s in spreads[:show]:
                m = s.match
                title = m.kalshi_title if len(m.kalshi_title) <= 46 else m.kalshi_title[:43] + "..."
                typer.echo(
                    f"  {s.valuation_spread:+.3f}  {s.best_executable_spread:+.3f}  "
                    f"{m.score:.2f}   {s.direction:<22} "
                    f"{s.kalshi_yes_bid:.2f}/{s.kalshi_yes_ask:.2f}  "
                    f"{s.polymarket_yes_bid:.2f}/{s.polymarket_yes_ask:.2f}  "
                    f"{m.kalshi_ticker} :: {title}"
                )
        except (KalshiAPIError, PolymarketAPIError) as e:
            typer.echo(f"✗ {e}", err=True)
            raise typer.Exit(1)
        finally:
            await kalshi.close()
            poly.close()

    asyncio.run(_go())


@app.command("polymarket-momentum-scan")
def polymarket_momentum_scan(
    candidate_limit: int = typer.Option(50, help="Leaderboard pull size"),
    lookback_minutes: int = typer.Option(0, help="Momentum lookback window (0 = env)"),
    min_buy_trades: int = typer.Option(0, help="Minimum recent BUY trades (0 = env)"),
    min_wallets: int = typer.Option(0, help="Minimum unique wallets buying (0 = env)"),
    min_notional_usd: float = typer.Option(-1.0, help="Minimum recent buy notional (-1 = env)"),
    min_price_move: float = typer.Option(-1.0, help="Minimum late-vs-early VWAP move (-1 = env)"),
    max_entry_premium: float = typer.Option(-1.0, help="Max live ask over recent VWAP (-1 = env)"),
    notional_usd: float = typer.Option(10.0, help="Notional per paper fill"),
    execute: bool = typer.Option(False, "--execute/--no-execute", help="Actually place paper fills; default is observe-only"),
    force_paper_execute: bool = typer.Option(False, "--force-paper-execute", help="Bypass momentum history gate for paper-only testing"),
    exclude_terms: str = typer.Option("", help="Comma-separated market terms to reject (empty = env default)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one Polymarket fresh momentum scan. Observe-only unless --execute is set."""
    import time as _time
    _setup_logging(verbose)
    client = PolymarketClient()
    executor = build_momentum_executor()
    try:
        pool = fetch_candidate_pool(client, top_n=candidate_limit)
        typer.echo(f"Candidate pool: {len(pool)} wallets")
        trades_by_wallet = {w: client.get_wallet_trades(w, limit=500) for w in pool}
        typer.echo(
            "Fetched trades: "
            f"{sum(1 for trs in trades_by_wallet.values() if trs)}/{len(pool)} wallets"
        )
        summary = momentum_run_once(
            client=client,
            executor=executor,
            db_path=settings.db_path,
            candidate_wallets=pool,
            candidate_trades=trades_by_wallet,
            now_unix=int(_time.time()),
            lookback_minutes=lookback_minutes or settings.polymarket_momentum_lookback_minutes,
            min_buy_trades=min_buy_trades or settings.polymarket_momentum_min_buy_trades,
            min_unique_wallets=min_wallets or settings.polymarket_momentum_min_wallets,
            min_notional_usd=(
                settings.polymarket_momentum_min_notional_usd
                if min_notional_usd < 0
                else min_notional_usd
            ),
            min_price_move=(
                settings.polymarket_momentum_min_price_move
                if min_price_move < 0
                else min_price_move
            ),
            max_entry_premium=(
                settings.polymarket_momentum_max_entry_premium
                if max_entry_premium < 0
                else max_entry_premium
            ),
            notional_per_signal_usd=notional_usd,
            execute=execute,
            exclude_terms=exclude_terms or None,
            require_positive_history=not force_paper_execute,
        )
        typer.echo(str(summary))
    except PolymarketAPIError as e:
        typer.echo(f"✗ {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()


@app.command("polymarket-exit-scan")
def polymarket_exit_scan(
    take_profit_cents: float = typer.Option(-1.0, help="Sell if bid-entry >= this amount (-1 = env)"),
    profit_capture: float = typer.Option(-1.0, help="Sell if unrealized captures this fraction of max profit (-1 = env)"),
    stop_loss_cents: float = typer.Option(-1.0, help="Sell if entry-bid >= this amount (-1 = env)"),
    stop_loss_fraction: float = typer.Option(-1.0, help="Sell if unrealized loss exceeds this cost fraction (-1 = env)"),
    min_bid_size: float = typer.Option(-1.0, help="Minimum bid size to sell into (-1 = env)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one paper exit-manager pass for open Polymarket positions."""
    _setup_logging(verbose)
    client = PolymarketClient()
    executor = build_exit_executor()
    try:
        summary = exit_run_once(
            client=client,
            executor=executor,
            db_path=settings.db_path,
            take_profit_cents=None if take_profit_cents < 0 else take_profit_cents,
            profit_capture=None if profit_capture < 0 else profit_capture,
            stop_loss_cents=None if stop_loss_cents < 0 else stop_loss_cents,
            stop_loss_fraction=None if stop_loss_fraction < 0 else stop_loss_fraction,
            min_bid_size=None if min_bid_size < 0 else min_bid_size,
        )
        typer.echo(str(summary))
    except PolymarketAPIError as e:
        typer.echo(f"✗ {e}", err=True)
        raise typer.Exit(1)
    finally:
        client.close()


@app.command("weather-scan")
def weather_scan(
    max_kalshi: int = typer.Option(500, help="Max open Kalshi markets to scan"),
    max_polymarket: int = typer.Option(500, help="Max active Polymarket markets to scan"),
    min_edge: float = typer.Option(-1.0, help="Minimum net edge after safety buffer (-1 = env)"),
    safety_buffer: float = typer.Option(-1.0, help="Probability buffer for forecast uncertainty/slippage (-1 = env)"),
    min_confidence: float = typer.Option(-1.0, help="Minimum confidence to recommend a trade (-1 = env)"),
    use_ai: bool = typer.Option(False, "--ai/--no-ai", help="Use optional OpenAI weather assessor if OPENAI_API_KEY is set"),
    ai_limit: int = typer.Option(0, help="Max weather markets to send to AI (0 = env)"),
    ensemble: bool = typer.Option(True, "--ensemble/--no-ensemble", help="Use Open-Meteo best-match + NOAA GFS/HRRR alongside NWS"),
    execute: bool = typer.Option(False, "--execute/--no-execute", help="Paper-fill qualifying Kalshi weather candidates"),
    notional_usd: float = typer.Option(5.0, help="Paper notional per weather trade"),
    persist: bool = typer.Option(True, "--persist/--no-persist", help="Save weather specialist estimates to the bot DB"),
    show: int = typer.Option(10, help="Rows to print"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Scan weather/climate markets against public NWS forecasts.

    This is signal-only: it does not execute trades.
    """
    _setup_logging(verbose)

    async def _go():
        kalshi = KalshiClient()
        poly = PolymarketClient()
        nws = NWSClient()
        open_meteo = OpenMeteoClient() if ensemble and settings.weather_open_meteo_enabled else None
        ai = build_weather_ai_client() if use_ai else None
        executor = PaperExecutor(settings.db_path, settings.starting_bankroll) if execute else None
        if use_ai and ai is None:
            typer.echo("AI requested, but WEATHER_AI_ENABLED/OPENAI_API_KEY is not configured; using NWS-only mode.")
        try:
            summary, estimates = await weather_run_once(
                kalshi=kalshi,
                polymarket=poly,
                nws=nws,
                open_meteo=open_meteo,
                max_kalshi_markets=max_kalshi,
                max_polymarket_markets=max_polymarket,
                min_edge=settings.weather_min_edge if min_edge < 0 else min_edge,
                safety_buffer=settings.weather_safety_buffer if safety_buffer < 0 else safety_buffer,
                min_confidence=settings.weather_min_confidence if min_confidence < 0 else min_confidence,
                ai_client=ai,
                ai_limit=settings.weather_ai_max_markets if ai_limit <= 0 else ai_limit,
                executor=executor,
                execute=execute,
                notional_per_trade_usd=notional_usd,
                db_path=settings.db_path if persist else None,
            )
            typer.echo(str(summary))
            if not estimates:
                typer.echo("No parseable weather markets found in this scan window.")
                return
            typer.echo("  rec       conf  edgeY  edgeN  p_yes  bid/ask  city          forecast  market")
            for e in estimates[:show]:
                edge_y = e.edge_to_buy_yes if e.edge_to_buy_yes is not None else float("nan")
                edge_n = e.edge_to_buy_no if e.edge_to_buy_no is not None else float("nan")
                bid = "--" if e.market_yes_bid is None else f"{e.market_yes_bid:.2f}"
                ask = "--" if e.market_yes_ask is None else f"{e.market_yes_ask:.2f}"
                title = e.contract.title if len(e.contract.title) <= 58 else e.contract.title[:55] + "..."
                typer.echo(
                    f"  {e.recommendation:<9} {e.confidence:.2f}  {edge_y:+.3f} {edge_n:+.3f} "
                    f"{e.p_yes:.2f}  {bid}/{ask}  {e.contract.city:<13} "
                    f"{e.forecast_value:>6.1f}  {e.contract.market_id} :: {title}"
                )
        except (KalshiAPIError, PolymarketAPIError, NWSAPIError, OpenMeteoAPIError) as e:
            typer.echo(f"✗ {e}", err=True)
            raise typer.Exit(1)
        finally:
            await kalshi.close()
            poly.close()
            nws.close()
            if open_meteo is not None:
                open_meteo.close()
            if ai is not None:
                ai.close()

    asyncio.run(_go())


# =====================================================================
# Soccer / FIFA WC bet builder
# =====================================================================

@app.command("soccer-fetch")
def soccer_fetch(
    competition: int = typer.Option(43, "--competition", "-c",
                                    help="StatsBomb competition_id (default 43 = FIFA WC)"),
    season: int = typer.Option(106, "--season", "-s",
                               help="StatsBomb season_id (default 106 = 2022)"),
    cache_dir: str = typer.Option(None, "--cache-dir",
                                   help="Override cache dir (defaults to settings.soccer_data_cache_dir)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Download StatsBomb open-data matches for a competition/season into the local cache."""
    _setup_logging(verbose)
    from src.sports.soccer.data.statsbomb import StatsBombOpenData
    from src.sports.soccer.data.store import SoccerStore

    cdir = cache_dir or settings.soccer_data_cache_dir
    typer.echo(f"caching to {cdir}")
    with StatsBombOpenData(cache_dir=cdir) as sb:
        comps = sb.competitions()
        match = next((c for c in comps if int(c["competition_id"]) == competition and int(c["season_id"]) == season), None)
        if match is None:
            typer.echo(f"competition_id={competition} season_id={season} not in StatsBomb open-data; "
                       "available competitions:", err=True)
            for c in comps[:10]:
                typer.echo(f"  {c['competition_id']}/{c['season_id']} {c['competition_name']} {c['season_name']}", err=True)
            raise typer.Exit(2)
        typer.echo(f"fetching {match['competition_name']} {match['season_name']}")
        rows = sb.matches_for_dixon_coles(competition, season, neutral_default=True)
        typer.echo(f"got {len(rows)} matches")
        # Persist to soccer DB as fixtures of competition='HISTORY' so the
        # dashboard's upcoming-fixtures filter ignores them, but the
        # Dixon-Coles fitter can still load them via store extension later.
        # For v1 we just print stats; fitting is in `soccer-fit`.
        store = SoccerStore(settings.soccer_db_path)
        # ensure DB is at least created
        _ = store
        if rows:
            sample = rows[0]
            typer.echo(f"sample row: {sample}")
        typer.echo("Use `cli.py soccer-fit` to fit Dixon-Coles on the cached matches.")


@app.command("soccer-fit")
def soccer_fit(
    competition: int = typer.Option(43, "--competition", "-c"),
    season: int = typer.Option(106, "--season", "-s"),
    cache_dir: str = typer.Option(None, "--cache-dir"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fit Dixon-Coles + Elo from cached StatsBomb data and dump diagnostics."""
    _setup_logging(verbose)
    from src.sports.soccer.data.statsbomb import StatsBombOpenData
    from src.sports.soccer.models.dixon_coles import DixonColesModel
    from src.sports.soccer.ratings.elo import EloTable

    cdir = cache_dir or settings.soccer_data_cache_dir
    with StatsBombOpenData(cache_dir=cdir) as sb:
        rows = sb.matches_for_dixon_coles(competition, season, neutral_default=True)
    if not rows:
        typer.echo("no matches available — run `soccer-fetch` first", err=True)
        raise typer.Exit(2)
    elo = EloTable()
    elo.fit(rows)
    typer.echo(f"Elo top 8: {sorted(elo.to_dict().items(), key=lambda kv: -kv[1])[:8]}")
    dc = DixonColesModel.fit(rows, decay_per_day=settings.soccer_decay_per_day)
    typer.echo(f"DC home_advantage={dc.params.home_advantage:.3f} rho={dc.params.rho:.3f} "
               f"loglik={dc.params.log_likelihood:.1f} n={dc.params.n_matches}")


@app.command("soccer-ingest")
def soccer_ingest(
    cache_dir: str = typer.Option(None, "--cache-dir"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pull StatsBomb open-data for WC 2018, WC 2022, Euro 2020, Euro 2024
    into the soccer DB, then refit Elo + Dixon-Coles. Replaces synthetic
    demo data with real match data."""
    _setup_logging(verbose)
    from dashboard_v2.api.routes.soccer import _DEFAULT_STATSBOMB_COMPS, SoccerEngine

    eng = SoccerEngine()
    typer.echo("ingesting StatsBomb open-data (WC18/WC22/Euro20/Euro24)...")
    info = eng.fit_from_statsbomb(
        _DEFAULT_STATSBOMB_COMPS,
        cache_dir=cache_dir,
    )
    typer.echo(f"matches: {info['matches_used']}")
    typer.echo(f"teams:   {info['teams']}")
    typer.echo("competitions:")
    for c in info["competitions"]:
        typer.echo(f"  {c}")
    top = sorted(eng.elo.to_dict().items(), key=lambda kv: -kv[1])[:8]
    typer.echo("Top 8 Elo:")
    for tid, r in top:
        typer.echo(f"  {tid:>5} {eng.team_names.get(tid, '?'):<22} {r:.0f}")


@app.command("soccer-simulate")
def soccer_simulate(
    home: str = typer.Argument(..., help="Home team id"),
    away: str = typer.Argument(..., help="Away team id"),
    competition: int = typer.Option(43, "--competition", "-c"),
    season: int = typer.Option(106, "--season", "-s"),
    n_sims: int = typer.Option(10000, "--n-sims", "-n"),
    neutral: bool = typer.Option(True, "--neutral/--home-game"),
    cache_dir: str = typer.Option(None, "--cache-dir"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run a Monte Carlo simulation of a fixture using cached StatsBomb data."""
    _setup_logging(verbose)
    from src.sports.soccer.data.statsbomb import StatsBombOpenData
    from src.sports.soccer.models.cards import CardsModel
    from src.sports.soccer.models.dixon_coles import DixonColesModel
    from src.sports.soccer.models.minutes import MinutesModel
    from src.sports.soccer.models.player_share import PlayerShareModel
    from src.sports.soccer.simulator.match_sim import MatchSimulator, SimulationConfig

    cdir = cache_dir or settings.soccer_data_cache_dir
    with StatsBombOpenData(cache_dir=cdir) as sb:
        rows = sb.matches_for_dixon_coles(competition, season, neutral_default=True)
    if not rows:
        typer.echo("no matches available — run `soccer-fetch` first", err=True)
        raise typer.Exit(2)
    dc = DixonColesModel.fit(rows, decay_per_day=settings.soccer_decay_per_day)
    sm_probs = dc.outcome_probs(home, away, neutral=neutral)
    typer.echo(f"DC outcome: {sm_probs}")

    # Rough player share: empty (no lineups loaded). Sim will still produce
    # goals/cards distributions; scorer attribution will fall through to the
    # unlisted bucket.
    share = PlayerShareModel()
    share.team_to_players[home] = []
    share.team_to_players[away] = []
    minutes = MinutesModel()
    cards = CardsModel()
    sim = MatchSimulator(score_model=dc, player_share=share, minutes=minutes, cards=cards,
                         squads={home: [], away: []})
    sims = sim.simulate(home, away, neutral_venue=neutral,
                         config=SimulationConfig(n_sims=n_sims, seed=0))
    avg_goals = sum(s.total_goals for s in sims) / len(sims)
    p_home = sum(1 for s in sims if s.result == "H") / len(sims)
    p_btts = sum(1 for s in sims if s.btts) / len(sims)
    typer.echo(f"sim: avg goals={avg_goals:.2f} P(home)={p_home:.3f} P(btts)={p_btts:.3f}")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
