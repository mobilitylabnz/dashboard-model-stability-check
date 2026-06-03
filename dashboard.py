"""
Aimsun Diagnostics Dashboard
Run with:  streamlit run dashboard.py
"""

import json
import math
import pathlib
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Aimsun Diagnostics",
    page_icon="🚦",
    layout="wide",
)

_BASE_DT = datetime(2000, 1, 1)   # reference date for time-axis conversion
PLOTLY_PALETTE = px.colors.qualitative.Plotly

# VALID_TABLES = ["MISUBPATH", "MISYS", "MISECT", "MILANE", "MINODE", "MITURN", "MIDETEC"]
VALID_TABLES = ["MISUBPATH", "MISYS"]

# Load OID labels from oid_labels.json in the project folder (keys are strings in JSON).
# If the file is missing the dashboard still works — OIDs just show as raw numbers.
_label_file = pathlib.Path(__file__).parent / "oid_labels.json"
if _label_file.exists():
    DEFAULT_OID_LABELS = {int(k): v for k, v in json.loads(_label_file.read_text()).items()}
else:
    DEFAULT_OID_LABELS = {}

MEAN_COLOR = "#e67e22"
AIMSUN_COLOR = "#27ae60"
FLAG_COLOR = "#e74c3c"


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_hhmm(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    return f"{h:02d}:{r // 60:02d}"


def oid_label(oid: int, eid: str, label_map: dict) -> str:
    if oid in label_map:
        return label_map[oid]
    return f"{eid}  [{oid}]" if pd.notna(eid) else str(oid)


def compute_outliers(
    df: pd.DataFrame, metric: str, threshold_pct: float, label_map: dict, flag_mode: str = "Either"
) -> tuple[pd.DataFrame, dict]:
    """
    For each (experiment, eid, oid) group compute per-replication MAPE and max deviation
    against the cross-rep mean.  Returns:
      - summary DataFrame of flagged (rep, group) pairs for display
      - flagged_by_group dict: {group_key: set(rep_ids)} for per-subplot highlighting
    Flagging is per-group so a rep is only marked as an outlier in the specific
    subplot where it deviates, not globally across all routes.
    """
    if df.empty or metric not in df.columns:
        return pd.DataFrame(), {}

    work = df.copy()
    work["eid"] = work["eid"].fillna("(none)")
    work = work.dropna(subset=[metric])

    has_intervals = (work["ent"] > 0).any()
    if has_intervals:
        work = work[work["ent"] > 0].copy()
        work["_time_s"] = work["from_time"] + (work["ent"] - 1) * 900

    oid_equals_did = (work["oid"] == work["replication_id"]).all()
    group_cols = ["experiment", "eid"] if oid_equals_did else ["experiment", "eid", "oid"]

    records = []
    flagged_by_group: dict = {}

    for group_key, grp in work.groupby(group_cols):
        key = group_key if isinstance(group_key, tuple) else (group_key,)
        flagged_in_group: set = set()

        if has_intervals:
            mean_profile = grp.groupby("_time_s")[metric].mean()
            for rep_id, rep_grp in grp.groupby("replication_id"):
                rep_vals = rep_grp.set_index("_time_s")[metric]
                aligned_mean = mean_profile.reindex(rep_vals.index)
                nonzero = aligned_mean != 0
                if nonzero.sum() == 0:
                    continue
                abs_pct = (
                    (rep_vals[nonzero] - aligned_mean[nonzero]).abs()
                    / aligned_mean[nonzero].abs()
                    * 100
                )
                mape = abs_pct.mean()
                max_dev = abs_pct.max()
                flagged = (
                    (flag_mode == "MAPE"    and mape > threshold_pct) or
                    (flag_mode == "Max dev" and max_dev > threshold_pct) or
                    (flag_mode == "Either"  and (mape > threshold_pct or max_dev > threshold_pct))
                )
                if flagged:
                    flagged_in_group.add(rep_id)
                    records.append(
                        {**dict(zip(group_cols, key)),
                         "replication_id": rep_id,
                         "mape_pct": round(mape, 1),
                         "max_dev_pct": round(max_dev, 1)}
                    )
        else:
            mean_val = grp[metric].mean()
            if mean_val == 0:
                continue
            for rep_id, rep_grp in grp.groupby("replication_id"):
                val = rep_grp[metric].iloc[0]
                dev = abs(val - mean_val) / abs(mean_val) * 100
                if dev > threshold_pct:
                    flagged_in_group.add(rep_id)
                    records.append(
                        {**dict(zip(group_cols, key)),
                         "replication_id": rep_id,
                         "mape_pct": round(dev, 1),
                         "max_dev_pct": round(dev, 1)}
                    )

        if flagged_in_group:
            flagged_by_group[key] = flagged_in_group

    if not records:
        return pd.DataFrame(), flagged_by_group

    result = pd.DataFrame(records)
    if "oid" in result.columns:
        result["oid_label"] = result["oid"].map(lambda o: label_map.get(int(o), str(o)))
    else:
        result["oid_label"] = result.get("eid", "")

    return result.sort_values("mape_pct", ascending=False).reset_index(drop=True), flagged_by_group


@st.cache_data(show_spinner="Loading replications list…")
def load_replication_list(db_path: str) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT did, didname, type,
               xname  AS experiment,
               scname AS scenario,
               seed, twhen AS date,
               from_time, duration, exec_date
        FROM SIM_INFO
        ORDER BY xname, did
        """,
        con,
    )
    con.close()
    df["start_time"] = df["from_time"].apply(fmt_hhmm)
    df["duration_hr"] = (df["duration"] / 3600).round(1).astype(str) + "h"
    return df


@st.cache_data(show_spinner="Extracting table…")
def extract_table(
    db_path: str,
    table: str,
    rep_type: int,
    experiment_filter: tuple,
    eid_filter: tuple,
    sid_filter: tuple,
    summary_only: bool,
) -> pd.DataFrame:
    con = sqlite3.connect(db_path)

    cur = con.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    raw_cols = [r[1] for r in cur.fetchall()]

    key_cols = {"did", "oid", "eid", "sid", "ent"}
    metric_cols = [c for c in raw_cols if c not in key_cols and not c.endswith("_D")]
    has_travel = "travel" in raw_cols and "count" in raw_cols

    dids = pd.read_sql(
        f"SELECT did FROM SIM_INFO WHERE type={rep_type}", con
    )["did"].tolist()
    if not dids:
        con.close()
        return pd.DataFrame()

    conditions = [f"t.did IN ({', '.join('?' * len(dids))})"]
    params: list = list(dids)

    if experiment_filter:
        conditions.append(
            f"s.xname IN ({', '.join('?' * len(experiment_filter))})"
        )
        params.extend(experiment_filter)
    if eid_filter:
        conditions.append(
            f"t.eid IN ({', '.join('?' * len(eid_filter))})"
        )
        params.extend(eid_filter)
    if sid_filter:
        conditions.append(
            f"t.sid IN ({', '.join('?' * len(sid_filter))})"
        )
        params.extend(sid_filter)
    if summary_only:
        conditions.append("t.ent = 0")

    where = " AND ".join(conditions)
    metric_select = ",\n        ".join(f"t.{c}" for c in metric_cols)
    distance_expr = (
        ",\n        CASE WHEN t.count > 0 THEN t.travel / t.count ELSE NULL END AS distance_km"
        if has_travel
        else ""
    )

    query = f"""
        SELECT
            s.did      AS replication_id,
            s.didname  AS replication_name,
            s.xname    AS experiment,
            s.scname   AS scenario,
            s.seed,
            s.twhen    AS date,
            s.from_time,
            s.duration,
            t.oid,
            t.eid,
            t.sid,
            t.ent,
            {metric_select}{distance_expr}
        FROM {table} t
        JOIN SIM_INFO s ON t.did = s.did
        WHERE {where}
    """

    df = pd.read_sql(query, con, params=params)
    con.close()

    df.replace(-1.0, np.nan, inplace=True)
    for col in ["ttime", "dtime", "stime", "traveltime", "approachDelay", "link_ttime", "link_dtime"]:
        if col in df.columns:
            df[f"{col}_min"] = (df[col] / 60).round(3)

    return df


def make_figures(
    df: pd.DataFrame,
    avg_df: pd.DataFrame,
    metric: str,
    label_map: dict,
    eid_filter: tuple,
    flagged_by_group: dict | None = None,
) -> list[go.Figure]:
    if df.empty or metric not in df.columns:
        return []

    plot_df = df.copy()
    plot_df["eid"] = plot_df["eid"].fillna("(none)")
    plot_df = plot_df.dropna(subset=[metric])
    if plot_df.empty:
        return []

    has_intervals = (plot_df["ent"] > 0).any()
    if has_intervals:
        plot_df = plot_df[plot_df["ent"] > 0].copy()
        plot_df["_time_s"] = plot_df["from_time"] + (plot_df["ent"] - 1) * 900

    oid_equals_did = (plot_df["oid"] == plot_df["replication_id"]).all()
    group_cols = ["experiment", "eid"] if oid_equals_did else ["experiment", "eid", "oid"]

    all_rep_ids = sorted(plot_df["replication_id"].unique())
    rep_color = {rid: PLOTLY_PALETTE[i % len(PLOTLY_PALETTE)] for i, rid in enumerate(all_rep_ids)}

    avg_base = pd.DataFrame()
    if not avg_df.empty and metric in avg_df.columns:
        avg_base = avg_df.copy()
        avg_base["eid"] = avg_base["eid"].fillna("(none)")

    figures = []

    for group_key, grp in plot_df.groupby(group_cols, sort=True):
        key = group_key if isinstance(group_key, tuple) else (group_key,)
        exp = key[0]
        eid = key[1]
        oid = key[2] if not oid_equals_did else None

        if oid_equals_did:
            title = eid if eid != "(none)" else exp
        else:
            title = oid_label(oid, eid, label_map)

        rep_ids = sorted(grp["replication_id"].unique())
        flagged = (flagged_by_group or {}).get(key, set())

        fig = go.Figure()

        if has_intervals:
            for rep_id in rep_ids:
                rep_data = grp[grp["replication_id"] == rep_id].sort_values("_time_s")
                is_flagged = rep_id in flagged
                x_vals = [_BASE_DT + timedelta(seconds=int(t)) for t in rep_data["_time_s"]]
                color = FLAG_COLOR if is_flagged else rep_color[rep_id]

                fig.add_trace(go.Scatter(
                    x=x_vals,
                    y=rep_data[metric].values,
                    mode="lines+markers",
                    name=f"⚠ {rep_id}" if is_flagged else str(rep_id),
                    line=dict(color=color, width=2.5 if is_flagged else 1.2,
                              dash="dot" if is_flagged else "solid"),
                    marker=dict(symbol="diamond" if is_flagged else "circle",
                                size=7 if is_flagged else 4, color=color),
                    opacity=1.0 if is_flagged else 0.7,
                    hovertemplate=(
                        f"<b>{'⚠ ' if is_flagged else ''}Rep {rep_id}</b><br>"
                        f"Time: %{{x|%H:%M}}<br>{metric}: %{{y:.3f}}<extra></extra>"
                    ),
                ))

            # Cross-rep mean
            mean_s = grp.groupby("_time_s")[metric].mean().reset_index().sort_values("_time_s")
            fig.add_trace(go.Scatter(
                x=[_BASE_DT + timedelta(seconds=int(t)) for t in mean_s["_time_s"]],
                y=mean_s[metric].values,
                mode="lines", name="Calc. Mean",
                line=dict(color=MEAN_COLOR, width=2.5, dash="dash"),
                hovertemplate=f"<b>Calc. Mean</b><br>Time: %{{x|%H:%M}}<br>{metric}: %{{y:.3f}}<extra></extra>",
            ))

            # Aimsun avg
            if not avg_base.empty:
                _avg = avg_base[avg_base["experiment"] == exp]
                if eid_filter:
                    _avg = _avg[_avg["eid"].isin(eid_filter)]
                if not oid_equals_did:
                    _avg = _avg[_avg["oid"] == oid]
                _avg = _avg[_avg["ent"] > 0].copy()
                if not _avg.empty:
                    _avg["_time_s"] = _avg["from_time"] + (_avg["ent"] - 1) * 900
                    _avg = _avg.sort_values("_time_s")
                    fig.add_trace(go.Scatter(
                        x=[_BASE_DT + timedelta(seconds=int(t)) for t in _avg["_time_s"]],
                        y=_avg[metric].values,
                        mode="lines", name="Aimsun Avg",
                        line=dict(color=AIMSUN_COLOR, width=2.5),
                        hovertemplate=f"<b>Aimsun Avg</b><br>Time: %{{x|%H:%M}}<br>{metric}: %{{y:.3f}}<extra></extra>",
                    ))

            fig.update_xaxes(tickformat="%H:%M", tickangle=45, dtick=900000)

        else:
            for rep_id in rep_ids:
                rep_data = grp[grp["replication_id"] == rep_id]
                y_val = float(rep_data[metric].values[0]) if not rep_data.empty else float("nan")
                is_flagged = rep_id in flagged
                color = FLAG_COLOR if is_flagged else rep_color[rep_id]

                fig.add_trace(go.Bar(
                    x=[str(rep_id)], y=[y_val],
                    name=f"⚠ {rep_id}" if is_flagged else str(rep_id),
                    marker=dict(color=color, opacity=1.0 if is_flagged else 0.8,
                                line=dict(color="black" if is_flagged else "rgba(0,0,0,0)",
                                          width=1.5 if is_flagged else 0)),
                    hovertemplate=f"<b>{'⚠ ' if is_flagged else ''}Rep {rep_id}</b><br>{metric}: %{{y:.3f}}<extra></extra>",
                ))

            x_labels = [str(r) for r in rep_ids]
            mean_val = grp[metric].mean()
            fig.add_trace(go.Scatter(
                x=x_labels, y=[mean_val] * len(x_labels),
                mode="lines", name="Calc. Mean",
                line=dict(color=MEAN_COLOR, width=2, dash="dash"),
                hovertemplate=f"<b>Calc. Mean</b><br>{metric}: {mean_val:.3f}<extra></extra>",
            ))

            if not avg_base.empty:
                _avg = avg_base[avg_base["experiment"] == exp]
                if eid_filter:
                    _avg = _avg[_avg["eid"].isin(eid_filter)]
                if not oid_equals_did:
                    _avg = _avg[_avg["oid"] == oid]
                _avg = _avg[_avg["ent"] == 0]
                if not _avg.empty:
                    aimsun_val = _avg[metric].dropna().mean()
                    if not pd.isna(aimsun_val):
                        fig.add_trace(go.Scatter(
                            x=x_labels, y=[aimsun_val] * len(x_labels),
                            mode="lines", name="Aimsun Avg",
                            line=dict(color=AIMSUN_COLOR, width=2),
                            hovertemplate=f"<b>Aimsun Avg</b><br>{metric}: {aimsun_val:.3f}<extra></extra>",
                        ))

        fig.update_yaxes(title_text=metric, title_font_size=11)
        fig.update_layout(
            title_text=title,
            title_font_size=13,
            height=400,
            hovermode="closest",
            template="plotly_white",
            legend=dict(font_size=11),
            margin=dict(t=50, b=40, l=60, r=20),
        )
        figures.append(fig)

    return figures


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.title("🚦 Aimsun Diagnostics Dashboard")

with st.sidebar:
    st.header("Data source")
    db_path = st.text_input(
        "SQLite database path",
        value=r"C:\Users\IvanVelilla\Mobility Lab Limited\Projects - 1034 - Western LX Aimsun\New North Road\Model\v10\Resources\Outputs\ADTA_NNR_WesternLX_v10.sqlite",
    )

    if not db_path:
        st.info("Enter a database path to continue.")
        st.stop()

    try:
        rep_list = load_replication_list(db_path)
    except Exception as e:
        st.error(f"Cannot open database: {e}")
        st.stop()

    st.success(f"{len(rep_list[rep_list['type']==1])} replication(s) found")

    st.header("Filters")
    table = st.selectbox("Table", VALID_TABLES, index=0)

    all_experiments = sorted(rep_list[rep_list["type"] == 1]["experiment"].unique())
    experiments = st.multiselect("Experiments", all_experiments, default=all_experiments)

    summary_only = st.checkbox("Summary only (ent=0)", value=False)

    st.header("Outlier flagging")
    flag_threshold = st.slider("Flag threshold (%)", min_value=1, max_value=50, value=10, step=1)
    flag_mode = st.radio(
        "Flag using",
        options=["MAPE", "Max dev", "Either"],
        index=2,
        help="MAPE = average deviation across all intervals. Max dev = worst single interval. Either = flag if either exceeds the threshold.",
    )

# ── Replications overview ─────────────────────────────────────────────────────
st.subheader("Replications")
show_cols = ["did", "experiment", "scenario", "seed", "date", "start_time", "duration_hr", "exec_date"]
type1 = rep_list[rep_list["type"] == 1][show_cols].reset_index(drop=True)
st.dataframe(type1, use_container_width=True)

# ── Extract data ──────────────────────────────────────────────────────────────
exp_tuple = tuple(experiments)
if not exp_tuple:
    st.warning("Select at least one experiment.")
    st.stop()

with st.spinner(f"Loading {table}…"):
    try:
        df = extract_table(db_path, table, 1, exp_tuple, (), (0,), summary_only)
        avg_df = extract_table(db_path, table, 2, exp_tuple, (), (0,), summary_only)
    except Exception as e:
        st.error(f"Extraction failed: {e}")
        st.stop()

if df.empty:
    st.warning("No data returned for the selected filters.")
    st.stop()

# ── Dynamic filters from extracted data ──────────────────────────────────────
col1, col2, col3 = st.columns(3)

all_eids = sorted(df["eid"].dropna().unique())
with col1:
    selected_eids = st.multiselect("EID (subpath group / object label)", all_eids, default=all_eids[:1] if all_eids else [])

numeric_cols = [c for c in df.columns if df[c].dtype in ["float64", "int64"] and c not in {"replication_id", "seed", "from_time", "duration", "oid", "sid", "ent"}]
default_metric = "ttime_min" if "ttime_min" in numeric_cols else (numeric_cols[0] if numeric_cols else None)
with col2:
    metric = st.selectbox("Metric", numeric_cols, index=numeric_cols.index(default_metric) if default_metric else 0)

all_oids = sorted(df["oid"].unique())
oid_options = {oid_label(o, "", DEFAULT_OID_LABELS): o for o in all_oids}
with col3:
    selected_oid_labels = st.multiselect(
        "OIDs (leave blank = all)",
        list(oid_options.keys()),
        default=[],
    )

# ── Apply OID + EID filters to plot data ─────────────────────────────────────
plot_df = df.copy()
plot_avg_df = avg_df.copy() if not avg_df.empty else pd.DataFrame()

if selected_eids:
    plot_df = plot_df[plot_df["eid"].isin(selected_eids)]
    if not plot_avg_df.empty:
        plot_avg_df = plot_avg_df[plot_avg_df["eid"].isin(selected_eids)]

if selected_oid_labels:
    selected_oids = [oid_options[lbl] for lbl in selected_oid_labels]
    plot_df = plot_df[plot_df["oid"].isin(selected_oids)]
    if not plot_avg_df.empty:
        plot_avg_df = plot_avg_df[plot_avg_df["oid"].isin(selected_oids)]

# ── Outlier flagging ──────────────────────────────────────────────────────────
st.subheader(f"{table} · {metric}")

outlier_df, flagged_by_group = compute_outliers(plot_df, metric, flag_threshold, DEFAULT_OID_LABELS, flag_mode)

if not outlier_df.empty:
    n_flagged_reps = outlier_df["replication_id"].nunique()
    n_flagged_routes = outlier_df["oid_label"].nunique() if "oid_label" in outlier_df.columns else "—"
    st.error(f"⚠ {n_flagged_reps} replication(s) flagged on {n_flagged_routes} route(s) above {flag_threshold}% threshold")
    show_outlier_cols = [c for c in ["replication_id", "experiment", "oid_label", "eid", "mape_pct", "max_dev_pct"] if c in outlier_df.columns]
    st.dataframe(
        outlier_df[show_outlier_cols].rename(columns={"mape_pct": "MAPE %", "max_dev_pct": "Max dev %", "oid_label": "Route / Object"}),
        use_container_width=True,
    )
else:
    st.success(f"✓ No replications exceed the {flag_threshold}% deviation threshold")

# ── Plots ─────────────────────────────────────────────────────────────────────
if plot_df.empty:
    st.warning("No data after applying filters.")
else:
    # Split by experiment, render each group as its own figure in a 2-column grid
    for exp in sorted(plot_df["experiment"].unique()):
        st.markdown(f"**{exp}**")
        exp_df = plot_df[plot_df["experiment"] == exp]
        exp_avg_df = plot_avg_df[plot_avg_df["experiment"] == exp] if not plot_avg_df.empty else pd.DataFrame()

        figs = make_figures(exp_df, exp_avg_df, metric, DEFAULT_OID_LABELS, tuple(selected_eids), flagged_by_group)
        if not figs:
            st.info(f"No plottable data for {exp}.")
            continue

        for i in range(0, len(figs), 2):
            cols = st.columns(2)
            for j in range(2):
                if i + j < len(figs):
                    cols[j].plotly_chart(figs[i + j], use_container_width=True)

# ── Raw data table ────────────────────────────────────────────────────────────
with st.expander("Raw data"):
    display_df = plot_df.copy()
    if "oid" in display_df.columns:
        display_df["oid_label"] = display_df["oid"].map(
            lambda o: DEFAULT_OID_LABELS.get(o, str(o))
        )
    st.dataframe(display_df, use_container_width=True)
    st.caption(f"{len(display_df):,} rows")
