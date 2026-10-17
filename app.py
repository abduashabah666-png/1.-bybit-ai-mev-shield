import json
import unittest
import streamlit as st
import requests
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

APP_VERSION = "V4.6"
DEFAULT_RPC = "https://ethereum-rpc.publicnode.com"
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
TOKENS = {
    "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18, "id": "ethereum"},
    "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6, "id": "usd-coin"},
    "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6, "id": "tether"},
    "DAI": {"address": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18, "id": "dai"},
}
FEES = {"0.01%": 100, "0.05%": 500, "0.30%": 3000, "1.00%": 10000}


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def assess(deviation, slippage, sensitivity):
    """Transparent heuristic only; not attack detection. Missing inputs are not treated as low risk."""
    missing = []
    if deviation is None:
        missing.append("External reference deviation is unavailable.")
    if sensitivity is None:
        missing.append("Quote-size sensitivity is unavailable.")

    score = 0
    reasons = []

    if deviation is not None:
        magnitude = abs(float(deviation))
        if magnitude >= 5:
            score += 45
            reasons.append("Quote differs from the reference snapshot by at least 5%.")
        elif magnitude >= 2:
            score += 25
            reasons.append("Quote differs from the reference snapshot by at least 2%.")
        elif magnitude >= 1:
            score += 10
            reasons.append("Quote differs from the reference snapshot by at least 1%.")
        else:
            reasons.append("Quote deviation from the reference snapshot is below 1%.")

    if slippage >= 3:
        score += 25
        reasons.append("Selected slippage tolerance is 3% or more.")
    elif slippage >= 1:
        score += 12
        reasons.append("Selected slippage tolerance is at least 1%.")
    else:
        reasons.append("Selected slippage tolerance is below 1%.")

    if sensitivity is not None:
        magnitude = abs(float(sensitivity))
        if magnitude >= 5:
            score += 25
            reasons.append("Output-per-input varies by at least 5% across probes.")
        elif magnitude >= 2:
            score += 15
            reasons.append("Output-per-input varies by at least 2% across probes.")
        elif magnitude >= 1:
            score += 8
            reasons.append("Output-per-input varies by at least 1% across probes.")
        else:
            reasons.append("Quote-size sensitivity is below 1% across tested probes.")

    score = min(score, 100)
    if missing:
        label = "Insufficient data"
        status = "insufficient_data"
    else:
        label = "High indicator level" if score >= 50 else "Moderate indicator level" if score >= 25 else "Lower indicator level"
        status = "indicators_available"

    return {
        "score_out_of_100": score,
        "label": label,
        "status": status,
        "missing_data": missing,
        "reasons": reasons,
        "verified_attack_detection": False,
        "warning": "Heuristic indicators only. This score does not confirm an attack or prove MEV prevention."
    }


def rpc_call(url, method, params):
    response = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        error = data["error"]
        raise RuntimeError(f"RPC error {error.get('code', '')}: {error.get('message', str(error))}")
    if "result" not in data:
        raise RuntimeError("RPC response did not include a result.")
    return data["result"]


def uint_word(value):
    if int(value) < 0 or int(value) >= 2**256:
        raise ValueError("Value is outside uint256 range.")
    return int(value).to_bytes(32, "big").hex()


def address_word(address):
    address = address.lower().removeprefix("0x")
    if len(address) != 40 or any(c not in "0123456789abcdef" for c in address):
        raise ValueError("Invalid token address.")
    return address.rjust(64, "0")


def quote_calldata(token_in, token_out, fee, amount_raw):
    # QuoterV2 quoteExactInputSingle tuple ABI: (tokenIn, tokenOut, amountIn, fee, sqrtPriceLimitX96)
    return "0xc6a5026a" + address_word(token_in) + address_word(token_out) + uint_word(amount_raw) + uint_word(fee) + uint_word(0)


def decode_words(result):
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RuntimeError(f"Unexpected RPC result: {result!r}")
    raw = result[2:]
    if len(raw) < 64 or len(raw) % 64:
        raise RuntimeError("Unexpected QuoterV2 response; pool may be unavailable or call reverted.")
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw), 64)]


def get_quote(rpc, token_in, token_out, fee, amount):
    raw_amount = int(amount * (Decimal(10) ** TOKENS[token_in]["decimals"]))
    if raw_amount <= 0:
        raise ValueError("Amount is too small for selected token precision.")
    calldata = quote_calldata(TOKENS[token_in]["address"], TOKENS[token_out]["address"], fee, raw_amount)
    words = decode_words(rpc_call(rpc, "eth_call", [{"to": QUOTER_V2, "data": calldata}, "latest"]))
    output = Decimal(words[0]) / (Decimal(10) ** TOKENS[token_out]["decimals"])
    return output, (words[3] if len(words) > 3 else None)


@st.cache_data(ttl=20, show_spinner=False)
def reference_prices(ids):
    response = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ",".join(ids), "vs_currencies": "usd"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    prices = {item: float(data[item]["usd"]) for item in ids if item in data and "usd" in data[item]}
    return prices, now_utc()


def build_report():
    return {
        "app": "Anti-MEV Shield",
        "version": APP_VERSION,
        "report_created_at_utc": now_utc(),
        "status": "started",
        "limitations": [
            "Experimental proof of concept.",
            "Read-only; no wallet connection, signing, or transaction submission.",
            "No live mempool monitoring or private relay.",
            "Heuristic indicators are not verified attack detection and do not prove MEV prevention.",
        ],
        "inputs": {},
        "quote": None,
        "quote_size_probes": [],
        "reference_comparison": None,
        "heuristic_indicators": None,
        "errors": [],
    }


st.set_page_config(page_title=f"Anti-MEV Shield {APP_VERSION}", page_icon="🛡️", layout="wide")
st.title(f"🛡️ Anti-MEV Shield {APP_VERSION}")
st.caption("Read-only quote comparison, quote-size sensitivity, transparent heuristic indicators, and downloadable JSON reports.")
st.warning(
    "Experimental proof of concept only. No wallet connection, signing, transaction submission, "
    "live mempool monitoring, private relay, or guaranteed MEV prevention. "
    "Heuristic indicators are not verified attack detection."
)

with st.sidebar:
    st.header("Connection")
    rpc = st.text_input("Ethereum Mainnet RPC URL", DEFAULT_RPC)
    st.caption("Never enter a seed phrase or private key.")
    if st.button("Test RPC connection", use_container_width=True):
        try:
            chain_id = rpc_call(rpc.strip(), "eth_chainId", [])
            block_hex = rpc_call(rpc.strip(), "eth_blockNumber", [])
            if int(chain_id, 16) != 1:
                st.error(f"Connected to chain ID {int(chain_id, 16)}; Ethereum Mainnet (1) is required.")
            else:
                st.success(f"Connected • Ethereum Mainnet • Block {int(block_hex, 16):,}")
        except Exception as exc:
            st.error(f"Connection failed: {exc}")

st.subheader("Uniswap V3 indicative quote")
col1, col2, col3 = st.columns(3)
with col1:
    token_in = st.selectbox("Input token", list(TOKENS), index=0)
with col2:
    token_out = st.selectbox("Output token", list(TOKENS), index=1)
with col3:
    fee_label = st.selectbox("Pool fee tier", list(FEES), index=2)

amount_text = st.text_input("Input amount", "0.1")
slippage = st.slider("Illustrative slippage tolerance (%)", 0.1, 5.0, 0.5, 0.1)
run = st.button("Get quote & analyze", type="primary", use_container_width=True)

if "last_report" not in st.session_state:
    st.session_state.last_report = None

if token_in == token_out:
    st.info("Choose two different tokens.")
elif run:
    report = build_report()
    report["inputs"] = {
        "input_token": token_in,
        "output_token": token_out,
        "pool_fee_label": fee_label,
        "pool_fee_units": FEES[fee_label],
        "input_amount": amount_text,
        "slippage_tolerance_percent": slippage,
    }

    try:
        amount = Decimal(amount_text)
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Input amount must be a finite positive number.")
        if amount * (Decimal(10) ** TOKENS[token_in]["decimals"]) < 1:
            raise ValueError("Amount is too small for selected token precision.")
        clean_rpc = rpc.strip()
        if not clean_rpc.startswith("https://"):
            raise ValueError("Use an HTTPS Ethereum Mainnet RPC URL.")

        with st.spinner("Checking network and retrieving indicative quote..."):
            chain_id = rpc_call(clean_rpc, "eth_chainId", [])
            if int(chain_id, 16) != 1:
                raise RuntimeError("RPC endpoint is not on Ethereum Mainnet (chain ID 1).")
            block_number = int(rpc_call(clean_rpc, "eth_blockNumber", []), 16)
            output, gas = get_quote(clean_rpc, token_in, token_out, FEES[fee_label], amount)
            quote_time = now_utc()
            minimum = output * (Decimal(1) - Decimal(str(slippage)) / Decimal(100))

        report["status"] = "quote_succeeded"
        report["network"] = {"name": "Ethereum Mainnet", "chain_id": 1, "block_number": block_number}
        report["quote"] = {
            "retrieved_at_utc": quote_time,
            "estimated_output": str(output),
            "output_token": token_out,
            "illustrative_minimum_output": str(minimum),
            "quoter_gas_estimate": gas,
            "execution_price_guaranteed": False,
        }

        st.success("Indicative quote retrieved from configured Ethereum RPC.")
        st.caption(f"Quote fetched at {quote_time} • Not a guaranteed execution price.")
        m1, m2, m3 = st.columns(3)
        m1.metric("Estimated output", f"{output:,.8f} {token_out}")
        m2.metric("Illustrative minimum output", f"{minimum:,.8f} {token_out}")
        m3.metric("Quoter gas estimate", f"{gas:,}" if gas is not None else "Unavailable")

        st.subheader("Quote-size sensitivity (read-only probes)")
        sensitivity = None
        try:
            probe_amounts = [amount / Decimal(10), amount, amount * Decimal(10)]
            probes = []
            with st.spinner("Comparing smaller, current, and larger quote sizes..."):
                for probe_amount in probe_amounts:
                    probe_output, probe_gas = get_quote(
                        clean_rpc, token_in, token_out, FEES[fee_label], probe_amount
                    )
                    probes.append({
                        "input_amount": str(probe_amount),
                        "input_token": token_in,
                        "estimated_output": str(probe_output),
                        "output_token": token_out,
                        "output_per_input_unit": str(probe_output / probe_amount),
                        "quoter_gas_estimate": probe_gas,
                    })
            rates = [Decimal(item["output_per_input_unit"]) for item in probes]
            if rates[1] > 0:
                sensitivity = float((max(rates) - min(rates)) / rates[1] * Decimal(100))
            report["quote_size_probes"] = probes
            report["quote_size_sensitivity_percent"] = sensitivity
            st.dataframe(
                [{
                    "Input amount": f"{Decimal(item['input_amount']).normalize():f} {token_in}",
                    "Estimated output": f"{Decimal(item['estimated_output']):.8f} {token_out}",
                    "Output per input unit": f"{Decimal(item['output_per_input_unit']):.8f} {token_out}/{token_in}",
                } for item in probes],
                use_container_width=True,
                hide_index=True,
            )
            st.metric(
                "Variation in output per input across probes",
                f"{sensitivity:.3f}%" if sensitivity is not None else "Unavailable",
            )
            st.caption("Three quote-size probes only; not an independently verified price-impact or MEV measurement.")
        except Exception as exc:
            report["errors"].append({
                "stage": "quote_size_probes",
                "message": str(exc),
                "occurred_at_utc": now_utc(),
            })
            st.warning(f"Quote succeeded, but size-sensitivity probes were incomplete: {exc}")

        deviation = None
        expected = None
        in_price = None
        out_price = None
        reference_time = None
        try:
            with st.spinner("Fetching external reference prices..."):
                ids = sorted({TOKENS[token_in]["id"], TOKENS[token_out]["id"]})
                prices, reference_time = reference_prices(ids)
                input_id, output_id = TOKENS[token_in]["id"], TOKENS[token_out]["id"]
                if input_id not in prices or output_id not in prices:
                    raise RuntimeError("External reference price unavailable for one or both tokens.")
                in_price, out_price = prices[input_id], prices[output_id]
                expected = float(amount) * in_price / out_price
                if expected <= 0:
                    raise RuntimeError("Reference output is not positive.")
                deviation = (float(output) - expected) / expected * 100
            report["reference_comparison"] = {
                "provider": "CoinGecko simple price API",
                "retrieved_at_utc": reference_time,
                "input_token_usd_price": in_price,
                "output_token_usd_price": out_price,
                "approx_reference_output": expected,
                "quote_deviation_percent": deviation,
                "market_observation_timestamp_provided": False,
                "note": "Retrieval time is not a market-observation timestamp; data may be cached.",
            }
        except Exception as exc:
            report["errors"].append({
                "stage": "reference_price_comparison",
                "message": str(exc),
                "occurred_at_utc": now_utc(),
            })
            st.warning(f"External-reference comparison unavailable: {exc}")

        assessment = assess(deviation, slippage, sensitivity)
        report["heuristic_indicators"] = assessment
        report["status"] = (
            "analysis_completed_with_warnings"
            if report["errors"] or assessment["status"] == "insufficient_data"
            else "analysis_completed"
        )
        report["report_updated_at_utc"] = now_utc()

        st.subheader("External reference-price comparison")
        a, b, c = st.columns(3)
        a.metric("Approx. reference output", f"{expected:,.8f} {token_out}" if expected is not None else "Unavailable")
        b.metric("Quote deviation", f"{deviation:+.3f}%" if deviation is not None else "Unavailable")
        c.metric(
            "Reference prices",
            f"${in_price:,.4f} / ${out_price:,.4f}" if in_price is not None and out_price is not None else "Unavailable",
        )
        if reference_time:
            st.caption(
                f"Reference prices retrieved at {reference_time}; this is retrieval time, not a guarantee of real-time data."
            )

        st.subheader("Pre-trade heuristic indicators")
        st.metric("Heuristic indicator score", f"{assessment['score_out_of_100']}/100", assessment["label"])
        if assessment["status"] == "insufficient_data":
            st.warning("Insufficient data to characterize the indicators as low. Review the missing stages below.")
        elif assessment["score_out_of_100"] >= 50:
            st.error("Several indicators warrant extra review. This is not a verified threat alert.")
        elif assessment["score_out_of_100"] >= 25:
            st.warning("Review reference price, liquidity, fee tier, sensitivity, and slippage.")
        else:
            st.info("Indicators are relatively low for this snapshot; this is not a safety guarantee.")

        st.write("**Reasons considered**")
        for reason in assessment["reasons"]:
            st.write("- " + reason)
        if assessment["missing_data"]:
            st.write("**Missing data**")
            for item in assessment["missing_data"]:
                st.write("- " + item)
        st.caption(assessment["warning"])

    except (InvalidOperation, ValueError, OverflowError) as exc:
        report["status"] = "input_or_validation_error"
        report["errors"].append({"stage": "validation", "message": str(exc), "occurred_at_utc": now_utc()})
        st.error(f"Enter a valid positive amount and valid HTTPS RPC URL. Details: {exc}")
    except Exception as exc:
        report["status"] = "quote_failed"
        report["errors"].append({"stage": "quote", "message": str(exc), "occurred_at_utc": now_utc()})
        st.error(f"Quote request failed: {exc}")
        st.caption("Check RPC endpoint, selected pair, and whether the fee-tier pool exists.")

    report["report_updated_at_utc"] = now_utc()
    st.session_state.last_report = report

if st.session_state.last_report is not None:
    st.divider()
    st.subheader("Download test report")
    st.caption("Report reflects the latest analysis run in this session, including errors or incomplete stages.")
    st.download_button(
        "⬇️ Download JSON Report",
        data=json.dumps(st.session_state.last_report, indent=2, ensure_ascii=False),
        file_name=f"anti_mev_shield_v4_6_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
        mime="application/json",
        use_container_width=True,
    )
    with st.expander("Preview JSON report"):
        st.json(st.session_state.last_report)

st.divider()
st.subheader("Read-only transaction lookup (optional)")
tx_hash = st.text_input("Ethereum transaction hash", placeholder="0x…")
if st.button("Inspect transaction", use_container_width=True):
    tx_hash = tx_hash.strip()
    if len(tx_hash) != 66 or not tx_hash.startswith("0x") or any(
        character not in "0123456789abcdefABCDEF" for character in tx_hash[2:]
    ):
        st.error("Enter a transaction hash: 0x followed by 64 hexadecimal characters.")
    else:
        try:
            transaction = rpc_call(rpc.strip(), "eth_getTransactionByHash", [tx_hash])
            receipt = rpc_call(rpc.strip(), "eth_getTransactionReceipt", [tx_hash])
            if not transaction:
                st.warning("Transaction not found by this RPC.")
            else:
                st.json({
                    "hash": transaction.get("hash"),
                    "from": transaction.get("from"),
                    "to": transaction.get("to"),
                    "value_wei": transaction.get("value"),
                    "block_number": transaction.get("blockNumber"),
                    "receipt_status": receipt.get("status") if receipt else "pending or unavailable",
                })
                st.caption("Read-only RPC data only; not a full security audit.")
        except Exception as exc:
            st.error(f"Transaction lookup failed: {exc}")

st.divider()
st.caption(
    f"Anti-MEV Shield {APP_VERSION} • Experimental proof of concept • No wallet connection, signing, "
    "transaction submission, private relay, or guaranteed MEV protection."
)
