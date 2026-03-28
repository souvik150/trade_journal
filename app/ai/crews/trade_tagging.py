from __future__ import annotations

import time

from crewai import Agent, Crew, Process, Task

from app.ai.shared import TradeTagResult, has_openai_key, llm, parse_trade_tag_result, record_llm_metric


class TradeTaggingCrew:
    def __init__(self, trade: dict, date: str | None = None):
        self.trade = trade
        self.date = date

    def run(self) -> dict:
        if not has_openai_key():
            return {"strategy": None, "emotion": None}

        started_at = time.perf_counter()
        trade = self.trade
        model = llm()
        tagger = Agent(
            role="Trade Setup Tagger",
            goal="Infer concise trade setup and trader emotion labels from trade execution details",
            backstory=(
                "You classify trades into short strategy labels and infer the likely trader state "
                "from entry, exit, direction, timing, and P&L. You stay concise and practical."
            ),
            llm=model,
            verbose=False,
        )
        tag_trade = Task(
            description=(
                f"Classify this trade from {self.date or 'the session'}.\n\n"
                f"Instrument : {trade['instrument']}\n"
                f"Direction  : {trade['direction']}\n"
                f"Entry      : {trade['entry_time']} @ ₹{trade['entry_price']}\n"
                f"Exit       : {trade['exit_time']} @ ₹{trade['exit_price']}\n"
                f"Qty        : {trade['qty']}\n"
                f"P&L        : ₹{trade['pnl']}\n\n"
                "Output a JSON object with exactly these keys:\n"
                "  strategy : short setup label, 2-5 words\n"
                "  emotion  : one-word or short phrase trader state\n\n"
                "Examples of strategy labels: 'opening momentum', 'ORB breakdown', "
                "'VWAP reclaim long', 'trend-follow short', 'late chase'.\n"
                "Examples of emotion labels: 'disciplined', 'confident', 'hesitant', "
                "'impatient', 'reactive'.\n"
                "Do not use null unless the trade details are genuinely insufficient."
            ),
            expected_output="JSON object with keys: strategy, emotion",
            agent=tagger,
            output_pydantic=TradeTagResult,
        )
        crew = Crew(agents=[tagger], tasks=[tag_trade], process=Process.sequential, verbose=False)
        try:
            result = crew.kickoff()
        except Exception as exc:
            record_llm_metric(operation="trade_tagging", started_at=started_at, success=False, error=str(exc))
            raise
        record_llm_metric(operation="trade_tagging", started_at=started_at, success=True)

        if hasattr(result, "pydantic") and result.pydantic:
            data: TradeTagResult = result.pydantic
            return {"strategy": data.strategy, "emotion": data.emotion}

        raw = result.raw if hasattr(result, "raw") else str(result)
        return parse_trade_tag_result(raw)
