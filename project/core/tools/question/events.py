"""Event formatting for question tool."""
from __future__ import annotations

def format_event(question: str = "", **_) -> str:
    # Rule: Never display long free-form inputs such as full questions
    # But event is static per spec: "Waiting for you"
    return "Waiting for you"
