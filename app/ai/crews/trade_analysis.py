from __future__ import annotations

import time

from crewai import Agent, Crew, Process, Task

from app.ai.shared import TradeAnalysisResult, has_openai_key, llm, no_key_trade_result, parse_trade_result, record_llm_metric


class TradeAnalysisCrew:
    def __init__(self, trade: dict, intraday_csv: str):
        self.trade = trade
        self.intraday_csv = intraday_csv

    def run(self) -> dict:
        if not has_openai_key():
            return no_key_trade_result()

        started_at = time.perf_counter()
        trade = self.trade
        model = llm()
        ohlcv_block = (
            f"\n\nINTRADAY 1m OHLCV (prices in exchange native units):\n{self.intraday_csv}"
            if self.intraday_csv else
            "\n\n(No intraday OHLCV available — use trade prices for context.)"
        )

        technical_analyst = Agent(
            role="Intraday Technical Analyst",
            goal="Identify key technical events from 1-minute OHLCV data",
            backstory=(
                "You are an expert at reading intraday price action. You identify VWAP levels, "
                "Opening Range Breakouts (ORB — high/low of first 15 minutes), EMA 9/21 crossovers, "
                "RSI signals (above 67 = overbought, below 33 = oversold), volume spikes "
                "(>2× average), and key support/resistance zones."
            ),
            llm=model,
            verbose=False,
        )

        trade_coach = Agent(
            role="Professional Trading Coach",
            goal="Generate detailed signal timelines and coaching analysis for individual trades",
            backstory=(
                "You are a trading mentor who reviews each trade against its market context. "
                "You write clear signal timelines that show what the market signalled before, "
                "during and after a trade, and give honest, specific coaching on what worked "
                "and what the trader should improve."
            ),
            llm=model,
            verbose=False,
        )

        analyze_market = Task(
            description=(
                f"Analyze intraday data for {trade['instrument']} on {trade.get('date', 'the trade date')}.\n"
                f"Trade context:\n"
                f"  Direction : {trade['direction']}\n"
                f"  Entry     : {trade['entry_time']} @ ₹{trade['entry_price']}\n"
                f"  Exit      : {trade['exit_time']}  @ ₹{trade['exit_price']}\n"
                f"  Qty       : {trade['qty']}  |  P&L : ₹{trade['pnl']}\n"
                f"{ohlcv_block}\n\n"
                "Identify 7–10 key events during the session. Cover:\n"
                "- Market open: gap direction, opening bias\n"
                "- VWAP: when price crossed / reclaimed VWAP\n"
                "- ORB: first-15m high/low breakout if it occurred\n"
                "- EMA 9/21 crossover or price interaction\n"
                "- RSI: any extreme readings or divergences\n"
                "- Volume: any spikes at key candles\n"
                "- The entry event and the exit event\n"
                "- What happened for 15-45 minutes after the exit, including whether price extended further or reversed\n"
                "- The best realistic exit window visible in hindsight, if it differed from the actual exit\n\n"
                "For each event output:\n"
                '  {"time":"HH:MM","title":"...","description":"one sentence","tag":"TAG or null"}\n'
                "Valid tags: EMA, VWAP, ORB, RSI, VOLUME, ENTRY, EXIT, POST-EXIT, EXIT REVIEW, CAUTION, EXIT SIGNAL"
            ),
            expected_output='JSON array only: [{"time":"HH:MM","title":"...","description":"...","tag":"..."}]',
            agent=technical_analyst,
        )

        analyze_trade = Task(
            description=(
                f"Using the market events identified, write a complete trade report.\n\n"
                f"Trade:\n"
                f"  Instrument : {trade['instrument']}\n"
                f"  Direction  : {trade['direction']}\n"
                f"  Entry      : {trade['entry_time']} @ ₹{trade['entry_price']}\n"
                f"  Exit       : {trade['exit_time']}  @ ₹{trade['exit_price']}\n"
                f"  Qty        : {trade['qty']}  |  P&L : ₹{trade['pnl']}\n\n"
                "Output a JSON object with exactly these keys:\n"
                "  signal_timeline      : refined event array from previous task\n"
                "  strategy             : short setup label, 2-5 words (example: 'ORB breakdown', 'VWAP reclaim long')\n"
                "  emotion              : one-word or short phrase trader state inferred from the trade (example: 'disciplined', 'hesitant', 'impatient', 'confident')\n"
                "  why_it_worked        : 2-3 sentences (honest if it was a loss)\n"
                "  what_could_be_better : 2-3 sentences focused on execution quality, especially exit timing and what price did after exit\n"
                "  post_exit_summary    : 1-2 sentences describing what price did after the actual exit\n"
                "  lessons              : list of 3-5 short lesson strings\n"
                "  trade_narrative      : 3-4 sentence story of the full trade\n\n"
                "The signal_timeline must include an 'ENTRY REVIEW' event for the best realistic entry window in hindsight "
                "and an 'EXIT REVIEW' event for the best realistic exit window in hindsight.\n\n"
                "Be specific: mention prices, times, indicator values. "
                "If the setup is unclear, choose the closest concise strategy label instead of null. "
                "If emotion is ambiguous, infer the most likely trading state from the execution quality. "
                "Do not give generic advice like 'use a stoploss', 'manage risk better', or 'follow your plan'. "
                "Prefer concrete comments about whether the actual exit was early, late, or close to optimal and what the market did after exit. "
                "For the ENTRY REVIEW and EXIT REVIEW timeline events, always mention specific time(s) and approximate price level(s)."
            ),
            expected_output="JSON object with keys: signal_timeline, strategy, emotion, why_it_worked, what_could_be_better, post_exit_summary, lessons, trade_narrative",
            agent=trade_coach,
            context=[analyze_market],
            output_pydantic=TradeAnalysisResult,
        )

        crew = Crew(
            agents=[technical_analyst, trade_coach],
            tasks=[analyze_market, analyze_trade],
            process=Process.sequential,
            verbose=False,
        )

        try:
            result = crew.kickoff()
        except Exception as exc:
            record_llm_metric(operation="trade_analysis", started_at=started_at, success=False, error=str(exc))
            raise
        usage = getattr(result, "token_usage", None)
        record_llm_metric(
            operation="trade_analysis",
            started_at=started_at,
            success=True,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )

        if hasattr(result, "pydantic") and result.pydantic:
            data: TradeAnalysisResult = result.pydantic
            return {
                "signal_timeline": [e.model_dump() for e in data.signal_timeline],
                "strategy": data.strategy,
                "emotion": data.emotion,
                "analysis": {
                    "why_it_worked": data.why_it_worked,
                    "what_could_be_better": data.what_could_be_better,
                    "post_exit_summary": data.post_exit_summary,
                    "lessons": data.lessons,
                    "trade_narrative": data.trade_narrative,
                },
            }

        raw = result.raw if hasattr(result, "raw") else str(result)
        return parse_trade_result(raw)
