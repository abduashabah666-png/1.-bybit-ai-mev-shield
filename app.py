import streamlit as st
import requests
from decimal import Decimal, InvalidOperation

st.set_page_config(page_title='Anti-MEV Shield V4', page_icon='🛡️', layout='wide')
DEFAULT_RPC = 'https://ethereum-rpc.publicnode.com'
QUOTER_V2 = '0x61fFE014bA17989E743c5F6cB21bF9697530B21e'
TOKENS = {
 'WETH': {'address':'0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2','decimals':18,'id':'ethereum'},
 'USDC': {'address':'0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48','decimals':6,'id':'usd-coin'},
 'USDT': {'address':'0xdAC17F958D2ee523a2206206994597C13D831ec7','decimals':6,'id':'tether'},
 'DAI':  {'address':'0x6B175474E89094C44Da98b954EedeAC495271d0F','decimals':18,'id':'dai'},
}
FEES = {'0.01%':100,'0.05%':500,'0.30%':3000,'1.00%':10000}

st.title('🛡️ Anti-MEV Shield V4')
st.caption('Read-only Ethereum / Uniswap V3 quote analysis with external reference-price comparison.')
st.warning('Experimental prototype only. It does not connect to a wallet, sign or send transactions, monitor the public mempool, use a private relay, or guarantee MEV prevention. Quote deviation is not a verified MEV score or full price-impact calculation.')

def rpc_call(url, method, params):
    r = requests.post(url, json={'jsonrpc':'2.0','id':1,'method':method,'params':params}, timeout=20)
    r.raise_for_status(); data = r.json()
    if 'error' in data: raise RuntimeError(data['error'].get('message', str(data['error'])))
    return data.get('result')

def uint_word(v): return int(v).to_bytes(32, 'big').hex()
def address_word(a):
    a = a.lower().removeprefix('0x')
    if len(a) != 40: raise ValueError('Invalid token address')
    return a.rjust(64, '0')
def quote_calldata(token_in, token_out, fee, amount):
    # QuoterV2 quoteExactInputSingle((address,address,uint256,uint24,uint160))
    return '0xc6a5026a' + address_word(token_in) + address_word(token_out) + uint_word(amount) + uint_word(fee) + uint_word(0)
def decode_words(result):
    raw = result[2:] if result.startswith('0x') else result
    if len(raw) < 64: raise RuntimeError('Unexpected response from QuoterV2')
    return [int(raw[i:i+64],16) for i in range(0,len(raw),64)]

@st.cache_data(ttl=20, show_spinner=False)
def reference_prices(ids):
    r = requests.get('https://api.coingecko.com/api/v3/simple/price', params={'ids':','.join(ids),'vs_currencies':'usd'}, timeout=15)
    r.raise_for_status(); data = r.json()
    return {i:float(data[i]['usd']) for i in ids if i in data and 'usd' in data[i]}

with st.sidebar:
    st.header('Connection')
    rpc = st.text_input('Ethereum Mainnet RPC URL', DEFAULT_RPC)
    st.caption('Use a trusted RPC. Never enter a seed phrase or private key.')
    if st.button('Test RPC connection', use_container_width=True):
        try:
            chain = rpc_call(rpc.strip(),'eth_chainId',[])
            block = rpc_call(rpc.strip(),'eth_blockNumber',[])
            st.success(f'Connected • Chain ID {int(chain,16)} • Block {int(block,16):,}')
        except Exception as e: st.error(f'Connection failed: {e}')

st.subheader('Uniswap V3 indicative quote')
a,b,c = st.columns(3)
with a: tin = st.selectbox('Input token', list(TOKENS), index=0)
with b: tout = st.selectbox('Output token', list(TOKENS), index=1)
with c: fee_label = st.selectbox('Pool fee tier', list(FEES), index=2)
amount_text = st.text_input('Input amount', '0.1')
slippage = st.slider('Illustrative slippage tolerance (%)', 0.1, 5.0, 0.5, 0.1)

if tin == tout:
    st.info('Choose two different tokens.')
else:
    try:
        amount = Decimal(amount_text)
        if not amount.is_finite() or amount <= 0: raise InvalidOperation
        raw_amount = int(amount * (Decimal(10) ** TOKENS[tin]['decimals']))
        if raw_amount <= 0: raise InvalidOperation
        if st.button('Get quote & analyze', type='primary', use_container_width=True):
            with st.spinner('Querying Ethereum RPC and reference prices...'):
                try:
                    data = quote_calldata(TOKENS[tin]['address'], TOKENS[tout]['address'], FEES[fee_label], raw_amount)
                    result = rpc_call(rpc.strip(),'eth_call',[{'to':QUOTER_V2,'data':'0x'+data},'latest'])
                    words = decode_words(result)
                    output = Decimal(words[0]) / (Decimal(10) ** TOKENS[tout]['decimals'])
                    min_output = output * (Decimal(1) - Decimal(str(slippage))/Decimal(100))
                    gas = words[3] if len(words) > 3 else None
                    st.success('Indicative quote retrieved from the configured Ethereum RPC.')
                    x,y,z = st.columns(3)
                    x.metric('Estimated output', f'{output:,.6f} {tout}')
                    y.metric('Illustrative minimum output', f'{min_output:,.6f} {tout}')
                    z.metric('Quoter gas estimate', f'{gas:,}' if gas is not None else 'Unavailable')
                    try:
                        ids = list({TOKENS[tin]['id'], TOKENS[tout]['id']})
                        prices = reference_prices(ids)
                        pin, pout = prices[TOKENS[tin]['id']], prices[TOKENS[tout]['id']]
                        expected = float(amount) * pin / pout
                        deviation = (float(output)-expected)/expected*100 if expected else 0
                        st.subheader('External reference-price comparison')
                        p,q,r = st.columns(3)
                        p.metric('Approx. reference output', f'{expected:,.6f} {tout}')
                        q.metric('Quote deviation', f'{deviation:+.3f}%')
                        r.metric('Reference prices', f'${pin:,.4f} / ${pout:,.4f}')
                        if abs(deviation) >= 2:
                            st.warning('The quote differs by at least 2% from the external reference snapshot. Check liquidity, pool fee, market timing and token prices before drawing conclusions.')
                        else:
                            st.info('The quote is within 2% of this reference snapshot. This does not prove the trade is safe or MEV-free.')
                        st.caption('Deviation = (Uniswap quote output − reference-price output) ÷ reference-price output × 100. This is not pure price impact.')
                    except Exception as e: st.warning(f'Quote retrieved, but reference-price comparison failed: {e}')
                    st.subheader('Interpretation')
                    st.write('This compares a single Uniswap V3 pool quote with an external public USD price snapshot. It does not simulate a full route, independently calculate price impact, estimate sandwich-attack probability, or prove MEV prevention.')
                    st.caption(f'Pair: {tin} → {tout} • Fee tier: {fee_label} • Slippage setting: {slippage:.1f}%')
                except Exception as e:
                    st.error(f'Quote request failed: {e}')
                    st.info('Try another fee tier, check the RPC, and confirm that this token pair has liquidity in the selected Uniswap V3 pool.')
    except (InvalidOperation, ValueError, OverflowError): st.error('Enter a valid positive amount, for example 0.1.')

st.divider(); st.subheader('Read-only transaction lookup (optional)')
tx_hash = st.text_input('Ethereum transaction hash', placeholder='0x…')
if st.button('Inspect transaction', use_container_width=True):
    if not tx_hash.startswith('0x') or len(tx_hash) != 66:
        st.error('Enter a valid transaction hash: 0x followed by 64 hexadecimal characters.')
    else:
        try:
            tx = rpc_call(rpc.strip(),'eth_getTransactionByHash',[tx_hash.strip()])
            receipt = rpc_call(rpc.strip(),'eth_getTransactionReceipt',[tx_hash.strip()])
            if not tx: st.warning('Transaction not found by this RPC.')
            else:
                st.json({'hash':tx.get('hash'),'from':tx.get('from'),'to':tx.get('to'),'value_wei':tx.get('value'),'block_number':tx.get('blockNumber'),'receipt_status':receipt.get('status') if receipt else 'pending or unavailable'})
                st.caption('Read-only RPC data only; not a full security audit.')
        except Exception as e: st.error(f'Transaction lookup failed: {e}')

st.divider()
st.caption('Anti-MEV Shield V4 • Experimental proof of concept • No wallet connection, signing, transaction submission, private relay, or guaranteed MEV protection.')
