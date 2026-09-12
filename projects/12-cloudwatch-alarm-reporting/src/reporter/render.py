"""Rendering for the alarm report. No AWS imports here so it is trivially testable."""

from __future__ import annotations

import html
from typing import Any

STATE_COLOURS = {
    "ALARM": "#b42318",
    "OK": "#067647",
    "INSUFFICIENT_DATA": "#b54708",
}


def render_html(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    parts: list[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>CloudWatch alarm report</title>",
        "<style>",
        "body{font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:#1f2328;"
        "background:#fff;margin:0;padding:24px;}",
        "h1{font-size:20px;margin:0 0 4px;} h2{font-size:15px;margin:28px 0 8px;}",
        ".meta{color:#57606a;font-size:12px;margin-bottom:20px;}",
        ".tiles{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px;}",
        ".tile{border:1px solid #d0d7de;border-radius:8px;padding:12px 16px;min-width:110px;}",
        ".tile .n{font-size:24px;font-weight:600;line-height:1.1;}",
        ".tile .l{font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:#57606a;}",
        "table{border-collapse:collapse;width:100%;margin-top:6px;font-size:13px;}",
        "th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #eaeef2;vertical-align:top;}",
        "th{font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:#57606a;}",
        "td.state{font-weight:600;white-space:nowrap;}",
        ".empty{color:#57606a;font-style:italic;padding:8px 0;}",
        "</style></head><body>",
        f"<h1>CloudWatch alarm report</h1>",
        f"<div class='meta'>{html.escape(summary.get('accountLabel',''))} · "
        f"window {html.escape(summary['windowStart'])} → {html.escape(summary['windowEnd'])} "
        f"({summary['windowHours']}h)</div>",
        "<div class='tiles'>",
    ]

    for label, key in (
        ("In alarm", "ALARM"),
        ("OK", "OK"),
        ("No data", "INSUFFICIENT_DATA"),
        ("Total", "total"),
    ):
        colour = STATE_COLOURS.get(key, "#1f2328")
        parts.append(
            f"<div class='tile'><div class='n' style='color:{colour}'>{counts[key]}</div>"
            f"<div class='l'>{label}</div></div>"
        )
    parts.append("</div>")

    parts.append("<h2>Currently firing</h2>")
    parts.append(
        _table(
            ["Alarm", "Since", "Reason"],
            [
                [a["name"], a.get("updatedAt", ""), _clip(a.get("reason", ""), 160)]
                for a in summary["firing"]
            ],
            empty="Nothing is firing right now.",
        )
    )

    parts.append("<h2>Noisiest alarms in the window</h2>")
    parts.append(
        _table(
            ["Alarm", "Transitions", "Entries into ALARM", "Last state", "Last change"],
            [
                [
                    n["name"],
                    str(n["transitions"]),
                    str(n["toAlarm"]),
                    n.get("lastState", ""),
                    n.get("lastChangedAt", ""),
                ]
                for n in summary["noisiest"]
            ],
            empty="No state changes recorded in this window.",
        )
    )

    parts.append("<h2>All transitions</h2>")
    parts.append(
        _table(
            ["When", "Alarm", "From", "To", "Reason"],
            [
                [
                    str(t.get("changedAt", "")),
                    str(t.get("alarmName", "")),
                    str(t.get("previousState", "")),
                    str(t.get("state", "")),
                    _clip(str(t.get("reason", "")), 120),
                ]
                for t in summary["transitions"]
            ],
            empty="No transitions.",
            state_column=3,
        )
    )

    parts.append("<h2>Worth fixing</h2>")
    parts.append(
        _table(
            ["Alarm", "Issue"],
            [[a["name"], "No data for the whole window"] for a in summary["stale"]]
            + [[a["name"], "No alarm actions configured"] for a in summary["unactioned"]],
            empty="Every alarm has data and actions.",
        )
    )

    parts.append("</body></html>")
    return "".join(parts)


def render_csv_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = [["section", "alarm", "field1", "field2", "field3"]]

    for alarm in summary["firing"]:
        rows.append(["firing", alarm["name"], alarm.get("updatedAt", ""), alarm.get("state", ""), alarm.get("reason", "")])

    for item in summary["noisiest"]:
        rows.append(["noisiest", item["name"], str(item["transitions"]), str(item["toAlarm"]), item.get("lastState", "")])

    for transition in summary["transitions"]:
        rows.append(
            [
                "transition",
                str(transition.get("alarmName", "")),
                str(transition.get("changedAt", "")),
                f"{transition.get('previousState','')}->{transition.get('state','')}",
                str(transition.get("reason", "")),
            ]
        )

    for alarm in summary["stale"]:
        rows.append(["stale", alarm["name"], "INSUFFICIENT_DATA", "", ""])

    for alarm in summary["unactioned"]:
        rows.append(["unactioned", alarm["name"], "no actions", "", ""])

    return rows


def _table(headers: list[str], rows: list[list[str]], empty: str, state_column: int | None = None) -> str:
    if not rows:
        return f"<div class='empty'>{html.escape(empty)}</div>"

    out = ["<table><thead><tr>"]
    out += [f"<th>{html.escape(h)}</th>" for h in headers]
    out.append("</tr></thead><tbody>")

    for row in rows:
        out.append("<tr>")
        for index, cell in enumerate(row):
            text = html.escape(str(cell))
            if state_column is not None and index == state_column:
                colour = STATE_COLOURS.get(str(cell), "#1f2328")
                out.append(f"<td class='state' style='color:{colour}'>{text}</td>")
            else:
                out.append(f"<td>{text}</td>")
        out.append("</tr>")

    out.append("</tbody></table>")
    return "".join(out)


def _clip(value: str, limit: int) -> str:
    value = value.replace("\n", " ").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"
