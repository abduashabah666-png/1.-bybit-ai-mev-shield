import streamlit as st
import requests
from decimal import Decimal, InvalidOperation

st.set_page_config(page_title="Anti-MEV Shield V4.2", page_icon="🛡️", layout="wide")
DEFAULT_RPC="https://ethereum-rpc.publicnode.com"
QUOTER_V2="0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
TOKENS={
"WETH":{"address":"0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2","decimals":18,"id":"ethereum"},
"USDC":{"address":"0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48","decimals":6,"id":"usd-coin"},
"USDT":{"address":"0xdAC17F958D2ee523a2206206994597C13D831ec7","decimals":6,"id":"tether"},
"DAI":{"address":"0x6B175474E89094C44Da98b954EedeAC495271d0F","decimals":18,"id":"dai"}}
FEES={"0.01%":100,"0.05%":500,"0.30%":3000,"1.00%":10000}

st.title("🛡️ Anti-MEV Shield V4.2")
st.caption("Read-only quote comparison and heuristic pre-trade risk indicators.")
st.warning("Experimental prototype only. It does not connect to a wallet, sign or submit transactions, monitor the live mempool, use a private relay, or guarantee MEV prevention. Risk labels are heuristic indicators, not verified attack detection.")

def rpc_call(url, method, params):
    r=requests.post(url,json={"jsonrpc":"2.0","id":1,"method":method,"params":params},timeout=20)
    r.raise_for_status()
    d=r.json()
    if "error" in d:
        e=d["error"]; raise RuntimeError(f"RPC error {e.get('code','')}: {e.get('message',str(e))}")
    return d.get("result")

def uint_word(v): return int(v).to_bytes(32,"big").hex()
def address_word(a):
    a=a.lower().removeprefix("0x")
    if len(a)!=40: raise ValueError("Invalid token address")
    return a.rjust(64,"0")
def quote_calldata(ti,to,fee,amount):
    return "0xc6a5026a"+address_word(ti)+address_word(to)+uint_word(amount)+uint_word(fee)+uint_word(0)
def decode_words(result):
    if not isinstance(result,str) or not result.startswith("0x"): raise RuntimeError(f"Unexpected RPC result: {result!r}")
    raw=result[2:]
    if len(raw)<64 or len(raw)%64: raise RuntimeError("Unexpected QuoterV2 response; pool may be unavailable or call reverted.")
    return [int(raw[i:i+64],16) for i in range(0,len(raw),64)]

@st.cache_data(ttl=20,show_spinner=False)
def reference_prices(ids):
    r=requests.get("https://api.coingecko.com/api/v3/simple/price",params={"ids":",".join(ids),"vs_currencies":"usd"},timeout=15)
    r.raise_for_status(); d=r.json()
    return {i:float(d[i]["usd"]) for i in ids if i in d and "usd" in d[i]}

def assess(deviation, slippage):
    score=0; reasons=[]
    if abs(deviation)>=5: score+=45; reasons.append("Quote differs from reference by at least 5%.")
    elif abs(deviation)>=2: score+=25; reasons.append("Quote differs from reference by at least 2%.")
    elif abs(deviation)>=1: score+=10; reasons.append("Quote differs from reference by at least 1%.")
    else: reasons.append("Quote deviation from this reference snapshot is below 1%.")
    if slippage>=3: score+=25; reasons.append("Selected slippage tolerance is high (3% or more).")
    elif slippage>=1: score+=12; reasons.append("Selected slippage tolerance is at least 1%.")
    else: reasons.append("Selected slippage tolerance is below 1%.")
    label="High indicator level" if score>=50 else "Moderate indicator level" if score>=25 else "Lower indicator level"
    return min(score,100),label,reasons

with st.sidebar:
    st.header("Connection")
    rpc=st.text_input("Ethereum Mainnet RPC URL",DEFAULT_RPC)
    st.caption("Never enter a seed phrase or private key.")
    if st.button("Test RPC connection",use_container_width=True):
        try:
            chain=rpc_call(rpc.strip(),"eth_chainId",[]); block=rpc_call(rpc.strip(),"eth_blockNumber",[])
            if int(chain,16)!=1: st.error(f"Connected to chain ID {int(chain,16)}; Ethereum Mainnet (1) is required.")
            else: st.success(f"Connected • Ethereum Mainnet • Block {int(block,16):,}")
        except Exception as e: st.error(f"Connection failed: {e}")

st.subheader("Uniswap V3 indicative quote")
a,b,c=st.columns(3)
with a: tin=st.selectbox("Input token",list(TOKENS),index=0)
with b: tout=st.selectbox("Output token",list(TOKENS),index=1)
with c: fee_label=st.selectbox("Pool fee tier",list(FEES),index=2)
amount_text=st.text_input("Input amount","0.1")
slippage=st.slider("Illustrative slippage tolerance (%)",0.1,5.0,0.5,0.1)

if tin==tout: st.info("Choose two different tokens.")
else:
    try:
        amount=Decimal(amount_text)
        if not amount.is_finite() or amount<=0: raise InvalidOperation
        amount_raw=int(amount*(Decimal(10)**TOKENS[tin]["decimals"]))
        if amount_raw<=0: raise InvalidOperation
        if st.button("Get quote & analyze",type="primary",use_container_width=True):
            try:
                with st.spinner("Fetching quote..."):
                    chain=rpc_call(rpc.strip(),"eth_chainId",[])
                    if int(chain,16)!=1: raise RuntimeError("RPC endpoint is not on Ethereum Mainnet (chain ID 1).")
                    data=quote_calldata(TOKENS[tin]["address"],TOKENS[tout]["address"],FEES[fee_label],amount_raw)
                    result=rpc_call(rpc.strip(),"eth_call",[{"to":QUOTER_V2,"data":data},"latest"])
                    words=decode_words(result)
                    output=Decimal(words[0])/(Decimal(10)**TOKENS[tout]["decimals"])
                    minimum=output*(Decimal(1)-Decimal(str(slippage))/Decimal(100))
                    gas=words[3] if len(words)>3 else None
                st.success("Indicative quote retrieved from the configured Ethereum RPC.")
                x,y,z=st.columns(3)
                x.metric("Estimated output",f"{output:,.6f} {tout}")
                y.metric("Illustrative minimum output",f"{minimum:,.6f} {tout}")
                z.metric("Quoter gas estimate",f"{gas:,}" if gas is not None else "Unavailable")
                try:
                    with st.spinner("Fetching reference prices..."):
                        ids=sorted({TOKENS[tin]["id"],TOKENS[tout]["id"]})
                        prices=reference_prices(ids); iid=TOKENS[tin]["id"]; oid=TOKENS[tout]["id"]
                        if iid not in prices or oid not in prices: raise RuntimeError("External reference price unavailable.")
                        pin,pout=prices[iid],prices[oid]
                        expected=float(amount)*pin/pout
                        if expected<=0: raise RuntimeError("Reference output is not positive.")
                        deviation=(float(output)-expected)/expected*100
                    st.subheader("External reference-price comparison")
                    p,q,r=st.columns(3)
                    p.metric("Approx. reference output",f"{expected:,.6f} {tout}")
                    q.metric("Quote deviation",f"{deviation:+.3f}%")
                    r.metric("Reference prices",f"${pin:,.4f} / ${pout:,.4f}")
                    st.caption("Deviation is a rough comparison with a public price snapshot; it is not pure price impact.")
                    st.subheader("Pre-trade risk indicators")
                    score,label,reasons=assess(deviation,slippage)
                    st.metric("Heuristic indicator score",f"{score}/100",label)
                    if score>=50: st.error("Several indicators suggest extra review before any trade.")
                    elif score>=25: st.warning("Review price, liquidity, fee tier and slippage settings.")
                    else: st.info("Simple indicators are relatively low for this snapshot; this is not a safety guarantee.")
                    st.write("**Reasons considered**")
                    for reason in reasons: st.write("- "+reason)
                    st.caption("This transparent rule-based score uses only reference-price deviation and selected slippage. It does not inspect liquidity depth, pending transactions, sandwich patterns, or attack probability.")
                except Exception as e: st.warning(f"Quote succeeded, but risk indicators could not be calculated: {e}")
                st.subheader("Interpretation")
                st.write("This version adds a rule-based indicator, not AI-based detection. It does not simulate a full route, independently calculate price impact, monitor mempool activity, or prove MEV prevention.")
                st.caption(f"Pair: {tin} → {tout} • Fee tier: {fee_label} • Slippage: {slippage:.1f}%")
            except Exception as e:
                st.error(f"Quote request failed: {e}")
                st.caption("Check the detailed error, RPC endpoint, selected pair and whether the fee-tier pool exists.")
    except (InvalidOperation,ValueError,OverflowError): st.error("Enter a valid positive amount, for example 0.1.")

st.divider()
st.subheader("Read-only transaction lookup (optional)")
tx_hash=st.text_input("Ethereum transaction hash",placeholder="0x…")
if st.button("Inspect transaction",use_container_width=True):
    if not tx_hash.startswith("0x") or len(tx_hash)!=66: st.error("Enter a transaction hash: 0x followed by 64 hexadecimal characters.")
    else:
        try:
            tx=rpc_call(rpc.strip(),"eth_getTransactionByHash",[tx_hash.strip()])
            receipt=rpc_call(rpc.strip(),"eth_getTransactionReceipt",[tx_hash.strip()])
            if not tx: st.warning("Transaction not found by this RPC.")
            else:
                st.json({"hash":tx.get("hash"),"from":tx.get("from"),"to":tx.get("to"),"value_wei":tx.get("value"),"block_number":tx.get("blockNumber"),"receipt_status":receipt.get("status") if receipt else "pending or unavailable"})
                st.caption("Read-only RPC data only; not a full security audit.")
        except Exception as e: st.error(f"Transaction lookup failed: {e}")

st.divider()
st.caption("Anti-MEV Shield V4.2 • Experimental proof of concept • No wallet connection, signing, transaction submission, private relay, or guaranteed MEV protection.")
