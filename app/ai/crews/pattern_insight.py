from __future__ import annotations

import time

from crewai import Agent, Crew, Process, Task

from app.ai.shared import has_openai_key, llm, record_llm_metric


class PatternInsightCrew:
    def __init__(self, windows: list[dict], scope: str):
        self.windows = windows
        self.scope = scope

    def run(self) -> str | None:
        if not has_openai_key():
            return None

        started_at = time.perf_counter()
        model = llm()
        pattern_analyst = Agent(
            role="Trading Pattern Analyst",
            goal="Identify time-of-day performance patterns and give actionable advice",
            backstory=(
                "You are a quantitative trading analyst who specialises in identifying "
                "when traders perform best and worst during the session. You give specific, "
                "concise recommendations based on performance data."
            ),
            llm=model,
            verbose=False,
        )
        scope_label = "daily" if self.scope == "day" else "monthly aggregated"
        windows_text = "\n".join(
            f"  {w['label']}: P&L=₹{w['pnl']}, trades={w['trades']}, wins={w['wins']}, losses={w['losses']}"
            for w in self.windows
        )
        write_pattern = Task(
            description=(
                f"Analyze this {scope_label} time-window performance:\n\n"
                f"{windows_text}\n\n"
                "Write 2-3 sentences starting with 'Pattern:' covering:\n"
                "1. The best performing window and what it suggests\n"
                "2. The worst window to avoid (if any has net negative P&L)\n"
                "3. One specific actionable recommendation\n"
                "Use the exact time labels (e.g. 09:15-10:30) in your answer."
            ),
            expected_output="2-3 sentences starting with 'Pattern:'",
            agent=pattern_analyst,
        )
        crew = Crew(agents=[pattern_analyst], tasks=[write_pattern], process=Process.sequential, verbose=False)
        try:
            result = crew.kickoff()
        except Exception as exc:
            record_llm_metric(operation="pattern_insight", started_at=started_at, success=False, error=str(exc))
            raise
        usage = getattr(result, "token_usage", None)
        record_llm_metric(
            operation="pattern_insight",
            started_at=started_at,
            success=True,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
        raw = result.raw if hasattr(result, "raw") else str(result)
        return raw.strip() or None
