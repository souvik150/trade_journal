from __future__ import annotations

import json
import time

from crewai import Agent, Crew, Process, Task

from app.ai.shared import has_openai_key, llm, record_llm_metric


class DailyJournalCrew:
    def __init__(self, trades: list[dict], date: str, stats: dict):
        self.trades = trades
        self.date = date
        self.stats = stats

    def run(self) -> list[str]:
        if not has_openai_key():
            return []

        from app.integrations.intraday import format_trades_for_llm

        started_at = time.perf_counter()
        model = llm()
        journal_writer = Agent(
            role="Trading Journal Writer",
            goal="Write concise, honest daily trading summaries that help traders improve",
            backstory=(
                "You are an experienced trading mentor. You read a trader's daily activity "
                "and write a sharp 3-4 sentence insight in second person (You...) that "
                "highlights their best decision, their most avoidable mistake, and one "
                "specific recurring pattern they should be aware of."
            ),
            llm=model,
            verbose=False,
        )
        trades_text = format_trades_for_llm(self.trades)
        stats = self.stats
        write_insight = Task(
            description=(
                f"Write a smart insight for {self.date}.\n\n"
                f"Session stats: Net P&L ₹{stats.get('net_pnl')} | Trades {stats.get('trades')} | Win Rate {stats.get('win_rate')}%\n\n"
                f"Trades:\n{trades_text}\n\n"
                "Write 3-4 sentences in second person (You...) covering:\n"
                "1. Best trade: instrument, time, P&L, why it worked\n"
                "2. Most avoidable loss or mistake: what happened and why it was avoidable\n"
                "3. One specific pattern the trader repeats (positive or negative)\n"
                "Be specific with instrument names, prices, and times."
            ),
            expected_output="JSON array of 3-5 short insight strings in second person.",
            agent=journal_writer,
        )
        crew = Crew(agents=[journal_writer], tasks=[write_insight], process=Process.sequential, verbose=False)
        try:
            result = crew.kickoff()
        except Exception as exc:
            record_llm_metric(operation="daily_journal", started_at=started_at, success=False, error=str(exc))
            raise
        record_llm_metric(operation="daily_journal", started_at=started_at, success=True)
        raw = result.raw if hasattr(result, "raw") else str(result)
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start != -1 and end > start:
                data = json.loads(raw[start:end])
                if isinstance(data, list):
                    return [str(item).strip() for item in data if str(item).strip()]
        except Exception:
            pass
        text = raw.strip()
        return [text] if text else []
