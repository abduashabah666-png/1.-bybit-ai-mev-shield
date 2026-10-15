import os
from datetime import datetime, timezone

import requests
import streamlit as st

st.set_page_config(page_title="Anti-MEV Shield", page_icon="🛡️", layout="wide")

PUBLIC_RPC_DEFAULT = "https://ethereum-rpc.publicnode.com"
REQUEST_TIMEOUT = 15
UNISWAP_V3_QUOTER_V2_ETH = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
QUOTER_V2_QUOTE_EXACT_INPUT_SINGLE_SELECTOR = "0xc6a5026a"

TOKENS = {
    "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18},
    "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6},
    "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6},
    "DAI": {"address": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18},
}


def rpc_call(endpoint: str, method: str, params=None):
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
        raise RuntimeError(payload["error"].get("message", str(payload["error"])))
    if "result" not in payload:
        raise RuntimeError("RPC response did not contain a result.")
    return payload["result"]


def hex_int(value):
    if value is None:
        return None
    try:
        return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)
    except (ValueError, TypeError):
        return None


def word(value: int) -> str:
    return format(value, "064x")


def address_word(address: str) -> str:
    clean = address.lower().removeprefix("0x")
    if len(clean) != 40:
        raise ValueError("Invalid EVM address length.")
    int(clean, 16)
    return clean.rjust(64, "0")


def encode_quote(token_in, token_out, fee, amount_in):
    # ABI-encode QuoterV2's quoteExactInputSingle tuple parameter.
    tuple_data = (
        address_word(token_in)
        + address_word(token_out)
        + word(amount_in)
        + word(fee)
        + word(0)
    )
    # This tuple is fully static, so ABI encoding is inline (no offset word).
    return QUOTER_V2_QUOTE_EXACT_INPUT_SINGLE_SELECTOR + tuple_data


def quote_swap(rpc, token_in, token_out, amount_in_raw, fee):
    data = encode_quote(token_in, token_out, fee, amount_in_raw)
    result = rpc_call(
        rpc,
        "eth_call",
        [{"to": UNISWAP_V3_QUOTER_V2_ETH, "data": data}, "latest"],
    )
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RuntimeError("Unexpected quote response.")
    raw = result[2:]
    if len(raw) < 64 * 4:
        raise RuntimeError("The pool may not exist at this fee tier, or the RPC returned an unexpected quote.")
    words = [raw[i:i+64] for i in range(0, 64 * 4, 64)]
    return {
        "amount_out_raw": int(words[0], 16),
        "initialized_ticks_crossed": int(words[2], 16),
        "gas_estimate": int(words[3], 16),
    }


def fmt_num(n, decimals=8):
    return f"{n:,.{decimals}f}"


st.title("🛡️ Anti-MEV Shield")
st.caption("Ethereum swap review prototype • Read-only quote • No wallet keys collected")

st.warning(
    "This version requests a read-only Uniswap V3 quote from Ethereum Mainnet. "
    "It does not connect a wallet, sign or send transactions, continuously monitor the pending mempool, "
    "or guarantee MEV prevention."
)

with st.sidebar:
    st.header("Ethereum connection")
    rpc_url = st.text_input(
        "Ethereum RPC endpoint",
        value=os.getenv("ETH_RPC_URL", PUBLIC_RPC_DEFAULT),
        help="Read-only RPC endpoint. Never enter a seed phrase or private key here.",
    )
    st.caption("Never enter a seed phrase, private key, or wallet password.")

tab_swap, tab_health, tab_limits = st.tabs(
    ["Swap review", "Connection check", "What this version does"]
)

with tab_swap:
    st.subheader("Get a live indicative swap quote")
    st.write(
        "Request an indicative Uniswap V3 quote for common Ethereum tokens. "
        "This is not a transaction preview and does not guarantee the amount received."
    )
    left, right = st.columns(2)
    with left:
        token_in_symbol = st.selectbox("You pay", ["WETH", "USDC", "USDT", "DAI"], index=0)
        token_in = TOKENS[token_in_symbol]
        amount_in = st.number_input(
            f"Amount of {token_in_symbol}",
            min_value=0.000001,
            value=0.1 if token_in_symbol == "WETH" else 100.0,
            step=0.1,
            format="%.6f",
        )
    with right:
        outputs = [symbol for symbol in TOKENS if symbol != token_in_symbol]
        default_out = "USDC" if "USDC" in outputs else outputs[0]
        token_out_symbol = st.selectbox("You receive", outputs, index=outputs.index(default_out))
        token_out = TOKENS[token_out_symbol]
        fee_tier = st.selectbox(
            "Uniswap V3 pool fee tier",
            options=[100, 500, 3000, 10000],
            format_func=lambda x: {100: "0.01%", 500: "0.05%", 3000: "0.30%", 10000: "1.00%"}[x],
            index=2,
        )

    st.subheader("Review settings")
    c1, c2 = st.columns(2)
    with c1:
        slippage_pct = st.number_input(
            "Slippage tolerance (%)", min_value=0.1, max_value=50.0,
            value=0.5, step=0.1, format="%.2f"
        )
    with c2:
        impact_threshold = st.number_input(
            "Price-impact warning threshold (%)", min_value=0.1, max_value=50.0,
            value=2.0, step=0.1, format="%.2f"
        )

    if st.button("Fetch live quote and review", type="primary"):
        try:
            chain_id = hex_int(rpc_call(rpc_url, "eth_chainId"))
            if chain_id != 1:
                raise RuntimeError(f"This setup is for Ethereum Mainnet (chain ID 1); RPC reported {chain_id}.")
            amount_in_raw = int(round(amount_in * (10 ** token_in["decimals"])))
            quote = quote_swap(
                rpc_url, token_in["address"], token_out["address"], amount_in_raw, fee_tier
            )
            amount_out = quote["amount_out_raw"] / (10 ** token_out["decimals"])
            minimum_out = amount_out * (1 - slippage_pct / 100)

            st.success("Indicative quote retrieved from the configured Ethereum RPC.")
            a, b, c = st.columns(3)
            a.metric("Estimated output", f"{fmt_num(amount_out)} {token_out_symbol}")
            b.metric("Minimum output at selected slippage", f"{fmt_num(minimum_out)} {token_out_symbol}")
            c.metric("Quoter gas estimate", f'{quote["gas_estimate"]:,}')

            st.info(
                "Minimum output is calculated from this quote and your slippage setting. "
                "It is not a guarantee. A real transaction also requires checking the actual router, route, "
                "deadline, calldata, fees, and wallet confirmation."
            )
            if slippage_pct >= 3:
                st.error("High slippage setting: review carefully before any real swap.")
            elif slippage_pct >= 1.5:
                st.warning("Elevated slippage setting: consider whether this tolerance is necessary.")

            st.warning(
                "Price impact is not independently calculated in this prototype because that requires "
                "a reliable reference price and pool-state analysis. This quote alone is not a complete MEV risk check."
            )
            st.caption(f'Quote time (UTC): {datetime.now(timezone.utc).isoformat()}')
        except Exception as exc:
            st.error(f"Could not fetch a live swap quote: {exc}")
            st.caption(
                "Possible causes: RPC restrictions, no pool at the selected fee tier, unsupported eth_call, "
                "or provider rate limits. Try another fee tier or a different public RPC endpoint."
            )

with tab_health:
    st.subheader("Check the Ethereum RPC")
    if st.button("Check connection"):
        try:
            chain_id = hex_int(rpc_call(rpc_url, "eth_chainId"))
            latest = hex_int(rpc_call(rpc_url, "eth_blockNumber"))
            if chain_id == 1:
                st.success("Connected to an endpoint reporting Ethereum Mainnet.")
            else:
                st.error(f"RPC reported chain ID {chain_id}; expected 1 for Ethereum Mainnet.")
            st.metric("Chain ID", str(chain_id))
            st.metric("Latest reported block", f"{latest:,}" if latest is not None else "Unknown")
        except Exception as exc:
            st.error(f"Connection check failed: {exc}")

with tab_limits:
    st.subheader("Current capabilities and limits")
    st.markdown(
        """
- **Implemented:** RPC connection check and a read-only quote request against Uniswap V3 QuoterV2 for common Ethereum tokens.
- **Quote results:** indicative output, simple minimum-output estimate based on chosen slippage, and Quoter gas estimate.
- **Not implemented:** wallet connection, wallet-based signing, transaction submission, private relay integration, continuous pending-transaction monitoring, independent price-impact calculation, or MEV detection.
- **No guarantee:** a private route may reduce public-mempool exposure for supported flows but cannot guarantee protection against every MEV strategy.
- **Safety:** never enter a seed phrase or private key into this app. Do not submit a transaction based solely on this prototype.
        """
    )

st.divider()
st.caption("Anti-MEV Shield prototype • Read-only quote only • No signing or transaction submission")
