import streamlit as st
import requests
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import json

st.set_page_config(page_title="Anti-MEV Shield V4.4", page_icon="🛡️", layout="wide")
DEFAULT_RPC = "https://ethereum-rpc.publicnode.com"
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
TOKENS = {
    "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18, "id": "ethereum"},
    "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6, "id": "usd-coin"},
    "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6, "id": "tether"},
    "DAI": {"address": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18, "id": "dai"},
}
FEES = {"0.01%": 100, "0.05%": 500, "0.30%": 3000, "1.00%": 10000}

st.title("🛡️ Anti-MEV Shield V4.4")
st.caption("Read-only quote comparison, reference-data quality checks, quote-size sensitivity, and an exportable test report.")
st.warning("Experimental proof of concept only. It does not connect to a wallet, sign or submit transactions, monitor the live mempool, use a private relay, or guarantee MEV prevention. Indicators are heuristic checks, not verified attack detection or a probability of attack.")


def rpc_call(url, method, params):
    response = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        error = data["error"]
        raise RuntimeError(f"RPC error {error.get('code', '')}: {error.get('message', str(error))}")
    return data.get("result")


def uint_word(value):
    return int(value).to_bytes(32, "big").hex()


def address_word(address):
    address = address.lower().removeprefix("0x")
    if len(address) != 40:
        raise ValueError("Invalid token address")
    return address.rjust(64, "0")


def quote_calldata(token_in, token_out, fee, amount_raw):
    return "0xc6a5026a" + address_word(token_in) + address_word(token_out) + uint_word(amount_raw) + uint_word(fee) + uint_word(0)


def decode_words(result):
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RuntimeError(f"Unexpected RPC result: {result!r}")
    raw = result[2:]
    if len(raw) < 64 or len(raw) % 64:
        raise RuntimeError("Unexpected QuoterV2 response; the pool may be unavailable or the call may have reverted.")
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw), 64)]


def get_quote(rpc, token_in, token_out, fee, amount):
    amount_raw = int(amount * (Decimal(10) ** TOKENS[token_in]["decimals"]))
    if amount_raw <= 0:
        raise ValueError("Amount is too small for the selected token precision.")
    calldata = quote_calldata(TOKENS[token_in]["address"], TOKENS[token_out]["address"], fee, amount_raw)
    result = rpc_call(rpc, "eth_call", [{"to": QUOTER_V2, "data": calldata}, "latest"])
    words = decode_words(result)
    output = Decimal(words[0]) / (Decimal(10) ** TOKENS[token_out]["decimals"])
    gas_estimate = words[3] if len(words) > 3 else None
    return output, gas_estimate


@st.cache_data(ttl=20, show_spinner=False)
def reference_prices(ids):
    response = requests.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": ",".join(ids), "vs_currencies": "usd"}, timeout=15)
    response.raise_for_status()
    data = response.json()
    prices = {item: float(data[item]["usd"]) for item in ids if item in data and "usd" in data[item]}
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return prices, fetched_at


def assess(deviation, slippage, size_sensitivity):
    score = 0
    reasons = []
    if abs(deviation) >= 5:
        score += 45; reasons.append("Quote differs from the external reference snapshot by at least 5%.")
    elif abs(deviation) >= 2:
        score += 25; reasons.append("Quote differs from the external reference snapshot by at least 2%.")
    elif abs(deviation) >= 1:
        score += 10; reasons.append("Quote differs from the external reference snapshot by at least 1%.")
    else:
        reasons.append("Quote deviation from the reference snapshot is below 1%.")

    if slippage >= 3:
        score += 25; reasons.append("Selected slippage tolerance is high (3% or more).")
    elif slippage >= 1:
        score += 12; reasons.append("Selected slippage tolerance is at least 1%.")
    else:
        reasons.append("Selected slippage tolerance is below 1%.")

    if size_sensitivity is None:
        reasons.append("Quote-size sensitivity could not be calculated.")
    elif size_sensitivity >= 5:
        score += 25; reasons.append("Output-per-input changes by at least 5% across the quote-size probes.")
    elif size_sensitivity >= 2:
        score += 15; reasons.append("Output-per-input changes by at least 2% across the quote-size probes.")
    elif size_sensitivity >= 1:
        score += 8; reasons.append("Output-per-input changes by at least 1% across the quote-size probes.")
    else:
        reasons.append("Quote-size sensitivity is below 1% across the tested probes.")

    score = min(score, 100)
    label = "High indicator level" if score >= 50 else "Moderate indicator level" if score >= 25 else "Lower indicator level"
    return score, label, reasons


with st.sidebar:
    st.header("Connection")
    rpc = st.text_input("Ethereum Mainnet RPC URL", DEFAULT_RPC)
    st.caption("Never enter a seed phrase or private key.")
    if st.button("Test RPC connection", use_container_width=True):
        try:
            chain = rpc_call(rpc.strip(), "eth_chainId", [])
            block = rpc_call(rpc.strip(), "eth_blockNumber", [])
            if int(chain, 16) != 1:
                st.error(f"Connected to chain ID {int(chain, 16)}; Ethereum Mainnet (1) is required.")
            else:
                st.success(f"Connected • Ethereum Mainnet • Block {int(block, 16):,}")
        except Exception as exc:
            st.error(f"Connection failed: {exc}")

st.subheader("Uniswap V3 indicative quote")
a, b, c = st.columns(3)
with a:
    token_in = st.selectbox("Input token", list(TOKENS), index=0)
with b:
    token_out = st.selectbox("Output token", list(TOKENS), index=1)
with c:
    fee_label = st.selectbox("Pool fee tier", list(FEES), index=2)
amount_text = st.text_input("Input amount", "0.1")
slippage = st.slider("Illustrative slippage tolerance (%)", 0.1, 5.0, 0.5, 0.1)
run_analysis = st.button("Get quote & analyze", type="primary", use_container_width=True)

if token_in == token_out:
    st.info("Choose two different tokens.")
elif run_analysis:
    try:
        amount = Decimal(amount_text)
        if not amount.is_finite() or amount <= 0:
            raise InvalidOperation
        if amount * (Decimal(10) ** TOKENS[token_in]["decimals"]) < 1:
            raise ValueError("Amount is too small for the selected token precision.")
        clean_rpc = rpc.strip()
        if not clean_rpc.startswith("https://"):
            raise ValueError("Use an HTTPS Ethereum Mainnet RPC URL.")

        with st.spinner("Checking network and retrieving indicative quote..."):
            chain = rpc_call(clean_rpc, "eth_chainId", [])
            if int(chain, 16) != 1:
                raise RuntimeError("RPC endpoint is not on Ethereum Mainnet (chain ID 1).")
            output, gas = get_quote(clean_rpc, token_in, token_out, FEES[fee_label], amount)
            quote_time = datetime.now(timezone.utc)
            minimum = output * (Decimal(1) - Decimal(str(slippage)) / Decimal(100))

        st.success("Indicative quote retrieved from the configured Ethereum RPC.")
        st.caption(f"Quote fetched at {quote_time.strftime('%Y-%m-%d %H:%M:%S UTC')} • Latest state queried; not a guaranteed execution price.")
        x, y, z = st.columns(3)
        x.metric("Estimated output", f"{output:,.8f} {token_out}")
        y.metric("Illustrative minimum output", f"{minimum:,.8f} {token_out}")
        z.metric("Quoter gas estimate", f"{gas:,}" if gas is not None else "Unavailable")

        st.subheader("Quote-size sensitivity (read-only probes)")
        probe_rows = []
        size_sensitivity = None
        probe_error = None
        try:
            with st.spinner("Comparing smaller, current, and larger quote sizes..."):
                probe_amounts = [amount / Decimal(10), amount, amount * Decimal(10)]
                probe_rates = []
                for probe_amount in probe_amounts:
                    probe_output, _ = get_quote(clean_rpc, token_in, token_out, FEES[fee_label], probe_amount)
                    rate = probe_output / probe_amount
                    probe_rates.append(rate)
                    probe_rows.append({"Input amount": f"{probe_amount.normalize():f} {token_in}", "Estimated output": f"{probe_output:.8f} {token_out}", "Output per input unit": f"{rate:.8f} {token_out}/{token_in}"})
            rates = [float(rate) for rate in probe_rates]
            baseline = rates[1]
            if baseline > 0:
                size_sensitivity = (max(rates) - min(rates)) / baseline * 100
            st.dataframe(probe_rows, use_container_width=True, hide_index=True)
            st.metric("Variation in output per input across probes", f"{size_sensitivity:.3f}%" if size_sensitivity is not None else "Unavailable")
            st.caption("This is a quote-size sensitivity signal from three RPC quotes, not an independently verified price-impact or MEV measurement. It compares output-per-input across the tested sizes using the same token pair and fee tier.")
        except Exception as exc:
            probe_error = str(exc)
            st.warning(f"Quote succeeded, but size-sensitivity probes were incomplete: {probe_error}")

        try:
            with st.spinner("Fetching external reference prices..."):
                ids = sorted({TOKENS[token_in]["id"], TOKENS[token_out]["id"]})
                prices, reference_time = reference_prices(ids)
                in_id, out_id = TOKENS[token_in]["id"], TOKENS[token_out]["id"]
                if in_id not in prices or out_id not in prices:
                    raise RuntimeError("External reference price unavailable for one or both tokens.")
                input_price, output_price = prices[in_id], prices[out_id]
                expected = float(amount) * input_price / output_price
                if expected <= 0:
                    raise RuntimeError("Reference output is not positive.")
                deviation = (float(output) - expected) / expected * 100
            st.subheader("External reference-price comparison")
            p, q, r = st.columns(3)
            p.metric("Approx. reference output", f"{expected:,.8f} {token_out}")
            q.metric("Quote deviation", f"{deviation:+.3f}%")
            r.metric("Reference prices", f"${input_price:,.4f} / ${output_price:,.4f}")
            st.caption(f"Reference prices retrieved at {reference_time}. The provider response used here does not supply a market-observation timestamp; this is retrieval time only. Results may be cached for up to 20 seconds.")
            st.subheader("Reference-data quality checks")
            quality_notes = []
            if input_price <= 0 or output_price <= 0:
                quality_notes.append("Reference price is zero or negative; do not rely on this comparison.")
                st.error(quality_notes[-1])
            elif abs(deviation) >= 5:
                quality_notes.append("Quote/reference difference is 5% or more. Verify token mapping, pool route, liquidity, and reference data before interpreting it.")
                st.warning(quality_notes[-1])
            elif abs(deviation) >= 2:
                quality_notes.append("Quote/reference difference is 2% or more. This may reflect market movement, fees, liquidity, or reference-data mismatch.")
                st.warning(quality_notes[-1])
            else:
                quality_notes.append("Reference values are positive and quote/reference difference is below 2%; this is a consistency check, not proof that either price is correct.")
                st.info(quality_notes[-1])
            score, label, reasons = assess(deviation, slippage, size_sensitivity)
            st.subheader("Pre-trade heuristic indicators")
            st.metric("Heuristic indicator score", f"{score}/100", label)
            if score >= 50:
                st.error("Several indicators warrant extra review. This is not a verified threat alert.")
            elif score >= 25:
                st.warning("Review reference price, pool liquidity, fee tier, quote-size sensitivity, and slippage settings.")
            else:
                st.info("The simple indicators are relatively low for this snapshot; this is not a safety guarantee.")
            st.write("**Reasons considered**")
            for reason in reasons:
                st.write("- " + reason)
            st.caption("V4.4 uses reference-price deviation, selected slippage, and quote-size sensitivity only. Reference-data checks are sanity checks, not independent validation. It does not inspect liquidity depth directly, pending transactions, sandwich patterns, private order flow, or attack probability.")
            report = {
                "version": "V4.4",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "pair": f"{token_in}->{token_out}",
                "pool_fee_tier": fee_label,
                "input_amount": str(amount),
                "slippage_percent": slippage,
                "estimated_output": str(output),
                "output_token": token_out,
                "illustrative_minimum_output": str(minimum),
                "quoter_gas_estimate": gas,
                "quote_fetched_at_utc": quote_time.isoformat(timespec="seconds"),
                "reference_retrieved_at_utc": reference_time,
                "reference_prices_usd": {token_in: input_price, token_out: output_price},
                "approx_reference_output": expected,
                "quote_deviation_percent": deviation,
                "quote_size_sensitivity_percent": size_sensitivity,
                "heuristic_score": score,
                "heuristic_label": label,
                "reference_quality_notes": quality_notes,
                "limitations": ["No wallet connection", "No transaction signing or submission", "No live mempool monitoring", "No verified MEV attack detection or prevention", "Reference timestamp is retrieval time, not market-observation time"]
            }
            st.download_button("Download test report (JSON)", data=json.dumps(report, indent=2), file_name="anti_mev_v4_4_test_report.json", mime="application/json")
        except Exception as exc:
            st.warning(f"Quote and size probes succeeded, but external-reference indicators could not be calculated: {exc}")

        st.subheader("Interpretation")
        st.write("V4.4 adds reference-data sanity checks, an exportable JSON test report, quote-size sensitivity, and retrieval timestamps. It remains a transparent rule-based prototype, not AI-based detection. It does not submit transactions or prove MEV prevention.")
        st.caption(f"Pair: {token_in} → {token_out} • Fee tier: {fee_label} • Slippage: {slippage:.1f}%")
    except (InvalidOperation, ValueError, OverflowError) as exc:
        st.error(f"Enter a valid positive amount and valid HTTPS RPC URL. Details: {exc}")
    except Exception as exc:
        st.error(f"Quote request failed: {exc}")
        st.caption("Check the detailed error, RPC endpoint, selected pair, and whether the fee-tier pool exists.")

st.divider()
st.subheader("Read-only transaction lookup (optional)")
tx_hash = st.text_input("Ethereum transaction hash", placeholder="0x…")
if st.button("Inspect transaction", use_container_width=True):
    clean_hash = tx_hash.strip()
    if len(clean_hash) != 66 or not clean_hash.startswith("0x") or any(char not in "0123456789abcdefABCDEF" for char in clean_hash[2:]):
        st.error("Enter a transaction hash: 0x followed by 64 hexadecimal characters.")
    else:
        try:
            tx = rpc_call(rpc.strip(), "eth_getTransactionByHash", [clean_hash])
            receipt = rpc_call(rpc.strip(), "eth_getTransactionReceipt", [clean_hash])
            if not tx:
                st.warning("Transaction not found by this RPC.")
            else:
                st.json({"hash": tx.get("hash"), "from": tx.get("from"), "to": tx.get("to"), "value_wei": tx.get("value"), "block_number": tx.get("blockNumber"), "receipt_status": receipt.get("status") if receipt else "pending or unavailable"})
                st.caption("Read-only RPC data only; not a full security audit.")
        except Exception as exc:
            st.error(f"Transaction lookup failed: {exc}")

st.divider()
st.caption("Anti-MEV Shield V4.4 • Experimental proof of concept • No wallet connection, signing, transaction submission, private relay, or guaranteed MEV protection.")
