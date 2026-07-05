"""HTTP routes for activity ingestion and daily trace retrieval."""

from datetime import date
from pathlib import Path
import re

from flask import Blueprint, Response, current_app, jsonify, request

from .daily_trace import (
    build_available_days,
    build_daily_summary,
    build_daily_trace,
    primary_workspace,
    render_available_days_html,
    render_daily_trace_html,
    render_daily_trace_markdown,
)
from .ingest import IgnoredActivity, InvalidActivity, normalize_activity


api = Blueprint("pulse", __name__)
TRACE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_trace_date(value: str) -> date:
    if not TRACE_DATE_PATTERN.fullmatch(value):
        raise ValueError
    return date.fromisoformat(value)


def _build_status(trace):
    summary = build_daily_summary(trace)
    last_event = None
    if trace["sessions"]:
        activity = trace["sessions"][-1]["activities"][-1]
        last_event = {
            "type": activity["type"],
            "occurred_at": activity["occurred_at"],
            "summary": activity["summary"],
        }
    database_path = Path(current_app.config["DATABASE_PATH"])
    return {
        "daemon": "running",
        "url": "http://127.0.0.1:5000/",
        "database_path": str(database_path),
        "database_exists": database_path.exists(),
        "date": trace["date"],
        "event_count": trace["activity_count"],
        "displayed_session_count": summary["session_count"],
        "last_event": last_event,
        "primary_workspace": primary_workspace(trace),
        "terminal_watcher": "external; source the Zsh script separately",
    }


@api.get("/")
def get_home():
    trace = build_daily_trace(current_app.config["TRACE_STORE"])
    return Response(
        render_daily_trace_html(trace, system_status=_build_status(trace)),
        mimetype="text/html",
    )


@api.get("/status")
def get_status():
    trace = build_daily_trace(current_app.config["TRACE_STORE"])
    return jsonify(_build_status(trace))


@api.post("/activities")
def post_activity():
    try:
        activity = normalize_activity(request.get_json(silent=True))
    except IgnoredActivity:
        return "", 204
    except InvalidActivity as exc:
        return jsonify({"error": str(exc)}), 400

    stored = current_app.config["TRACE_STORE"].append(activity)
    return (
        jsonify(
            {
                "id": stored.id,
                "session_id": stored.session_id,
                "type": activity.activity_type,
                "occurred_at": activity.occurred_at_utc.isoformat(),
            }
        ),
        201,
    )


@api.get("/trace/today")
def get_today_trace():
    trace = build_daily_trace(current_app.config["TRACE_STORE"])
    return jsonify(trace)


@api.get("/trace/days")
def get_trace_days():
    return jsonify(build_available_days(current_app.config["TRACE_STORE"]))


@api.get("/days")
def get_days():
    available_days = build_available_days(current_app.config["TRACE_STORE"])
    return Response(
        render_available_days_html(available_days),
        mimetype="text/html",
    )


@api.get("/day/<date_value>")
def get_day(date_value):
    try:
        selected_date = _parse_trace_date(date_value)
    except ValueError:
        return jsonify({"error": "invalid date; expected YYYY-MM-DD"}), 400
    trace = build_daily_trace(
        current_app.config["TRACE_STORE"],
        day=selected_date,
    )
    return Response(
        render_daily_trace_html(
            trace,
            trace_json_url=f"/trace/{date_value}",
            trace_markdown_url=f"/trace/{date_value}.md",
            archive_mode=True,
        ),
        mimetype="text/html",
    )


@api.get("/trace/<date_value>")
def get_dated_trace(date_value):
    try:
        selected_date = _parse_trace_date(date_value)
    except ValueError:
        return jsonify({"error": "invalid date; expected YYYY-MM-DD"}), 400
    return jsonify(
        build_daily_trace(
            current_app.config["TRACE_STORE"],
            day=selected_date,
        )
    )


@api.get("/trace/<date_value>.md")
def get_dated_trace_markdown(date_value):
    try:
        selected_date = _parse_trace_date(date_value)
    except ValueError:
        return jsonify({"error": "invalid date; expected YYYY-MM-DD"}), 400
    trace = build_daily_trace(
        current_app.config["TRACE_STORE"],
        day=selected_date,
    )
    return Response(
        render_daily_trace_markdown(trace, archive_mode=True),
        mimetype="text/markdown",
    )


@api.get("/trace/today.md")
def get_today_trace_markdown():
    trace = build_daily_trace(current_app.config["TRACE_STORE"])
    return Response(render_daily_trace_markdown(trace), mimetype="text/markdown")
