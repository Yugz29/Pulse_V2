"""HTML renderers for daily traces and available-day archives."""

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from ..analysis.projects import (
    activity_project_context,
    activity_project_root,
    resolve_project_context,
)
from ..analysis.timeline import (
    _app_activation_counts,
    _display_file_path,
    _display_time,
    _displayed_sessions,
    _file_change_groups,
    _passive_sessions,
    _ranked_apps,
    _session_duration,
    _session_observed_bounds,
    _session_project_sequence,
)
from ..daily_trace import (
    _session_project_summaries,
    _terminal_labels,
    build_current_state,
    build_daily_summary,
    build_resume,
    build_session_summary,
)


def _html_summary_facts(facts: list[str | tuple[str, list[str]]]) -> str:
    items = []
    for fact in facts:
        if isinstance(fact, tuple):
            label, details = fact
            nested = "".join(f"<li>{escape(detail)}</li>" for detail in details)
            items.append(f"<li>{escape(label)}<ul>{nested}</ul></li>")
        else:
            items.append(f"<li>{escape(fact)}</li>")
    return f"<ul>{''.join(items)}</ul>"


def render_daily_trace_html(
    trace: dict[str, Any],
    system_status: dict[str, Any] | None = None,
    trace_json_url: str = "/trace/today",
    trace_markdown_url: str = "/trace/today.md",
    archive_mode: bool = False,
) -> str:
    summary = build_daily_summary(
        trace,
        project_mode="archive" if archive_mode else "live",
    )
    current = build_current_state(trace) if not archive_mode else None
    resume = build_resume(trace) if not archive_mode else []
    displayed_sessions = _displayed_sessions(trace)
    passive_sessions = _passive_sessions(trace)
    apps = [escape(str(app)) for app, _count in _ranked_apps(summary["apps"])]
    projects = [
        f'<span title="{escape(path)}">'
        f"{escape(resolve_project_context(path).project_name)}</span>"
        for path in summary["workspaces"]
    ]
    project_workspaces = set(summary["workspaces"])
    last_activity = (
        f"{escape(str(current['last_activity_type']))} — "
        f"{escape(str(current['last_activity_description']))}"
        if current and current["last_activity_type"]
        else "Non détectée"
    )
    recent_files = (
        "<ul>"
        + "".join(
            f"<li>{escape(str(item['event']).capitalize())} "
            f"<code>{escape(item['path'])}</code></li>"
            for item in current["recent_files"]
        )
        + "</ul>"
        if current and current["recent_files"]
        else "Aucun"
    )
    navigation = []
    if archive_mode:
        navigation.append(
            '<a class="nav-main" href="#resume-jour">Résumé du jour</a>'
        )
    else:
        navigation.append(
            '<a class="nav-main" href="#maintenant">Maintenant</a>'
        )
        if resume:
            navigation.append('<a class="nav-main" href="#reprise">Reprise</a>')
        navigation.append(
            '<a class="nav-main" href="#aujourdhui">Aujourd’hui</a>'
        )
    if system_status and not archive_mode:
        navigation.append(
            '<a class="nav-main" href="#etat-systeme">État système</a>'
        )
    for index, session in enumerate(displayed_sessions, start=1):
        navigation.append(
            f'<a class="nav-session" href="#session-{index}">Session {index}</a>'
        )
        for project_index, workspace in enumerate(
            _session_project_sequence(session, project_workspaces), start=1
        ):
            navigation.append(
                f'<a class="nav-project" '
                f'href="#session-{index}-projet-{project_index}">'
                f"{escape(resolve_project_context(workspace).project_name)}</a>"
            )
    end_anchor = "timeline-end" if archive_mode else "timeline-live"
    end_label = "Fin du jour" if archive_mode else "Direct"
    navigation.append(
        f'<a class="nav-main nav-live nav-bottom" '
        f'href="#{end_anchor}">{end_label}</a>'
    )
    body = [
        "<!doctype html>",
        '<html lang="fr"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        (
            f"<title>Pulse — Journal du {escape(trace['date'])}</title>"
            if archive_mode
            else f"<title>Pulse — {escape(trace['date'])}</title>"
        ),
        """<style>
:root{color-scheme:dark;--bg:#11151a;--panel:#191f26;--panel-soft:#161c22;
--border:#2a333d;--text:#d7dee7;--muted:#8f9aaa;--link:#83a9d8}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:1rem}
body{font:16px/1.6 system-ui,sans-serif;margin:0;padding:2.5rem 2rem 4rem;
background:var(--bg);color:var(--text)}.page-shell{max-width:1180px;margin:0 auto;
display:grid;grid-template-columns:12rem minmax(0,1fr);gap:2rem;align-items:start}
.sidebar{position:sticky;top:1rem;background:var(--panel-soft);border:1px solid var(--border);
border-radius:10px;padding:.9rem}.sidebar h2{font-size:.78rem;text-transform:uppercase;
letter-spacing:.08em;color:var(--muted);margin:0 0 .55rem}.sidebar a{display:block;
padding:.22rem .4rem;border-radius:5px;color:#aebdcb;font-size:.84rem}
.sidebar a:hover{background:#202832;text-decoration:none;color:#dbe5ef}
.sidebar .nav-live{color:#8fc6a1}
.sidebar .nav-bottom{margin-top:.8rem;border-top:1px solid var(--border);padding-top:.6rem}
.nav-session{margin-top:.45rem;font-weight:650;color:#c6d2df!important}
.nav-project{padding-left:1.15rem!important;color:#829bb6!important;font-size:.78rem!important}
header{margin-bottom:2rem}h1{font-size:2rem;letter-spacing:-.025em;margin:0 0 .25rem}
h2{color:#e4eaf1;letter-spacing:-.01em}.meta,.detail{color:var(--muted)}
.current,.resume,.summary,.system,.session{background:var(--panel);border:1px solid var(--border);
border-radius:12px;padding:1.25rem 1.5rem;margin:1.25rem 0;box-shadow:0 8px 24px #0003}
.current{border-top:3px solid #5d8fc4}.resume{border-top:3px solid #8d78b5}
.summary{border-top:3px solid #669b78}
.system{border-top:3px solid #778493;background:var(--panel-soft)}
.session{margin-top:1.5rem}.session h2{font-size:1.1rem;margin:0 0 1rem;color:#cbd5e1}
.current h2,.resume h2,.summary h2,.system h2{font-size:1.2rem;margin:0 0 1rem}
.resume dl{display:grid;grid-template-columns:10rem minmax(0,1fr);gap:.45rem 1rem;margin:0}
.resume dt{font-weight:650;color:#b8a8d3}.resume dd{margin:0;min-width:0;
overflow-wrap:anywhere;color:#cbd3dd}
.resume-git{margin:.9rem 0}.resume-git h3{font-size:1rem;color:#b8a8d3;
margin:0 0 .5rem}.resume-git ul{margin:.2rem 0 0;padding-left:1.2rem}
.current dl,.summary dl,.system dl{display:grid;grid-template-columns:12rem 1fr;
gap:.5rem 1.25rem;margin:0}.current dt,.summary dt,.system dt{font-weight:600;
color:#aeb9c6}.current dd,.summary dd,.system dd{margin:0;min-width:0;
overflow-wrap:anywhere}.timeline{list-style:none;padding:0;margin:0}.event{display:grid;
grid-template-columns:4rem 10rem 1fr;gap:1rem;padding:.85rem .25rem;
border-top:1px solid var(--border)}.event:first-child{border-top:0}.event time{
color:var(--muted);font-variant-numeric:tabular-nums}.type{font-family:ui-monospace,
SFMono-Regular,Menlo,monospace;font-size:.82rem;color:#9daaba;overflow-wrap:anywhere}
.content{min-width:0}.content code,.current code{background:#222a33;color:#dce6f1;
padding:.12rem .35rem;border:1px solid #303b47;border-radius:5px;overflow-wrap:anywhere}
.label{display:inline-block;margin-top:.18rem;border:1px solid transparent;border-radius:999px;
padding:.02rem .4rem;font-size:.72rem;font-weight:650}.label-test{background:#173528;
border-color:#285940;color:#8fd5aa}.label-git{background:#29243f;border-color:#493d70;
color:#b9a7ed}.label-pulse{background:#17333c;border-color:#285663;color:#86c9d8}
.label-erreur{background:#3a2023;border-color:#6d363d;color:#e8a0a7}
.commands{margin:.5rem 0;padding-left:1.35rem}.commands li{margin:.25rem 0}
.project-separator{padding:1rem .25rem .45rem;color:#8fb4dd;font-weight:650;
letter-spacing:.01em;border-top:1px solid var(--border)}.project-separator:first-child{
border-top:0;padding-top:.25rem}
.session-summary{margin:0 0 1rem;padding:.8rem 1rem;background:#151b21;
border:1px solid #26313b;border-radius:8px}.session-summary h3{font-size:.9rem;
color:#aebdcb;margin:0 0 .4rem}.session-summary ul{margin:0;padding-left:1.2rem;
color:#b8c2cd}.session-summary li{margin:.18rem 0}
.session-summary ul ul{margin:.25rem 0 0;padding-left:1.3rem;color:#aeb8c4}
.session-project-summary+.session-project-summary{margin-top:.7rem}
.session-project-summary h4{margin:0 0 .3rem;color:#8fb4dd;font-size:.88rem}
.detail{font-size:.88rem;margin-top:.4rem}footer{margin-top:2.5rem;color:var(--muted);
font-size:.9rem}a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
@media(max-width:850px){body{padding:1.5rem 1rem 3rem}.page-shell{display:block}
.sidebar{position:static;margin-bottom:1.25rem}.sidebar a{display:inline-block}
.nav-session{margin-top:0}.nav-project{padding-left:.4rem!important}
.current dl,.resume dl,.summary dl,
.system dl{grid-template-columns:1fr;gap:.1rem}.current dd,.summary dd,.system dd{
margin-bottom:.55rem}.resume dd{margin-bottom:.45rem}.event{
grid-template-columns:3.25rem 1fr;gap:.65rem}.content{
grid-column:2}.current,.resume,.summary,.system,.session{padding:1rem}}
</style></head><body>""",
        '<div class="page-shell">',
        '<nav class="sidebar" aria-label="Navigation de la timeline">',
        "<h2>Navigation</h2>",
        *navigation,
        "</nav>",
        "<main>",
        "<header>",
        (
            f"<h1>Journal du {escape(trace['date'])}</h1>"
            if archive_mode
            else f"<h1>Trace du {escape(trace['date'])}</h1>"
        ),
        (
            f'<div class="meta">{trace["activity_count"]} activité(s) · '
            f'{summary["session_count"]} session(s) de travail · '
            f'{summary["passive_activity_count"]} activité(s) passive(s)</div>'
        ),
        "</header>",
    ]
    if not archive_mode:
        body.extend(
            [
                '<section class="current" id="maintenant"><h2>Maintenant</h2><dl>',
                f"<dt>Projet probable</dt><dd>{escape(str(current['project']))}</dd>",
                f"<dt>Workspace</dt><dd>{escape(str(current['workspace']))}</dd>",
                f"<dt>App active</dt><dd>{escape(str(current['app']))}</dd>",
                f"<dt>Dernière commande</dt><dd>{escape(str(current['command']))}</dd>",
                f"<dt>Fichiers récents</dt><dd>{recent_files}</dd>",
                f"<dt>Session active depuis</dt><dd>{current['session_started_at']}</dd>",
                f"<dt>Dernière activité utile</dt><dd>{last_activity}</dd>",
                "</dl></section>",
            ]
        )
    if resume:
        resume_content = []
        for fact in resume:
            if isinstance(fact, tuple):
                group_label, rows = fact
                group_rows = []
                for label, value in rows:
                    if isinstance(value, list):
                        rendered_value = "<ul>" + "".join(
                            f"<li>{escape(item)}</li>" for item in value
                        ) + "</ul>"
                    else:
                        rendered_value = escape(value)
                    group_rows.extend(
                        [
                            f"<dt>{escape(label)}</dt>",
                            f"<dd>{rendered_value}</dd>",
                        ]
                    )
                resume_content.append(
                    '<div class="resume-git">'
                    f"<h3>{escape(group_label)}</h3>"
                    f"<dl>{''.join(group_rows)}</dl></div>"
                )
            else:
                label, value = fact.split(" : ", 1)
                resume_content.append(
                    f"<dl><dt>{escape(label)}</dt>"
                    f"<dd>{escape(value)}</dd></dl>"
                )
        body.append(
            '<section class="resume" id="reprise"><h2>Reprise</h2>'
            f"{''.join(resume_content)}</section>"
        )
    body.extend(
        [
        (
            '<section class="summary" id="resume-jour">'
            "<h2>Résumé du jour</h2><dl>"
            if archive_mode
            else '<section class="summary" id="aujourdhui"><h2>Aujourd’hui</h2><dl>'
        ),
        (
            "<dt>Sessions de travail</dt>"
            f"<dd>{summary['session_count']}</dd>"
        ),
        (
            "<dt>Activités passives</dt>"
            f"<dd>{summary['passive_activity_count']}</dd>"
        ),
        f"<dt>Événements</dt><dd>{summary['activity_count']}</dd>",
        f"<dt>Commandes terminal</dt><dd>{summary['terminal_count']}</dd>",
        f"<dt>Tests</dt><dd>{summary['test_count']}</dd>",
        f"<dt>Git</dt><dd>{summary['git_count']}</dd>",
        f"<dt>Erreurs</dt><dd>{summary['error_count']}</dd>",
        f"<dt>Fichiers modifiés</dt><dd>{summary['distinct_file_count']}</dd>",
        f"<dt>Projets</dt><dd>{', '.join(projects) if projects else 'Aucun'}</dd>",
        f"<dt>Apps principales</dt><dd>{', '.join(apps) if apps else 'Aucune'}</dd>",
        "</dl></section>",
        ]
    )

    if system_status and not archive_mode:
        database_exists = "oui" if system_status["database_exists"] else "non"
        workspace = system_status["primary_workspace"] or "Non détecté"
        body.extend(
            [
                '<section class="system" id="etat-systeme"><h2>État système</h2><dl>',
                f"<dt>Daemon</dt><dd>{escape(system_status['daemon'])}</dd>",
                (
                    "<dt>URL locale</dt><dd>"
                    f"<a href=\"{escape(system_status['url'])}\">"
                    f"{escape(system_status['url'])}</a></dd>"
                ),
                (
                    "<dt>Base SQLite</dt><dd>"
                    f"{escape(system_status['database_path'])}</dd>"
                ),
                f"<dt>Base existante</dt><dd>{database_exists}</dd>",
                f"<dt>Événements du jour</dt><dd>{system_status['event_count']}</dd>",
                (
                    "<dt>Sessions affichées</dt><dd>"
                    f"{system_status['displayed_session_count']}</dd>"
                ),
                f"<dt>Workspace principal</dt><dd>{escape(workspace)}</dd>",
                (
                    "<dt>Watcher terminal</dt><dd>"
                    f"{escape(system_status['terminal_watcher'])}</dd>"
                ),
                "</dl></section>",
            ]
        )

    if not displayed_sessions and not passive_sessions:
        body.append("<p>Aucune activité pour cette journée.</p>")

    current_day = datetime.now().astimezone().date().isoformat()
    for index, session in enumerate(displayed_sessions, start=1):
        observed_start, observed_end = _session_observed_bounds(session)
        started_at = _display_time(observed_start)
        ended_at = _display_time(observed_end)
        duration = _session_duration(session)
        in_progress = (
            " · en cours"
            if not archive_mode
            and trace["date"] == current_day
            and index == len(displayed_sessions)
            else ""
        )
        project_summaries = _session_project_summaries(
            session, project_workspaces
        )
        session_facts = (
            [] if project_summaries
            else build_session_summary(session, project_workspaces)
        )
        body.extend(
            [
                f'<section class="session" id="session-{index}">',
                f"<h2>Session {index} · {started_at}–{ended_at}"
                f" · {duration}{in_progress}</h2>",
            ]
        )
        if project_summaries or session_facts:
            if project_summaries:
                summary_html = "".join(
                    '<div class="session-project-summary">'
                    f"<h4>{escape(project)}</h4>"
                    + (_html_summary_facts(facts) if facts else "")
                    + "</div>"
                    for project, facts in project_summaries
                )
            else:
                summary_html = _html_summary_facts(session_facts)
            body.append(
                '<div class="session-summary"><h3>Résumé de session</h3>'
                f"{summary_html}</div>"
            )
        elif not _app_activation_counts(session):
            body.append(
                '<div class="session-summary"><h3>Résumé de session</h3>'
                "<p>Aucun signal significatif dans cette session.</p></div>"
            )
        body.append('<ul class="timeline">')
        file_change_groups = _file_change_groups(session)
        app_activation_counts = _app_activation_counts(session)
        rendered_app_activations = False
        rendered_project = None
        rendered_project_index = 0

        for activity in session["activities"]:
            details = activity.get("details", {})
            path = details.get("path")
            workspace = details.get("workspace")
            event = details.get("event", details.get("change"))
            display_type = activity["type"]
            terminal_labels = (
                _terminal_labels(activity)
                if activity["type"] == "terminal_finished"
                else []
            )
            duplicate_file = (
                activity["type"] == "file_changed"
                and bool(event and path)
                and id(activity) not in file_change_groups
            )
            activity_workspace = activity_project_root(activity)
            if (
                not duplicate_file
                and activity_workspace in project_workspaces
                and activity_workspace != rendered_project
            ):
                rendered_project = activity_workspace
                rendered_project_index += 1
                body.append(
                    f'<li class="project-separator" '
                    f'id="session-{index}-projet-{rendered_project_index}">'
                    f"{escape(resolve_project_context(activity_workspace).project_name)}</li>"
                )
            if activity["type"] == "app_activated":
                if details.get("app") not in app_activation_counts:
                    continue
                if rendered_app_activations:
                    continue
                rendered_app_activations = True
                apps = [
                    escape(str(app))
                    for app, _count in _ranked_apps(app_activation_counts)
                ]
                content = f"Apps actives : {', '.join(apps)}"
                display_type = "applications"
            elif activity["type"] == "file_changed" and event and path:
                if id(activity) not in file_change_groups:
                    continue
                group = file_change_groups[id(activity)]
                if len(group) > 1:
                    items = []
                    for item_path, item_event, item_workspace, count in group:
                        suffix = f" ×{count}" if count > 1 else ""
                        items.append(
                            f"<li>{escape(str(item_event).capitalize())} "
                            f"<code>{escape(_display_file_path(item_path, item_workspace))}"
                            f"</code>{suffix}</li>"
                        )
                    content = (
                        "Fichiers modifiés :"
                        f'<ul class="commands">{"".join(items)}</ul>'
                    )
                    workspace = None
                else:
                    count = group[0][3]
                    suffix = f" ×{count}" if count > 1 else ""
                    content = (
                        f"{escape(str(event).capitalize())} "
                        f"<code>{escape(_display_file_path(path, workspace))}</code>"
                        f"{suffix}"
                    )
            else:
                command = details.get("command")
                command_lines = (
                    [line.strip() for line in command.splitlines() if line.strip()]
                    if isinstance(command, str)
                    else []
                )
                if activity["type"] == "terminal_finished" and len(command_lines) > 1:
                    exit_code = details.get("exit_code")
                    status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
                    items = "".join(
                        f"<li><code>{escape(line)}</code></li>"
                        for line in command_lines
                    )
                    content = f"Command {status}:<ul class=\"commands\">{items}</ul>"
                elif activity["type"] == "terminal_finished" and command_lines:
                    exit_code = details.get("exit_code")
                    status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
                    content = (
                        f"Command {status}: "
                        f"<code>{escape(command_lines[0])}</code>"
                    )
                else:
                    content = escape(str(activity["summary"]))

            detail_lines = []
            project_context = activity_project_context(activity)
            if project_context and project_context.module:
                detail_lines.append(
                    f"Module : {escape(project_context.module)}"
                )
            if details.get("cwd"):
                detail_lines.append(f"CWD : {escape(str(details['cwd']))}")
            if workspace:
                detail_lines.append(f"Workspace : {escape(str(workspace))}")
            detail_html = "".join(
                f'<div class="detail">{line}</div>' for line in detail_lines
            )
            type_html = escape(display_type) + "".join(
                f' <span class="label label-{escape(label)}">{escape(label)}</span>'
                for label in terminal_labels
            )
            body.append(
                '<li class="event">'
                f'<time>{_display_time(activity["occurred_at"])}</time>'
                f'<span class="type">{type_html}</span>'
                f'<div class="content">{content}{detail_html}</div></li>'
            )
        body.extend(["</ul>", "</section>"])

    if passive_sessions:
        passive_items = []
        for session in passive_sessions:
            passive_started_at, _passive_ended_at = _session_observed_bounds(
                session
            )
            passive_apps = [
                escape(str(app))
                for app, _count in _ranked_apps(
                    _app_activation_counts(session)
                )
            ]
            passive_items.append(
                f"<li>{escape(_display_time(passive_started_at))} · "
                f"{', '.join(passive_apps)}</li>"
            )
        body.append(
            '<section class="summary" id="activite-passive">'
            "<h2>Activité passive</h2>"
            "<p>Ces signaux ont été observés mais ne sont pas considérés "
            "comme des sessions de travail.</p>"
            f"<ul>{''.join(passive_items)}</ul></section>"
        )

    body.extend(
        [
            f'<div id="{end_anchor}" aria-hidden="true"></div>',
            '<footer><a href="/days">Jours</a> · '
            f'<a href="{escape(trace_json_url)}">JSON</a> · '
            f'<a href="{escape(trace_markdown_url)}">Markdown</a></footer>',
            "</main></div></body></html>",
        ]
    )
    return "\n".join(body)


def render_available_days_html(
    available_days: dict[str, list[dict[str, Any]]],
) -> str:
    def render_summary_lines(lines: list[str], class_name: str) -> str:
        primary = (
            f'<p class="{class_name}-primary">{escape(lines[0])}</p>'
        )
        secondary = (
            f'<p class="{class_name}-secondary">{escape(lines[1])}</p>'
            if len(lines) > 1
            else ""
        )
        return primary + secondary

    day_cards = []
    for item in available_days["days"]:
        day = escape(item["date"])
        event_count = item["event_count"]
        session_count = item["session_count"]
        project_count = len(item["projects"])
        if item["project_summaries"]:
            project_blocks = "".join(
                '<section class="day-project-summary">'
                f"<h3>{escape(project['project'])}</h3>"
                + render_summary_lines(
                    project["summary"], "day-project-summary"
                )
                + "</section>"
                for project in item["project_summaries"]
            )
            summary = (
                '<div class="day-project-summaries">'
                f"{project_blocks}</div>"
            )
        else:
            summary = (
                '<div class="day-summary">'
                + render_summary_lines(item["summary"], "day-summary")
                + "</div>"
            )
        day_cards.append(
            '<article class="day">'
            f"<h2>{day}</h2>"
            f"<p>{event_count} événement{'s' if event_count != 1 else ''} · "
            f"{session_count} session{'s' if session_count != 1 else ''}</p>"
            f'<p class="day-project-count">{project_count} '
            f"{'Projet' if project_count == 1 else 'Projets'}</p>"
            f"{summary}"
            f'<nav><a href="/day/{day}">HTML</a> · '
            f'<a href="/trace/{day}">JSON</a> · '
            f'<a href="/trace/{day}.md">Markdown</a></nav>'
            "</article>"
        )
    content = "".join(day_cards) or "<p>Aucun jour disponible.</p>"
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="fr"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Pulse — Jours disponibles</title>",
            """<style>
:root{color-scheme:dark;--bg:#11151a;--panel:#191f26;--border:#2a333d;
--text:#d7dee7;--muted:#8f9aaa;--link:#83a9d8}
*{box-sizing:border-box}body{font:16px/1.6 system-ui,sans-serif;max-width:760px;
margin:0 auto;padding:2.5rem 1.5rem 4rem;background:var(--bg);color:var(--text)}
header{margin-bottom:1.5rem}h1{margin:0 0 .2rem;font-size:2rem}header p,.day p{
color:var(--muted);margin:.2rem 0}.days{display:grid;gap:1rem}.day{background:var(--panel);
border:1px solid var(--border);border-radius:12px;padding:1.1rem 1.3rem}
.day .day-project-count{margin-top:.35rem;color:#aab5c1}
.day-summary{max-width:62ch;margin:.8rem 0 .7rem;padding-left:.8rem;
border-left:2px solid #3a4652}.day .day-summary-primary{color:var(--text);margin:0;
line-height:1.45}.day .day-summary-secondary{color:#9da8b5;margin:.3rem 0 0;
font-size:.92rem;line-height:1.4}
.day-project-summaries{max-width:62ch;margin:.8rem 0 .7rem;display:grid;gap:.75rem}
.day-project-summary{padding-left:.8rem;border-left:2px solid #3a4652}
.day-project-summary h3{margin:0 0 .15rem;color:#dce4ed;font-size:.98rem}
.day .day-project-summary-primary{color:var(--text);margin:0;line-height:1.45}
.day .day-project-summary-secondary{color:#9da8b5;margin:.3rem 0 0;
font-size:.92rem;line-height:1.4}
.day h2{margin:0 0 .35rem;font-size:1.15rem;color:#e4eaf1}.day nav{margin-top:.65rem}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
footer{margin-top:2rem;color:var(--muted)}
</style></head><body>""",
            "<header><h1>Jours récents</h1>"
            "<p>Traces disponibles du plus récent au plus ancien.</p></header>",
            f'<main class="days">{content}</main>',
            '<footer><a href="/">Aujourd’hui</a> · '
            '<a href="/trace/days">JSON</a></footer>',
            "</body></html>",
        ]
    )
