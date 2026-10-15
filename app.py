import json
import os
from datetime import datetime, timezone

import requests
import streamlit as st

st.set_page_config(page_title="Anti-MEV Shield", page_icon="🛡️", layout="wide")

PUBLIC_RPC_DEFAULT = "https://cloudflare-eth.com"
PRIVATE_RPC_DEFAULT = "https://rpc.flashbots.net"
REQUEST_TIMEOUT = 12


def rpc_call(endpoint: str, method: str, params=None):
    """Call a JSON-RPC endpoint. No wallet keys are used or stored."""
    if params is None:
        params = []
    response = requests.post(
        endpoint.strip(),
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=REQUEST_TIMEOUT,
        headers={"Content-Type": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"].get("message", "JSON-RPC error"))
    if "result" not in payload:
        raise RuntimeError("The RPC response did not include a result.")
    return payload["result"]


def hex_int(value):
    if value is None:
        return None
    try:
        return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)
    except (ValueError, TypeError):
        return None


def wei_to_eth(value):
    number = hex_int(value)
    return number / 10**18 if number is not None else None


def risk_review(amount_usd, slippage_pct, price_impact_pct, path_status, quote_stale):
    """Transparent review-priority heuristic, NOT an attack probability."""
    score = 0
    reasons = []

    if amount_usd >= 100_000:
        score += 20
        reasons.append("High estimated transaction value (≥ $100,000)")
    elif amount_usd >= 25_000:
        score += 10
        reasons.append("Elevated estimated transaction value (≥ $25,000)")

    if slippage_pct >= 3:
        score += 25
        reasons.append("High slippage tolerance (≥ 3%)")
    elif slippage_pct >= 1.5:
        score += 15
        reasons.append("Elevated slippage tolerance (≥ 1.5%)")

    if price_impact_pct >= 5:
        score += 25
        reasons.append("High estimated price impact (≥ 5%)")
    elif price_impact_pct >= 2:
        score += 15
        reasons.append("Elevated estimated price impact (≥ 2%)")

    if path_status == "Public mempool":
        score += 15
        reasons.append("Transaction path is marked as public")
    elif path_status == "Unknown / not verified":
        score += 5
        reasons.append("Submission path has not been verified")

    if quote_stale:
        score += 10
        reasons.append("Quote may be stale; refresh it before proceeding")

    score = min(score, 100)
    if score < 20:
        label = "Lower review priority"
    elif score < 45:
        label = "Moderate review priority"
    elif score < 70:
        label = "High review priority"
    else:
        label = "Very high review priority"

    if not reasons:
        reasons.append("No configured heuristic threshold was triggered")
    return score, label, reasons


st.title("🛡️ Anti-MEV Shield")
st.caption("Ethereum transaction risk review • Private-route readiness • No wallet keys collected")

st.warning(
    "This version reads live Ethereum RPC data and performs a transparent heuristic review. "
    "It does not guarantee MEV prevention. Private submission is not executed by this app yet; "
    "a wallet-signing integration and end-to-end testing are required."
)

with st.sidebar:
    st.header("Network connections")
    public_rpc = st.text_input(
        "Ethereum read RPC endpoint",
        value=os.getenv("ETH_RPC_URL", PUBLIC_RPC_DEFAULT),
        help="Used for reading public Ethereum data. Do not put wallet secrets here.",
    )
    private_rpc = st.text_input(
        "Private submission endpoint",
        value=os.getenv("ETH_PRIVATE_RPC_URL", PRIVATE_RPC_DEFAULT),
        help="Endpoint address only. This version checks connectivity but does not submit transactions.",
    )
    st.caption("Never enter a seed phrase, private key, or wallet password into this app.")

tab_review, tab_live, tab_route = st.tabs(
    ["Risk review", "Live Ethereum data", "Private-route readiness"]
)

with tab_review:
    st.subheader("Review transaction conditions")
    c1, c2 = st.columns(2)
    with c1:
        amount_usd = st.number_input(
            "Estimated transaction value (USD)",
            min_value=0.0, value=25000.0, step=1000.0, format="%.2f"
        )
        slippage_pct = st.number_input(
            "Slippage tolerance (%)", min_value=0.0, max_value=100.0,
            value=1.0, step=0.1, format="%.2f"
        )
    with c2:
        price_impact_pct = st.number_input(
            "Estimated price impact (%)", min_value=0.0, max_value=100.0,
            value=0.5, step=0.1, format="%.2f"
        )
        path_status = st.selectbox(
            "Submission path",
            ["Unknown / not verified", "Public mempool", "Private route verified by user"]
        )
        quote_stale = st.checkbox("Quote may be stale / needs refreshing", value=False)

    if st.button("Analyze risk", type="primary"):
        score, label, reasons = risk_review(
            amount_usd, slippage_pct, price_impact_pct, path_status, quote_stale
        )
        st.session_state["last_risk"] = {
            "score": score, "label": label, "reasons": reasons,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    if "last_risk" in st.session_state:
        result = st.session_state["last_risk"]
        st.metric("Review-priority score (not attack probability)", f'{result["score"]}/100')
        st.progress(result["score"] / 100)
        st.subheader(result["label"])
        st.write("Reasons:")
        for reason in result["reasons"]:
            st.write(f"- {reason}")
        st.caption(f'Analysis time (UTC): {result["timestamp"]}')
        st.info(
            "This score is a simple review heuristic, not a machine-learning model and not proof "
            "that an attack is happening. Confirm values using the actual swap quote and transaction."
        )

with tab_live:
    st.subheader("Read live Ethereum data")
    if st.button("Check Ethereum RPC connection"):
        try:
            chain_id = hex_int(rpc_call(public_rpc, "eth_chainId"))
            block_hex = rpc_call(public_rpc, "eth_blockNumber")
            block_number = hex_int(block_hex)
            if chain_id != 1:
                st.error(f"Connected endpoint returned chain ID {chain_id}; expected Ethereum Mainnet (1).")
            else:
                st.success("Connected to an endpoint reporting Ethereum Mainnet.")
            st.metric("Chain ID", str(chain_id))
            st.metric("Latest reported block", f"{block_number:,}" if block_number is not None else "Unknown")
        except Exception as exc:
            st.error(f"Could not read Ethereum RPC: {exc}")
            st.caption("The endpoint may be unavailable, rate-limited, or require a different URL.")

    st.divider()
    tx_hash = st.text_input(
        "Optional transaction hash",
        placeholder="0x…",
        help="A public transaction hash is not a private key."
    )
    if st.button("Look up transaction"):
        if not tx_hash.startswith("0x") or len(tx_hash) != 66:
            st.error("Enter a valid-looking 32-byte transaction hash (0x plus 64 hex characters).")
        else:
            try:
                tx = rpc_call(public_rpc, "eth_getTransactionByHash", [tx_hash])
                if tx is None:
                    st.info("The endpoint did not find this transaction. It may be unknown to this node.")
                else:
                    block_num = hex_int(tx.get("blockNumber"))
                    value_eth = wei_to_eth(tx.get("value"))
                    gas_price_wei = hex_int(tx.get("gasPrice"))
                    details = {
                        "hash": tx.get("hash"),
                        "from": tx.get("from"),
                        "to": tx.get("to"),
                        "nonce": hex_int(tx.get("nonce")),
                        "value_eth": value_eth,
                        "gas_price_gwei": gas_price_wei / 1e9 if gas_price_wei is not None else None,
                        "block_number": block_num,
                        "status_note": "A null block number commonly indicates the transaction is pending, if returned by this node."
                    }
                    st.json(details)
                    st.caption(
                        "RPC visibility varies by provider. This lookup is not a continuous mempool monitor "
                        "and does not determine whether MEV occurred."
                    )
            except Exception as exc:
                st.error(f"Transaction lookup failed: {exc}")

with tab_route:
    st.subheader("Private-route readiness")
    st.write(
        "A private RPC endpoint can reduce exposure to the public transaction path for supported "
        "transaction types, but it cannot guarantee protection from every MEV strategy."
    )
    st.markdown(
        "- The endpoint field below is only a connection setting.\n"
        "- This version does **not** sign or submit a transaction.\n"
        "- Do not paste signed raw transactions into this demo.\n"
        "- The next integration step is a wallet-based signing flow with explicit user approval."
    )
    if st.button("Check private endpoint connection"):
        try:
            chain_id = hex_int(rpc_call(private_rpc, "eth_chainId"))
            st.success(f"The endpoint responded to eth_chainId. Reported chain ID: {chain_id}")
            if chain_id != 1:
                st.warning("The endpoint did not report Ethereum Mainnet chain ID 1.")
            st.caption(
                "A successful RPC response only confirms that the endpoint answered. It does not prove "
                "private delivery, inclusion, or MEV protection."
            )
        except Exception as exc:
            st.error(f"Private endpoint check failed: {exc}")
            st.caption("The service may be unavailable, rate-limited, or may require another method.")

st.divider()
st.caption(
    "Safety: no wallet connection, no key storage, no signing, no transaction submission, "
    "no continuous pending-transaction scanning, and no guarantee of preventing MEV."
)
