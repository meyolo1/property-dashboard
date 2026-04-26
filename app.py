"""
9 Dawn Drive — Property Dashboard
Reads all config from environment variables or Streamlit secrets.
"""

import os
from datetime import date
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ── Config from secrets or env ────────────────────────────────────────────────

def get_secret(key: str) -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")

NOTION_API_KEY          = get_secret("NOTION_API_KEY")
NOTION_EXPENSES_DB_ID   = get_secret("NOTION_EXPENSES_DB_ID")
NOTION_INCOME_DB_ID     = get_secret("NOTION_INCOME_DB_ID")
NOTION_AGENT_REPORTS_DB_ID = get_secret("NOTION_AGENT_REPORTS_DB_ID")
NOTION_API_VERSION      = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_API_VERSION,
    "Content-Type": "application/json",
}

# ── Australian FY helpers ─────────────────────────────────────────────────────

def current_fy() -> tuple[str, str]:
    today = date.today()
    if today.month >= 7:
        return f"{today.year}-07-01", f"{today.year + 1}-06-30"
    return f"{today.year - 1}-07-01", f"{today.year}-06-30"

def fy_label() -> str:
    start, end = current_fy()
    return f"FY{start[2:4]}/{end[2:4]}"

# ── Notion fetch helpers ──────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_all_pages(db_id: str) -> list[dict]:
    pages, cursor = [], None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=HEADERS,
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return pages


def prop(page: dict, key: str):
    p = page.get("properties", {}).get(key, {})
    t = p.get("type")
    if t == "title":
        items = p.get("title", [])
        return items[0]["plain_text"] if items else ""
    if t == "rich_text":
        items = p.get("rich_text", [])
        return items[0]["plain_text"] if items else ""
    if t == "number":
        return p.get("number")
    if t == "date":
        d = p.get("date")
        return d["start"] if d else None
    if t == "select":
        s = p.get("select")
        return s["name"] if s else None
    if t == "checkbox":
        return p.get("checkbox", False)
    return None


def pages_to_df(pages: list[dict], fields: dict) -> pd.DataFrame:
    rows = [{col: prop(p, nk) for col, nk in fields.items()} for p in pages]
    return pd.DataFrame(rows)

# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_expenses() -> pd.DataFrame:
    pages = fetch_all_pages(NOTION_EXPENSES_DB_ID)
    if not pages:
        return pd.DataFrame()
    df = pages_to_df(pages, {
        "date": "Date", "vendor": "Vendor", "category": "Category",
        "amount": "Amount", "gst": "GST", "description": "Description",
        "billing_period": "Billing Period", "bill_state": "Bill State", "status": "Status",
    })
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    df["month"] = df["date"].dt.to_period("M")
    return df.sort_values("date")


@st.cache_data(ttl=300)
def load_income() -> pd.DataFrame:
    pages = fetch_all_pages(NOTION_INCOME_DB_ID)
    if not pages:
        return pd.DataFrame()
    df = pages_to_df(pages, {
        "date": "Date", "description": "Description",
        "tenant": "Tenant", "type": "Type", "amount": "Amount",
    })
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    df["month"] = df["date"].dt.to_period("M")
    return df.sort_values("date")


@st.cache_data(ttl=300)
def load_agent_reports() -> pd.DataFrame:
    pages = fetch_all_pages(NOTION_AGENT_REPORTS_DB_ID)
    if not pages:
        return pd.DataFrame()
    df = pages_to_df(pages, {
        "date": "Date", "report_type": "Report Type", "key_notes": "Key Notes",
        "action_items": "Action Items", "flags": "Flags", "full_summary": "Full Summary",
    })
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.sort_values("date", ascending=False)

# ── App ───────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="9 Dawn Drive",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("🏠 9 Dawn Drive — Seven Hills NSW")

if not NOTION_API_KEY:
    st.error("NOTION_API_KEY not set in Streamlit secrets.")
    st.stop()

if not NOTION_EXPENSES_DB_ID:
    st.error("NOTION_EXPENSES_DB_ID not set in Streamlit secrets.")
    st.stop()

with st.spinner("Loading data from Notion..."):
    expenses_all = load_expenses()
    income_all   = load_income()

if expenses_all.empty and income_all.empty:
    st.warning("No data found. Check Notion database IDs in secrets.")
    st.stop()

fy_start_str, fy_end_str = current_fy()
fy_start = pd.Timestamp(fy_start_str)
fy_end   = pd.Timestamp(fy_end_str)

tab_fy, tab_all, tab_bills, tab_tenants, tab_reports = st.tabs([
    f"📊 {fy_label()} Summary", "📅 All Time", "🧾 Bills", "🏘️ Tenants", "📋 Agent Reports",
])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — FY Summary
# ═══════════════════════════════════════════════════════════════════════════════
with tab_fy:
    exp_fy = expenses_all[
        (expenses_all["date"] >= fy_start) &
        (expenses_all["date"] <= fy_end) &
        (expenses_all["bill_state"] != "Withdrawn")
    ]
    inc_fy = income_all[
        (income_all["date"] >= fy_start) &
        (income_all["date"] <= fy_end) &
        (income_all["type"] == "Rent")
    ]

    total_income   = inc_fy["amount"].sum()
    total_expenses = exp_fy["amount"].sum()
    net            = total_income - total_expenses
    yield_pct      = (net / total_income * 100) if total_income > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rental Income",   f"${total_income:,.0f}")
    c2.metric("Total Expenses",  f"${total_expenses:,.0f}")
    c3.metric("Net Income",      f"${net:,.0f}", delta=f"${net:,.0f}")
    c4.metric("Net Yield",       f"{yield_pct:.1f}%")

    st.divider()
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("Monthly Income vs Expenses")
        months = pd.period_range(
            start=fy_start,
            end=min(fy_end, pd.Timestamp(date.today())),
            freq="M",
        )
        df_m = pd.DataFrame({"month": months, "month_str": [str(m) for m in months]})
        em = exp_fy.groupby("month")["amount"].sum().reset_index()
        em["month_str"] = em["month"].astype(str)
        im = inc_fy.groupby("month")["amount"].sum().reset_index()
        im["month_str"] = im["month"].astype(str)
        merged = df_m.merge(im.rename(columns={"amount": "Income"}), on="month_str", how="left") \
                     .merge(em.rename(columns={"amount": "Expenses"}), on="month_str", how="left")
        merged[["Income", "Expenses"]] = merged[["Income", "Expenses"]].fillna(0)
        merged["Net"] = merged["Income"] - merged["Expenses"]

        fig = go.Figure()
        fig.add_bar(x=merged["month_str"], y=merged["Income"],   name="Income",   marker_color="#2ecc71")
        fig.add_bar(x=merged["month_str"], y=merged["Expenses"], name="Expenses", marker_color="#e74c3c")
        fig.add_scatter(x=merged["month_str"], y=merged["Net"], name="Net",
                        mode="lines+markers", line=dict(color="#3498db", width=2))
        fig.update_layout(barmode="group", height=350, margin=dict(t=10, b=10),
                          legend=dict(orientation="h"), plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.subheader("Expenses by Category")
        if not exp_fy.empty:
            cat_sum = exp_fy.groupby("category")["amount"].sum().reset_index()
            fig2 = px.pie(cat_sum, names="category", values="amount",
                          color_discrete_sequence=px.colors.qualitative.Set3, hole=0.4)
            fig2.update_layout(height=350, margin=dict(t=10, b=10))
            fig2.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Top Expenses This FY")
        if not exp_fy.empty:
            top = exp_fy.nlargest(10, "amount")[["date", "vendor", "category", "amount", "bill_state"]].copy()
            top["date"]   = top["date"].dt.strftime("%d %b %Y")
            top["amount"] = top["amount"].apply(lambda x: f"${x:,.2f}")
            st.dataframe(top, hide_index=True, use_container_width=True)

    with col_b:
        st.subheader("Outstanding Bills (Due)")
        due = expenses_all[expenses_all["bill_state"] == "Due"].sort_values("date", ascending=False)
        if due.empty:
            st.success("No outstanding bills.")
        else:
            disp = due[["date", "vendor", "category", "amount"]].copy()
            disp["date"]   = disp["date"].dt.strftime("%d %b %Y")
            disp["amount"] = disp["amount"].apply(lambda x: f"${x:,.2f}")
            st.dataframe(disp, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Tax Summary")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Gross Rent (assessable)",    f"${total_income:,.0f}")
    t2.metric("Deductible Expenses",        f"${total_expenses:,.0f}")
    t3.metric("Net Rental Income",          f"${net:,.0f}")
    gst_total = exp_fy["gst"].sum() if "gst" in exp_fy.columns else 0
    t4.metric("GST Component (expenses)",   f"${gst_total:,.0f}")
    st.caption(f"Australian Financial Year {fy_label()} · Verify with your accountant")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — All Time
# ═══════════════════════════════════════════════════════════════════════════════
with tab_all:
    st.subheader("All-Time Monthly P&L")
    rent_all  = income_all[income_all["type"] == "Rent"]
    exp_clean = expenses_all[expenses_all["bill_state"] != "Withdrawn"]

    if not rent_all.empty and not exp_clean.empty:
        all_months = pd.period_range(
            start=min(expenses_all["date"].min(), income_all["date"].min()),
            end=max(expenses_all["date"].max(), income_all["date"].max()),
            freq="M",
        )
        df_all = pd.DataFrame({"month": all_months, "month_str": [str(m) for m in all_months]})
        em2 = exp_clean.groupby("month")["amount"].sum().reset_index()
        em2["month_str"] = em2["month"].astype(str)
        im2 = rent_all.groupby("month")["amount"].sum().reset_index()
        im2["month_str"] = im2["month"].astype(str)
        merged2 = df_all.merge(im2.rename(columns={"amount": "Income"}),   on="month_str", how="left") \
                        .merge(em2.rename(columns={"amount": "Expenses"}), on="month_str", how="left")
        merged2[["Income", "Expenses"]] = merged2[["Income", "Expenses"]].fillna(0)
        merged2["Net"]            = merged2["Income"] - merged2["Expenses"]
        merged2["Cumulative Net"] = merged2["Net"].cumsum()

        fig3 = go.Figure()
        fig3.add_bar(x=merged2["month_str"], y=merged2["Income"],   name="Income",   marker_color="#2ecc71")
        fig3.add_bar(x=merged2["month_str"], y=merged2["Expenses"], name="Expenses", marker_color="#e74c3c")
        fig3.add_scatter(x=merged2["month_str"], y=merged2["Cumulative Net"],
                         name="Cumulative Net", mode="lines", yaxis="y2",
                         line=dict(color="#9b59b6", width=2, dash="dot"))
        fig3.update_layout(
            barmode="group", height=420, margin=dict(t=10, b=10),
            yaxis=dict(title="AUD"),
            yaxis2=dict(title="Cumulative Net", overlaying="y", side="right"),
            legend=dict(orientation="h"), plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig3, use_container_width=True)

        a1, a2, a3, a4 = st.columns(4)
        total_bond = income_all[income_all["type"] == "Bond"]["amount"].sum()
        a1.metric("Total Rent Received",  f"${rent_all['amount'].sum():,.0f}")
        a2.metric("Total Bonds Collected",f"${total_bond:,.0f}")
        a3.metric("Total Expenses",       f"${exp_clean['amount'].sum():,.0f}")
        a4.metric("Net All Time",         f"${merged2['Net'].sum():,.0f}")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Bills
# ═══════════════════════════════════════════════════════════════════════════════
with tab_bills:
    f1, f2, f3 = st.columns(3)
    with f1:
        state_filter = st.multiselect("Bill State", ["Due", "Paid", "Withdrawn"], default=["Due", "Paid"])
    with f2:
        cat_options  = sorted(expenses_all["category"].dropna().unique().tolist())
        cat_filter   = st.multiselect("Category", cat_options, default=cat_options)
    with f3:
        date_range   = st.date_input("Date Range", value=(fy_start.date(), date.today()))

    filtered = expenses_all.copy()
    if state_filter:
        filtered = filtered[filtered["bill_state"].isin(state_filter)]
    if cat_filter:
        filtered = filtered[filtered["category"].isin(cat_filter)]
    if len(date_range) == 2:
        filtered = filtered[
            (filtered["date"] >= pd.Timestamp(date_range[0])) &
            (filtered["date"] <= pd.Timestamp(date_range[1]))
        ]

    st.caption(f"{len(filtered)} records · Total: ${filtered['amount'].sum():,.2f}")
    disp_b = filtered[["date", "vendor", "category", "amount", "billing_period", "bill_state", "status", "description"]].copy()
    disp_b["date"]   = disp_b["date"].dt.strftime("%d %b %Y")
    disp_b["amount"] = disp_b["amount"].apply(lambda x: f"${x:,.2f}")
    st.dataframe(disp_b, hide_index=True, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Tenants
# ═══════════════════════════════════════════════════════════════════════════════
with tab_tenants:
    st.subheader("Tenant Rent Income")
    tenant_info = {
        "Tahsin Khan":          {"rate": 650, "start": "2024-12-14", "end": "2026-03-15", "status": "Vacated"},
        "Ashley Uidam":         {"rate": 550, "start": "2025-03-14", "end": "2026-05-10", "status": "Active"},
        "Vaishnavi Pulavarthy": {"rate": 520, "start": "2025-05-30", "end": "2026-05-03", "status": "Active"},
    }
    rent_only = income_all[income_all["type"] == "Rent"]

    for tenant, info in tenant_info.items():
        t_data    = rent_only[rent_only["tenant"] == tenant]
        total_r   = t_data["amount"].sum()
        badge     = "🟢" if info["status"] == "Active" else "⚫"
        with st.expander(f"{badge} {tenant} — ${info['rate']}/fortnight — {info['status']}"):
            tc1, tc2, tc3, tc4 = st.columns(4)
            tc1.metric("Total Rent Paid", f"${total_r:,.0f}")
            tc2.metric("Payments",        len(t_data))
            tc3.metric("Tenancy Start",   info["start"])
            tc4.metric("Tenancy End",     info["end"])
            if not t_data.empty:
                tm = t_data.groupby("month")["amount"].sum().reset_index()
                tm["month_str"] = tm["month"].astype(str)
                fig_t = go.Figure()
                fig_t.add_bar(x=tm["month_str"], y=tm["amount"], marker_color="#3498db")
                fig_t.update_layout(height=200, margin=dict(t=5, b=5, l=10, r=10),
                                    showlegend=False, plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_t, use_container_width=True)
                disp_t = t_data[["date", "description", "amount"]].copy()
                disp_t["date"]   = disp_t["date"].dt.strftime("%d %b %Y")
                disp_t["amount"] = disp_t["amount"].apply(lambda x: f"${x:,.2f}")
                st.dataframe(disp_t, hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Bond Tracker")
    bonds = income_all[income_all["type"] == "Bond"]
    if bonds.empty:
        st.info("No bond records.")
    else:
        disp_bonds = bonds[["date", "tenant", "description", "amount"]].copy()
        disp_bonds["date"]   = disp_bonds["date"].dt.strftime("%d %b %Y")
        disp_bonds["amount"] = disp_bonds["amount"].apply(lambda x: f"${x:,.2f}")
        st.dataframe(disp_bonds, hide_index=True, use_container_width=True)
        st.caption("Bond refunds for Vaishnavi and Ashley due ~May 2026. Verify with NSW Fair Trading.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Agent Reports
# ═══════════════════════════════════════════════════════════════════════════════
with tab_reports:
    reports = load_agent_reports()
    if reports.empty:
        st.info("No agent reports yet. Reports appear once the n8n workflow processes agent emails.")
    else:
        urgent = reports[reports["flags"] == "Urgent"]
        if not urgent.empty:
            st.error(f"⚠️ {len(urgent)} urgent item(s) requiring attention")
        for _, row in reports.iterrows():
            date_str  = row["date"].strftime("%d %b %Y") if pd.notna(row["date"]) else "Unknown"
            flag_icon = "🚨" if row.get("flags") == "Urgent" else "⚠️" if row.get("flags") == "Needs Attention" else "✅"
            with st.expander(f"{flag_icon} {date_str} — {row.get('report_type', 'Report')}"):
                if row.get("key_notes"):
                    st.write("**Key Notes:**")
                    st.write(row["key_notes"])
                if row.get("action_items"):
                    st.write("**Action Items:**")
                    st.warning(row["action_items"])
                if row.get("full_summary"):
                    with st.expander("Full Summary"):
                        st.write(row["full_summary"])

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.markdown("### 9 Dawn Drive")
st.sidebar.caption("Seven Hills NSW · AUS")
st.sidebar.divider()
st.sidebar.markdown(f"**FY:** {fy_label()}")
st.sidebar.markdown(f"**Period:** {fy_start_str} → {fy_end_str}")
st.sidebar.divider()
if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption("Data cached 5 min")
