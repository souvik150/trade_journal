"""
CrewAI crews for generating AI-powered trade journal content.

Three crews:
  TradeAnalysisCrew   → signal_timeline + analysis & lessons  (per trade)
  InstrumentAnalysisCrew → signal_timeline + analysis for one instrument/day
  DailyJournalCrew    → smart_insight                         (per day)
  PatternInsightCrew  → pattern_insight                       (time-window)

Each crew falls back gracefully when OPENAI_API_KEY is not set.
"""

from __future__ import annotations

import json
import os

from crewai import Agent, Crew, Process, Task
from crewai import LLM
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

class SignalEvent(BaseModel):
    time:        str
    title:       str
    description: str
    tag:         str | None = None


class TradeAnalysisResult(BaseModel):
    signal_timeline:       list[SignalEvent] = []
    why_it_worked:         str | None = None
    what_could_be_better:  str | None = None
    post_exit_summary:     str | None = None
    lessons:               list[str]  = []
    trade_narrative:       str | None = None
    strategy:              str | None = None
    emotion:               str | None = None


class TradeTagResult(BaseModel):
    strategy: str | None = None
    emotion:  str | None = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _llm() -> LLM:
    return LLM(model="gpt-4o-mini", temperature=0.3)


def _no_key_trade_result() -> dict:
    return {
        "signal_timeline": [],
        "strategy": None,
        "emotion": None,
        "analysis": {
            "why_it_worked":        None,
            "what_could_be_better": None,
            "post_exit_summary":    None,
            "lessons":              [],
            "trade_narrative":      None,
        },
    }


def _parse_trade_result(raw: str) -> dict:
    """Try to extract a TradeAnalysisResult from a raw LLM string."""
    try:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(raw[start:end])
            return {
                "signal_timeline": data.get("signal_timeline", []),
                "strategy": data.get("strategy"),
                "emotion": data.get("emotion"),
                "analysis": {
                    "why_it_worked":        data.get("why_it_worked"),
                    "what_could_be_better": data.get("what_could_be_better"),
                    "post_exit_summary":    data.get("post_exit_summary"),
                    "lessons":              data.get("lessons", []),
                    "trade_narrative":      data.get("trade_narrative"),
                },
            }
    except Exception:
        pass
    return _no_key_trade_result()


def _parse_trade_tag_result(raw: str) -> dict:
    """Try to extract strategy/emotion tags from a raw LLM string."""
    try:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(raw[start:end])
            return {
                "strategy": data.get("strategy"),
                "emotion": data.get("emotion"),
            }
    except Exception:
        pass
    return {"strategy": None, "emotion": None}


# ---------------------------------------------------------------------------
# 1. Trade Analysis Crew
# ---------------------------------------------------------------------------

class TradeAnalysisCrew:
    """
    Agents
    ------
    TechnicalAnalyst : reads 1m OHLCV → identifies market events with tags
    TradeCoach       : reads trade + events → signal_timeline + analysis

    Input
    -----
    trade        : dict from analytics.pair_trades (includes date, instrument, etc.)
    intraday_csv : 1m OHLCV CSV string from intraday_tool.fetch_intraday_csv
    """

    def __init__(self, trade: dict, intraday_csv: str):
        self.trade        = trade
        self.intraday_csv = intraday_csv

    def run(self) -> dict:
        if not os.getenv("OPENAI_API_KEY"):
            return _no_key_trade_result()

        llm   = _llm()
        trade = self.trade

        ohlcv_block = (
            f"\n\nINTRADAY 1m OHLCV (prices in exchange native units):\n{self.intraday_csv}"
            if self.intraday_csv else
            "\n\n(No intraday OHLCV available — use trade prices for context.)"
        )

        # -- Agents -----------------------------------------------------------
        technical_analyst = Agent(
            role="Intraday Technical Analyst",
            goal="Identify key technical events from 1-minute OHLCV data",
            backstory=(
                "You are an expert at reading intraday price action. You identify VWAP levels, "
                "Opening Range Breakouts (ORB — high/low of first 15 minutes), EMA 9/21 crossovers, "
                "RSI signals (above 67 = overbought, below 33 = oversold), volume spikes "
                "(>2× average), and key support/resistance zones."
            ),
            llm=llm,
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
            llm=llm,
            verbose=False,
        )

        # -- Tasks ------------------------------------------------------------
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
            expected_output=(
                'JSON array only: [{"time":"HH:MM","title":"...","description":"...","tag":"..."}]'
            ),
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
            expected_output=(
                "JSON object with keys: signal_timeline, strategy, emotion, "
                "why_it_worked, what_could_be_better, post_exit_summary, lessons, trade_narrative"
            ),
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

        result = crew.kickoff()

        # structured pydantic output
        if hasattr(result, "pydantic") and result.pydantic:
            data: TradeAnalysisResult = result.pydantic
            return {
                "signal_timeline": [e.model_dump() for e in data.signal_timeline],
                "strategy": data.strategy,
                "emotion": data.emotion,
                "analysis": {
                    "why_it_worked":        data.why_it_worked,
                    "what_could_be_better": data.what_could_be_better,
                    "post_exit_summary":    data.post_exit_summary,
                    "lessons":              data.lessons,
                    "trade_narrative":      data.trade_narrative,
                },
            }

        # fallback: parse raw string
        raw = result.raw if hasattr(result, "raw") else str(result)
        return _parse_trade_result(raw)


# ---------------------------------------------------------------------------
# 2. Daily Journal Crew
# ---------------------------------------------------------------------------

class DailyJournalCrew:
    """
    Single agent: reads all paired trades + session stats for one day
    and writes a 3-4 sentence smart insight.
    """

    def __init__(self, trades: list[dict], date: str, stats: dict):
        self.trades = trades
        self.date   = date
        self.stats  = stats

    def run(self) -> list[str]:
        if not os.getenv("OPENAI_API_KEY"):
            return []

        from tools.intraday_tool import format_trades_for_llm

        llm = _llm()

        journal_writer = Agent(
            role="Trading Journal Writer",
            goal="Write concise, honest daily trading summaries that help traders improve",
            backstory=(
                "You are an experienced trading mentor. You read a trader's daily activity "
                "and write a sharp 3-4 sentence insight in second person (You...) that "
                "highlights their best decision, their most avoidable mistake, and one "
                "specific recurring pattern they should be aware of."
            ),
            llm=llm,
            verbose=False,
        )

        trades_text = format_trades_for_llm(self.trades)
        s = self.stats

        write_insight = Task(
            description=(
                f"Write a smart insight for {self.date}.\n\n"
                f"Session stats: Net P&L ₹{s.get('net_pnl')} | "
                f"Trades {s.get('trades')} | Win Rate {s.get('win_rate')}%\n\n"
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

        crew = Crew(
            agents=[journal_writer],
            tasks=[write_insight],
            process=Process.sequential,
            verbose=False,
        )

        result = crew.kickoff()
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


# ---------------------------------------------------------------------------
# 3. Instrument Analysis Crew
# ---------------------------------------------------------------------------

class InstrumentAnalysisCrew:
    """
    Single instrument/day analysis across all paired trades for that instrument.
    Produces a combined setup tag, emotion tag, signal timeline, and coaching analysis.
    """

    def __init__(self, instrument: str, date: str, trades: list[dict], intraday_csv: str):
        self.instrument = instrument
        self.date = date
        self.trades = trades
        self.intraday_csv = intraday_csv

    def run(self) -> dict:
        if not os.getenv("OPENAI_API_KEY"):
            return _no_key_trade_result()

        from tools.intraday_tool import format_trades_for_llm

        llm = _llm()
        trades_text = format_trades_for_llm(self.trades)
        ohlcv_block = (
            f"\n\nINTRADAY 1m OHLCV (prices in exchange native units):\n{self.intraday_csv}"
            if self.intraday_csv else
            "\n\n(No intraday OHLCV available — use the trade sequence for context.)"
        )

        analyst = Agent(
            role="Instrument Trading Analyst",
            goal="Review all trades for one instrument on one day and produce a compact, actionable report",
            backstory=(
                "You analyze how a trader handled one instrument across the session. "
                "You identify the key market moments, summarize the dominant setup, "
                "infer the trader's state, and give concise coaching."
            ),
            llm=llm,
            verbose=False,
        )

        analyze_instrument = Task(
            description=(
                f"Analyze all trades for {self.instrument} on {self.date}.\n\n"
                f"Trades:\n{trades_text}\n"
                f"{ohlcv_block}\n\n"
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
            expected_output=(
                "JSON object with keys: signal_timeline, strategy, emotion, "
                "why_it_worked, what_could_be_better, lessons, trade_narrative"
            ),
            agent=analyst,
            output_pydantic=TradeAnalysisResult,
        )

        crew = Crew(
            agents=[analyst],
            tasks=[analyze_instrument],
            process=Process.sequential,
            verbose=False,
        )

        result = crew.kickoff()

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
        return _parse_trade_result(raw)


# ---------------------------------------------------------------------------
# 4. Trade Tagging Crew
# ---------------------------------------------------------------------------

class TradeTaggingCrew:
    """
    Single lightweight agent: infers only strategy and emotion from a paired trade.
    This is intentionally cheaper than TradeAnalysisCrew and does not use intraday OHLCV.
    """

    def __init__(self, trade: dict, date: str | None = None):
        self.trade = trade
        self.date = date

    def run(self) -> dict:
        if not os.getenv("OPENAI_API_KEY"):
            return {"strategy": None, "emotion": None}

        llm = _llm()
        trade = self.trade

        tagger = Agent(
            role="Trade Setup Tagger",
            goal="Infer concise trade setup and trader emotion labels from trade execution details",
            backstory=(
                "You classify trades into short strategy labels and infer the likely trader state "
                "from entry, exit, direction, timing, and P&L. You stay concise and practical."
            ),
            llm=llm,
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

        crew = Crew(
            agents=[tagger],
            tasks=[tag_trade],
            process=Process.sequential,
            verbose=False,
        )

        result = crew.kickoff()

        if hasattr(result, "pydantic") and result.pydantic:
            data: TradeTagResult = result.pydantic
            return {
                "strategy": data.strategy,
                "emotion": data.emotion,
            }

        raw = result.raw if hasattr(result, "raw") else str(result)
        return _parse_trade_tag_result(raw)


# ---------------------------------------------------------------------------
# 5. Pattern Insight Crew
# ---------------------------------------------------------------------------

class PatternInsightCrew:
    """
    Single agent: reads time-window P&L breakdown and writes a
    2-3 sentence actionable pattern insight.
    """

    def __init__(self, windows: list[dict], scope: str):
        self.windows = windows
        self.scope   = scope

    def run(self) -> str | None:
        if not os.getenv("OPENAI_API_KEY"):
            return None

        llm = _llm()

        pattern_analyst = Agent(
            role="Trading Pattern Analyst",
            goal="Identify time-of-day performance patterns and give actionable advice",
            backstory=(
                "You are a quantitative trading analyst who specialises in identifying "
                "when traders perform best and worst during the session. You give specific, "
                "concise recommendations based on performance data."
            ),
            llm=llm,
            verbose=False,
        )

        scope_label = "daily" if self.scope == "day" else "monthly aggregated"
        windows_text = "\n".join(
            f"  {w['label']}: P&L=₹{w['pnl']}, trades={w['trades']}, "
            f"wins={w['wins']}, losses={w['losses']}"
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

        crew = Crew(
            agents=[pattern_analyst],
            tasks=[write_pattern],
            process=Process.sequential,
            verbose=False,
        )

        result = crew.kickoff()
        raw = result.raw if hasattr(result, "raw") else str(result)
        return raw.strip() or None
