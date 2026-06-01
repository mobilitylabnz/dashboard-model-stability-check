"""
Aimsun Diagnostics Dashboard
Run with:  streamlit run dashboard.py
"""

import math
import sqlite3

import matplotlib.cm as cm
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Aimsun Diagnostics",
    page_icon="🚦",
    layout="wide",
)

plt.rcParams.update(
    {
        "font.family": "Arial",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "figure.dpi": 110,
    }
)

VALID_TABLES = ["MISUBPATH", "MISYS", "MISECT", "MILANE", "MINODE", "MITURN", "MIDETEC"]

DEFAULT_OID_LABELS = {
    89336741: "Blockhouse Bay Rd NB",
    89336739: "New North Road NB",
    89336740: "New North Road SB",
    89336752: "Morningside NB",
    89336753: "Morningside SB",
    89336746: "Richardson Road EB",
    89336744: "Richardson Road WB",
    89361021: "Saint Georges Road NB",
    89361022: "Saint Georges Road SB",
    89336754: "Sandringham NB",
    89336755: "Sandringham SB",
    89336747: "Mount Albert Rd EB",
    89336749: "Mount Albert Rd WB",
    89336750: "St Lukes Rd EB",
    89336751: "St Lukes Rd WB",
    89362303: "Great North Road EB",
    89362304: "Great North Road WB",
}

MEAN_COLOR = "#e67e22"
AIMSUN_COLOR = "#27ae60"


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_hhmm(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    return f"{h:02d}:{r // 60:02d}"


def oid_label(oid: int, eid: str, label_map: dict) -> str:
    if oid in label_map:
        return label_map[oid]
    return f"{eid}  [{oid}]" if pd.notna(eid) else str(oid)


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


def make_figure(
    df: pd.DataFrame,
    avg_df: pd.DataFrame,
    metric: str,
    label_map: dict,
    eid_filter: tuple,
) -> plt.Figure | None:
    if df.empty or metric not in df.columns:
        return None

    plot_df = df.copy()
    plot_df["eid"] = plot_df["eid"].fillna("(none)")
    plot_df = plot_df.dropna(subset=[metric])
    if plot_df.empty:
        return None

    has_intervals = (plot_df["ent"] > 0).any()
    if has_intervals:
        plot_df = plot_df[plot_df["ent"] > 0].copy()
        plot_df["_time_s"] = plot_df["from_time"] + (plot_df["ent"] - 1) * 900

    oid_equals_did = (plot_df["oid"] == plot_df["replication_id"]).all()
    group_cols = ["experiment", "eid"] if oid_equals_did else ["experiment", "eid", "oid"]

    all_rep_ids = sorted(plot_df["replication_id"].unique())
    n_reps = len(all_rep_ids)
    palette = cm.tab10.colors if n_reps <= 10 else cm.tab20.colors
    rep_color = {rid: palette[i % len(palette)] for i, rid in enumerate(all_rep_ids)}

    groups = list(plot_df.groupby(group_cols, sort=True))
    n_plots = len(groups)
    if n_plots == 0:
        return None

    n_cols = min(n_plots, 3)
    n_rows = math.ceil(n_plots / n_cols)
    fig, axes = plt.subplots(
        n_rows, n_cols, figsize=(6 * n_cols, 4 * n_rows), squeeze=False
    )
    axes_flat = [axes[r][c] for r in range(n_rows) for c in range(n_cols)]

    avg_base = pd.DataFrame()
    if not avg_df.empty and metric in avg_df.columns:
        avg_base = avg_df.copy()
        avg_base["eid"] = avg_base["eid"].fillna("(none)")

    for ax_i, (group_key, grp) in enumerate(groups):
        ax = axes_flat[ax_i]
        rep_ids = sorted(grp["replication_id"].unique())

        if oid_equals_did:
            exp, eid = group_key
            subplot_title = f"{exp}\n{eid}"
        else:
            exp, eid, oid = group_key
            subplot_title = f"{exp}\n{oid_label(oid, eid, label_map)}"

        if has_intervals:
            for rep_id in rep_ids:
                rep_data = grp[grp["replication_id"] == rep_id].sort_values("_time_s")
                ax.plot(
                    rep_data["_time_s"].values,
                    rep_data[metric].values,
                    color=rep_color[rep_id],
                    linewidth=1.2,
                    alpha=0.75,
                    marker="o",
                    markersize=3,
                    label=str(rep_id),
                )

            mean_series = (
                grp.groupby("_time_s")[metric].mean().reset_index().sort_values("_time_s")
            )
            ax.plot(
                mean_series["_time_s"],
                mean_series[metric],
                color=MEAN_COLOR,
                linewidth=2.2,
                linestyle="--",
                zorder=5,
                label="Calc. Mean",
            )

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
                    ax.plot(
                        _avg["_time_s"],
                        _avg[metric],
                        color=AIMSUN_COLOR,
                        linewidth=2.4,
                        linestyle="-",
                        zorder=6,
                        label="Aimsun Avg",
                    )

            ax.xaxis.set_major_locator(mticker.MultipleLocator(1800))
            ax.xaxis.set_major_formatter(
                mticker.FuncFormatter(lambda v, _: fmt_hhmm(v))
            )
            ax.tick_params(axis="x", labelrotation=45)
            ax.set_xlabel("Time", fontsize=8)

        else:
            for i, rep_id in enumerate(rep_ids):
                rep_data = grp[grp["replication_id"] == rep_id]
                y_val = rep_data[metric].values[0] if not rep_data.empty else float("nan")
                ax.bar(i, y_val, color=rep_color[rep_id], alpha=0.8, width=0.6, label=str(rep_id))

            mean_val = grp[metric].mean()
            ax.axhline(
                mean_val,
                color=MEAN_COLOR,
                linewidth=1.8,
                linestyle="--",
                zorder=5,
                label=f"Mean: {mean_val:.3f}",
            )

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
                        ax.axhline(
                            aimsun_val,
                            color=AIMSUN_COLOR,
                            linewidth=1.8,
                            linestyle="-",
                            zorder=6,
                            label=f"Aimsun Avg: {aimsun_val:.3f}",
                        )

            ax.set_xticks(range(len(rep_ids)))
            ax.set_xticklabels([str(r) for r in rep_ids], rotation=45, ha="right", fontsize=7)
            ax.set_xlabel("Replication DID", fontsize=8)

        ax.set_title(subplot_title, fontsize=8, fontweight="bold")
        ax.set_ylabel(metric, fontsize=8)
        ax.legend(
            fontsize=6.5,
            frameon=False,
            ncol=2 if n_reps > 7 else 1,
            title="Replication",
            title_fontsize=6,
        )

    for ax_i in range(n_plots, len(axes_flat)):
        axes_flat[ax_i].set_visible(False)

    mode_label = "time-series" if has_intervals else "summary"
    fig.suptitle(
        f"{metric}  |  {n_reps} replication(s)  [{mode_label}]",
        fontsize=11,
        fontweight="bold",
        y=1.01,
    )
    fig.tight_layout()
    return fig


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

# ── Plots ─────────────────────────────────────────────────────────────────────
st.subheader(f"{table} · {metric}")

if plot_df.empty:
    st.warning("No data after applying filters.")
else:
    # Split by experiment so each gets its own figure
    for exp in sorted(plot_df["experiment"].unique()):
        st.markdown(f"**{exp}**")
        exp_df = plot_df[plot_df["experiment"] == exp]
        exp_avg_df = plot_avg_df[plot_avg_df["experiment"] == exp] if not plot_avg_df.empty else pd.DataFrame()

        fig = make_figure(exp_df, exp_avg_df, metric, DEFAULT_OID_LABELS, tuple(selected_eids))
        if fig:
            st.pyplot(fig, use_container_width=True)
            plt.close(fig)
        else:
            st.info(f"No plottable data for {exp}.")

# ── Raw data table ────────────────────────────────────────────────────────────
with st.expander("Raw data"):
    display_df = plot_df.copy()
    if "oid" in display_df.columns:
        display_df["oid_label"] = display_df["oid"].map(
            lambda o: DEFAULT_OID_LABELS.get(o, str(o))
        )
    st.dataframe(display_df, use_container_width=True)
    st.caption(f"{len(display_df):,} rows")
