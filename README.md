# Shipment Profit Analyzer

A local Streamlit app to combine:
- ShipStation shipment/order export (CSV)
- COGS report (CSV)

and calculate per-shipment profit so you can spot low-profit orders.

## What it calculates

`profit = revenue - estimated marketplace fee - postage cost - COGS`

The app supports:
- Uploading both CSVs
- Mapping columns in the UI (so your exports can vary)
- Marketplace fee estimation (default + known marketplace heuristics)
- Low-profit threshold filtering
- CSV download for all shipments and low-profit subset

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL shown in terminal (usually `http://localhost:8501`).

## Input tips

ShipStation report should include at minimum:
- order identifier
- revenue field
- postage/shipping cost field

COGS report works best with:
- order identifier and SKU
- either line COGS or unit COGS (+ quantity)

If auto-detection is wrong, use the **Column mapping** section.
