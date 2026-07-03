"""HTTP routes for activity ingestion and daily trace retrieval."""

from flask import Blueprint, current_app, jsonify, request

from .daily_trace import build_daily_trace
from .ingest import InvalidActivity, normalize_activity


api = Blueprint("pulse", __name__)


@api.post("/activities")
def post_activity():
    try:
        activity = normalize_activity(request.get_json(silent=True))
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
