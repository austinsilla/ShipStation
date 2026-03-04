import io
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Shipment Profit Analyzer", layout="wide")


@dataclass
class MatchedColumns:
    order_id: str
    sku: Optional[str]
    revenue: str
    postage: str
    quantity: Optional[str]
    marketplace: Optional[str]


def normalize_col(s: str) -> str:
    return "".join(ch.lower() for ch in str(s) if ch.isalnum())


def find_first_match(columns: List[str], candidates: List[str]) -> Optional[str]:
    normalized_map = {normalize_col(c): c for c in columns}
    for candidate in candidates:
        key = normalize_col(candidate)
        if key in normalized_map:
            return normalized_map[key]

    # Fuzzy fallback: contains check
    lowered = {c.lower(): c for c in columns}
    for candidate in candidates:
        c = candidate.lower()
        for col_lower, original in lowered.items():
            if c in col_lower or col_lower in c:
                return original
    return None


def auto_detect_shipstation_columns(df: pd.DataFrame) -> MatchedColumns:
    cols = list(df.columns)
    order_id = find_first_match(
        cols,
        [
            "Order Number",
            "Order ID",
            "Order #",
            "Order",
            "OrderNumber",
            "OrderId",
        ],
    )
    sku = find_first_match(cols, ["SKU", "Sku", "Product SKU", "Item SKU"])
    revenue = find_first_match(
        cols,
        [
            "Order Total",
            "Total",
            "Item Total",
            "Item Price",
            "Revenue",
            "Sale Amount",
            "Amount Paid",
        ],
    )
    postage = find_first_match(
        cols,
        ["Shipping Cost", "Postage", "Postage Cost", "Shipment Cost", "Cost"],
    )
    quantity = find_first_match(cols, ["Quantity", "Qty", "Item Qty"])
    marketplace = find_first_match(
        cols,
        ["Store", "Marketplace", "Source", "Channel", "Selling Channel"],
    )

    return MatchedColumns(
        order_id=order_id or "",
        sku=sku,
        revenue=revenue or "",
        postage=postage or "",
        quantity=quantity,
        marketplace=marketplace,
    )


def auto_detect_cogs_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    cols = list(df.columns)
    return {
        "order_id": find_first_match(cols, ["Order Number", "Order ID", "Order", "Order #"]),
        "sku": find_first_match(cols, ["SKU", "Product SKU", "Item SKU"]),
        "unit_cogs": find_first_match(
            cols,
            [
                "COGS",
                "Unit COGS",
                "Cost",
                "Cost Per Unit",
                "Unit Cost",
            ],
        ),
        "line_cogs": find_first_match(
            cols,
            ["Line COGS", "Total COGS", "Extended Cost", "Line Cost"],
        ),
        "quantity": find_first_match(cols, ["Quantity", "Qty", "Item Qty"]),
    }


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(r"[^0-9.-]", "", regex=True), errors="coerce").fillna(0)


def estimate_fee_rate(marketplace: pd.Series, default_rate: float) -> pd.Series:
    marketplace = marketplace.fillna("").astype(str).str.lower()
    # Reasonable defaults; user can override globally.
    rates = pd.Series(default_rate, index=marketplace.index)
    rates = rates.where(~marketplace.str.contains("amazon"), 0.15)
    rates = rates.where(~marketplace.str.contains("ebay"), 0.13)
    rates = rates.where(~marketplace.str.contains("etsy"), 0.10)
    rates = rates.where(~marketplace.str.contains("shopify"), 0.029)
    return rates


def build_profit_table(
    ship_df: pd.DataFrame,
    cogs_df: pd.DataFrame,
    ship_cols: MatchedColumns,
    cogs_cols: Dict[str, Optional[str]],
    default_fee_rate_pct: float,
) -> pd.DataFrame:
    data = ship_df.copy()
    data["order_id"] = data[ship_cols.order_id].astype(str).str.strip()
    data["sku"] = data[ship_cols.sku].astype(str).str.strip() if ship_cols.sku else ""
    data["revenue"] = to_num(data[ship_cols.revenue])
    data["postage_cost"] = to_num(data[ship_cols.postage])
    data["quantity"] = to_num(data[ship_cols.quantity]) if ship_cols.quantity else 1
    data["quantity"] = data["quantity"].where(data["quantity"] > 0, 1)
    data["marketplace"] = data[ship_cols.marketplace] if ship_cols.marketplace else ""

    cogs = cogs_df.copy()
    if cogs_cols["order_id"]:
        cogs["order_id"] = cogs[cogs_cols["order_id"]].astype(str).str.strip()
    else:
        cogs["order_id"] = ""

    if cogs_cols["sku"]:
        cogs["sku"] = cogs[cogs_cols["sku"]].astype(str).str.strip()
    else:
        cogs["sku"] = ""

    if cogs_cols["line_cogs"]:
        cogs["line_cogs"] = to_num(cogs[cogs_cols["line_cogs"]])
    else:
        cogs["line_cogs"] = 0

    if cogs_cols["unit_cogs"]:
        cogs["unit_cogs"] = to_num(cogs[cogs_cols["unit_cogs"]])
    else:
        cogs["unit_cogs"] = 0

    if cogs_cols["quantity"]:
        cogs["quantity"] = to_num(cogs[cogs_cols["quantity"]]).where(lambda s: s > 0, 1)
    else:
        cogs["quantity"] = 1

    cogs["computed_cogs"] = cogs["line_cogs"].where(cogs["line_cogs"] > 0, cogs["unit_cogs"] * cogs["quantity"])

    # Prefer matching by order_id + sku; fallback by order_id only.
    key_detail = ["order_id", "sku"]
    cogs_detail = cogs.groupby(key_detail, dropna=False, as_index=False)["computed_cogs"].sum()
    data = data.merge(cogs_detail, on=key_detail, how="left")

    missing_cogs = data["computed_cogs"].isna()
    if missing_cogs.any():
        cogs_order = cogs.groupby("order_id", as_index=False)["computed_cogs"].sum().rename(columns={"computed_cogs": "order_cogs"})
        data = data.merge(cogs_order, on="order_id", how="left")
        data["computed_cogs"] = data["computed_cogs"].fillna(data["order_cogs"])
        data = data.drop(columns=[c for c in ["order_cogs"] if c in data.columns])

    data["computed_cogs"] = data["computed_cogs"].fillna(0)

    fee_rate = estimate_fee_rate(data["marketplace"], default_fee_rate_pct / 100)
    data["estimated_fee_rate"] = fee_rate
    data["estimated_marketplace_fee"] = data["revenue"] * fee_rate

    data["profit"] = data["revenue"] - data["estimated_marketplace_fee"] - data["postage_cost"] - data["computed_cogs"]
    data["profit_margin_pct"] = data["profit"].div(data["revenue"].replace(0, pd.NA)).fillna(0) * 100

    selected = [
        "order_id",
        "sku",
        "marketplace",
        "quantity",
        "revenue",
        "estimated_fee_rate",
        "estimated_marketplace_fee",
        "postage_cost",
        "computed_cogs",
        "profit",
        "profit_margin_pct",
    ]
    return data[selected].sort_values("profit")


def csv_download(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def render_column_mapper(title: str, df: pd.DataFrame, defaults: Dict[str, Optional[str]], required: List[str]) -> Dict[str, str]:
    st.subheader(title)
    cols = [""] + list(df.columns)
    mapping = {}
    for key, default in defaults.items():
        idx = cols.index(default) if default in cols else 0
        mapping[key] = st.selectbox(
            f"{key.replace('_', ' ').title()} column",
            options=cols,
            index=idx,
            key=f"{title}-{key}",
        )

    missing = [k for k in required if not mapping.get(k)]
    if missing:
        st.warning(f"Required mappings missing: {', '.join(missing)}")
    return mapping


st.title("Shipment Profit Analyzer")
st.caption("Upload ShipStation and COGS reports, then find low-profit shipments quickly.")

with st.sidebar:
    st.header("Settings")
    default_fee_rate_pct = st.number_input(
        "Default marketplace fee %",
        min_value=0.0,
        max_value=40.0,
        value=12.0,
        step=0.5,
        help="Used when marketplace-specific rate is not recognized.",
    )
    low_profit_threshold = st.number_input(
        "Low-profit threshold ($)",
        value=5.0,
        step=0.5,
        help="Shipments with profit at or below this value are flagged.",
    )

ship_file = st.file_uploader("Upload ShipStation report (CSV)", type=["csv"])
cogs_file = st.file_uploader("Upload COGS report (CSV)", type=["csv"])

if ship_file and cogs_file:
    ship_df = pd.read_csv(ship_file)
    cogs_df = pd.read_csv(cogs_file)

    auto_ship = auto_detect_shipstation_columns(ship_df)
    auto_cogs = auto_detect_cogs_columns(cogs_df)

    with st.expander("Column mapping", expanded=True):
        ship_map = render_column_mapper(
            "ShipStation Columns",
            ship_df,
            {
                "order_id": auto_ship.order_id,
                "sku": auto_ship.sku,
                "revenue": auto_ship.revenue,
                "postage": auto_ship.postage,
                "quantity": auto_ship.quantity,
                "marketplace": auto_ship.marketplace,
            },
            required=["order_id", "revenue", "postage"],
        )

        cogs_map = render_column_mapper(
            "COGS Columns",
            cogs_df,
            auto_cogs,
            required=[],
        )

    if ship_map["order_id"] and ship_map["revenue"] and ship_map["postage"]:
        ship_cols = MatchedColumns(
            order_id=ship_map["order_id"],
            sku=ship_map["sku"] or None,
            revenue=ship_map["revenue"],
            postage=ship_map["postage"],
            quantity=ship_map["quantity"] or None,
            marketplace=ship_map["marketplace"] or None,
        )

        cogs_cols = {k: (v if v else None) for k, v in cogs_map.items()}

        try:
            result = build_profit_table(ship_df, cogs_df, ship_cols, cogs_cols, default_fee_rate_pct)
            result["is_low_profit"] = result["profit"] <= low_profit_threshold

            total_shipments = len(result)
            low_profit_count = int(result["is_low_profit"].sum())
            avg_profit = float(result["profit"].mean()) if total_shipments else 0

            c1, c2, c3 = st.columns(3)
            c1.metric("Total shipments", f"{total_shipments:,}")
            c2.metric("Low-profit shipments", f"{low_profit_count:,}")
            c3.metric("Average profit", f"${avg_profit:,.2f}")

            st.subheader("Low-profit shipments")
            low_df = result[result["is_low_profit"]].copy()
            st.dataframe(low_df, use_container_width=True, height=300)

            st.subheader("All shipments")
            st.dataframe(result, use_container_width=True, height=500)

            st.download_button(
                label="Download full profit report (CSV)",
                data=csv_download(result),
                file_name="shipment_profit_report.csv",
                mime="text/csv",
            )

            st.download_button(
                label="Download low-profit shipments (CSV)",
                data=csv_download(low_df),
                file_name="low_profit_shipments.csv",
                mime="text/csv",
            )

        except Exception as exc:
            st.error(f"Could not calculate profit table: {exc}")
else:
    st.info("Upload both CSV files to start analysis.")

with st.expander("How profit is calculated"):
    st.markdown(
        """
`profit = revenue - estimated_marketplace_fee - postage_cost - cogs`

Notes:
- Fee estimation uses marketplace-specific defaults when marketplace text contains Amazon/eBay/Etsy/Shopify.
- Otherwise it uses the `Default marketplace fee %` from the sidebar.
- COGS is matched by `order_id + sku` first, then falls back to `order_id`.
"""
    )
