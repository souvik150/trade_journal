from __future__ import annotations

import time

from crewai import Agent, Crew, Process, Task

from app.ai.shared import TradeAnalysisResult, has_openai_key, llm, no_key_trade_result, parse_trade_result, record_llm_metric


class InstrumentAnalysisCrew:
    def __init__(
        self,
        instrument: str,
        date: str,
        trades: list[dict],
        intraday_csv: str,
        best_entry: dict | None = None,
        best_exit: dict | None = None,
    ):
        self.instrument = instrument
        self.date = date
        self.trades = trades
        self.intraday_csv = intraday_csv
        self.best_entry = best_entry
        self.best_exit = best_exit

    def run(self) -> dict:
        if not has_openai_key():
            return no_key_trade_result()

        from app.integrations.intraday import format_trades_for_llm

        started_at = time.perf_counter()
        model = llm()
        trades_text = format_trades_for_llm(self.trades)
        ohlcv_block = (
            f"\n\nINTRADAY 1m OHLCV (prices in exchange native units):\n{self.intraday_csv}"
            if self.intraday_csv else
            "\n\n(No intraday OHLCV available — use the trade sequence for context.)"
        )

        # Ground truth computed from actual OHLCV — use these exact values
        verified_block = ""
        if self.best_entry or self.best_exit:
            lines = ["\n\nVERIFIED BEST ENTRY/EXIT (computed from actual OHLCV — do NOT deviate from these):"]
            if self.best_entry:
                c = self.best_entry.get("candle", {})
                lines.append(
                    f"  BEST ENTRY: time={self.best_entry['time']}  price={self.best_entry['price']}"
                    f"  (candle O={c.get('open')} H={c.get('high')} L={c.get('low')} C={c.get('close')})"
                )
            if self.best_exit:
                c = self.best_exit.get("candle", {})
                lines.append(
                    f"  BEST EXIT:  time={self.best_exit['time']}  price={self.best_exit['price']}"
                    f"  (candle O={c.get('open')} H={c.get('high')} L={c.get('low')} C={c.get('close')})"
                )
            lines.append("  Use these exact times and prices in the Entry Review and Exit Review timeline events.")
            verified_block = "\n".join(lines)
        analyst = Agent(
            role="Instrument Trading Analyst",
            goal="Review all trades for one instrument on one day and produce a compact, actionable report",
            backstory=(
                "You analyze how a trader handled one instrument across the session. "
                "You identify the key market moments, summarize the dominant setup, "
                "infer the trader's state, and give concise coaching."
            ),
            llm=model,
            verbose=False,
        )
        analyze_instrument = Task(
            description=(
                f"Analyze all trades for {self.instrument} on {self.date}.\n\n"
                f"Trades:\n{trades_text}\n"
                f"{ohlcv_block}"
                f"{verified_block}\n\n"
                "Output a JSON object with exactly these keys:\n"
                "  signal_timeline      : 6-10 key events for this instrument during the session\n"
                "  strategy             : the dominant setup label, 2-5 words\n"
                "  emotion              : the most likely trader state across these trades\n"
                "  why_it_worked        : 2-3 sentences\n"
                "  what_could_be_better : 2-3 sentences focused on execution quality and exit timing\n"
                "  post_exit_summary    : 1-2 sentences describing what happened after the actual exit(s)\n"
                "  lessons              : list of 3-5 short lesson strings\n"
                "  trade_narrative      : 3-4 sentence summary of the full instrument story\n\n"
                "The signal_timeline must include:\n"
                "- at least one entry event\n"
                "- at least one actual exit event\n"
                "- at least one post-exit event describing what happened after the exit\n"
                "- at least one entry review event stating the best realistic entry window in hindsight\n"
                "- at least one exit review event stating the best realistic exit window in hindsight\n\n"
                "Use prices and times from the trades where possible. "
                "Do not give generic feedback like 'use a stoploss', 'risk management', or 'be more disciplined'. "
                "Focus on market behavior, execution quality, and whether the exit captured enough of the move. "
                "For the ENTRY REVIEW and EXIT REVIEW timeline events, always mention specific time(s) and approximate price level(s)."
            ),
            expected_output="JSON object with keys: signal_timeline, strategy, emotion, why_it_worked, what_could_be_better, lessons, trade_narrative",
            agent=analyst,
            output_pydantic=TradeAnalysisResult,
        )
        crew = Crew(agents=[analyst], tasks=[analyze_instrument], process=Process.sequential, verbose=False)
        try:
            result = crew.kickoff()
        except Exception as exc:
            record_llm_metric(operation="instrument_analysis", started_at=started_at, success=False, error=str(exc))
            raise
        usage = getattr(result, "token_usage", None)
        record_llm_metric(
            operation="instrument_analysis",
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
