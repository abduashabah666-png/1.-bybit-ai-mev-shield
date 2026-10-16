import streamlit as st
import requests
from decimal import Decimal, InvalidOperation

st.set_page_config(page_title="Anti-MEV Shield V4.1", page_icon="🛡️", layout="wide")

DEFAULT_RPC = "https://ethereum-rpc.publicnode.com"
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

TOKENS = {
    "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18, "id": "ethereum"},
    "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6, "id": "usd-coin"},
    "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6, "id": "tether"},
    "DAI":  {"address": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18, "id": "dai"},
}
FEES = {"0.01%": 100, "0.05%": 500, "0.30%": 3000, "1.00%": 10000}

st.title("🛡️ Anti-MEV Shield V4.1")
st.caption("Read-only Ethereum / Uniswap V3 quote analysis with external reference-price comparison.")
st.warning(
    "Experimental prototype only. It does not connect to a wallet, sign or send transactions, monitor the public mempool, "
    "use a private relay, or guarantee MEV prevention. Quote deviation is not a verified MEV score or full price-impact calculation."
)

def rpc_call(url, method, params):
    response = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        err = data["error"]
        raise RuntimeError(f"RPC error {err.get('code', '')}: {err.get('message', str(err))}")
    return data.get("result")

def uint_word(value):
    return int(value).to_bytes(32, byteorder="big").hex()

def address_word(address):
    clean = address.lower().removeprefix("0x")
    if len(clean) != 40:
        raise ValueError("Invalid token address")
    return clean.rjust(64, "0")

def quote_calldata(token_in, token_out, fee, amount):
    # Correct ABI for QuoterV2 quoteExactInputSingle((address,address,uint256,uint24,uint160)):
    # selector + 5 static tuple words. The selector for this tuple signature is 0xc6a5026a.
    return (
        "0xc6a5026a"
        + address_word(token_in)
        + address_word(token_out)
        + uint_word(amount)
        + uint_word(fee)
        + uint_word(0)
    )

def decode_words(result):
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RuntimeError(f"RPC returned an unexpected result: {result!r}")
    raw = result[2:]
    if len(raw) < 64:
        raise RuntimeError("QuoterV2 returned too little data. The selected pool may not exist or the call reverted.")
    if len(raw) % 64 != 0:
        raise RuntimeError("QuoterV2 returned malformed data.")
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw), 64)]

@st.cache_data(ttl=20, show_spinner=False)
def reference_prices(ids):
    response = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ",".join(ids), "vs_currencies": "usd"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    return {coin_id: float(data[coin_id]["usd"]) for coin_id in ids if coin_id in data and "usd" in data[coin_id]}

with st.sidebar:
    st.header("Connection")
    rpc = st.text_input("Ethereum Mainnet RPC URL", DEFAULT_RPC)
    st.caption("Use a trusted RPC. Never enter a seed phrase or private key.")
    if st.button("Test RPC connection", use_container_width=True):
        try:
            chain = rpc_call(rpc.strip(), "eth_chainId", [])
            block = rpc_call(rpc.strip(), "eth_blockNumber", [])
            chain_id = int(chain, 16)
            if chain_id != 1:
                st.error(f"Connected, but chain ID is {chain_id}; Ethereum Mainnet (1) is required.")
            else:
                st.success(f"Connected • Ethereum Mainnet • Block {int(block, 16):,}")
        except Exception as exc:
            st.error(f"Connection failed: {exc}")

st.subheader("Uniswap V3 indicative quote")
col1, col2, col3 = st.columns(3)
with col1:
    token_in_symbol = st.selectbox("Input token", list(TOKENS), index=0)
with col2:
    token_out_symbol = st.selectbox("Output token", list(TOKENS), index=1)
with col3:
    fee_label = st.selectbox("Pool fee tier", list(FEES), index=2)

amount_text = st.text_input("Input amount", "0.1")
slippage = st.slider("Illustrative slippage tolerance (%)", 0.1, 5.0, 0.5, 0.1)

if token_in_symbol == token_out_symbol:
    st.info("Choose two different tokens.")
else:
    try:
        amount = Decimal(amount_text)
        if not amount.is_finite() or amount <= 0:
            raise InvalidOperation
        raw_amount = int(amount * (Decimal(10) ** TOKENS[token_in_symbol]["decimals"]))
        if raw_amount <= 0:
            raise InvalidOperation

        if st.button("Get quote & analyze", type="primary", use_container_width=True):
            try:
                with st.spinner("Querying Ethereum RPC..."):
                    chain = rpc_call(rpc.strip(), "eth_chainId", [])
                    if int(chain, 16) != 1:
                        raise RuntimeError("The RPC endpoint is not connected to Ethereum Mainnet (chain ID 1).")

                    call_data = quote_calldata(
                        TOKENS[token_in_symbol]["address"],
                        TOKENS[token_out_symbol]["address"],
                        FEES[fee_label],
                        raw_amount,
                    )
                    result = rpc_call(
                        rpc.strip(),
                        "eth_call",
                        [{"to": QUOTER_V2, "data": call_data}, "latest"],
                    )
                    words = decode_words(result)
                    output = Decimal(words[0]) / (Decimal(10) ** TOKENS[token_out_symbol]["decimals"])
                    min_output = output * (Decimal(1) - Decimal(str(slippage)) / Decimal(100))
                    gas_estimate = words[3] if len(words) > 3 else None

                st.success("Indicative quote retrieved from the configured Ethereum RPC.")
                x, y, z = st.columns(3)
                x.metric("Estimated output", f"{output:,.6f} {token_out_symbol}")
                y.metric("Illustrative minimum output", f"{min_output:,.6f} {token_out_symbol}")
                z.metric("Quoter gas estimate", f"{gas_estimate:,}" if gas_estimate is not None else "Unavailable")

                try:
                    with st.spinner("Fetching external reference prices..."):
                        ids = sorted({TOKENS[token_in_symbol]["id"], TOKENS[token_out_symbol]["id"]})
                        prices = reference_prices(ids)
                        input_id = TOKENS[token_in_symbol]["id"]
                        output_id = TOKENS[token_out_symbol]["id"]
                        if input_id not in prices or output_id not in prices:
                            raise RuntimeError("CoinGecko did not return both reference prices.")
                        input_price = prices[input_id]
                        output_price = prices[output_id]
                        expected = float(amount) * input_price / output_price
                        if expected <= 0:
                            raise RuntimeError("Reference output is not positive.")
                        deviation = (float(output) - expected) / expected * 100

                    st.subheader("External reference-price comparison")
                    p, q, r = st.columns(3)
                    p.metric("Approx. reference output", f"{expected:,.6f} {token_out_symbol}")
                    q.metric("Quote deviation", f"{deviation:+.3f}%")
                    r.metric("Reference prices", f"${input_price:,.4f} / ${output_price:,.4f}")
                    if abs(deviation) >= 2:
                        st.warning("The quote differs by at least 2% from the external reference snapshot. Check liquidity, fee tier, market timing and token prices.")
                    else:
                        st.info("The quote is within 2% of this reference snapshot. This does not prove the trade is safe or MEV-free.")
                    st.caption("Deviation = (Uniswap quote output − reference-price output) ÷ reference-price output × 100. This is not pure price impact.")
                except Exception as price_exc:
                    st.warning(f"Quote succeeded, but reference-price comparison failed: {price_exc}")

                st.subheader("Interpretation")
                st.write(
                    "This compares one Uniswap V3 pool quote with an external public USD price snapshot. "
                    "It does not simulate a full route, independently calculate price impact, estimate sandwich-attack probability, or prove MEV prevention."
                )
                st.caption(
                    f"Pair: {token_in_symbol} → {token_out_symbol} • Fee tier: {fee_label} • "
                    f"Slippage setting: {slippage:.1f}%"
                )
            except Exception as exc:
                st.error(f"Quote request failed: {exc}")
                st.caption("The detailed error above should help identify whether this is an RPC issue, a contract-call revert, or a missing pool.")
    except (InvalidOperation, ValueError, OverflowError):
        st.error("Enter a valid positive amount, for example 0.1.")

st.divider()
st.subheader("Read-only transaction lookup (optional)")
tx_hash = st.text_input("Ethereum transaction hash", placeholder="0x…")
if st.button("Inspect transaction", use_container_width=True):
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        st.error("Enter a transaction hash: 0x followed by 64 hexadecimal characters.")
    else:
        try:
            tx = rpc_call(rpc.strip(), "eth_getTransactionByHash", [tx_hash.strip()])
            receipt = rpc_call(rpc.strip(), "eth_getTransactionReceipt", [tx_hash.strip()])
            if not tx:
                st.warning("Transaction not found by this RPC.")
            else:
                st.json({
                    "hash": tx.get("hash"),
                    "from": tx.get("from"),
                    "to": tx.get("to"),
                    "value_wei": tx.get("value"),
                    "block_number": tx.get("blockNumber"),
                    "receipt_status": receipt.get("status") if receipt else "pending or unavailable",
                })
                st.caption("Read-only RPC data only; not a full security audit.")
        except Exception as exc:
            st.error(f"Transaction lookup failed: {exc}")

st.divider()
st.caption(
    "Anti-MEV Shield V4.1 • Experimental proof of concept • No wallet connection, signing, transaction submission, private relay, or guaranteed MEV protection."
)
