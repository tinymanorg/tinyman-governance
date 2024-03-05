# This sample is provided for demonstration purposes only.
# It is not intended for production use.
# This example does not constitute trading advice.
from pprint import pprint
from urllib.parse import quote_plus

from tinyman.assets import AssetAmount

from examples.v2.tutorial.common import get_account, get_assets
from examples.v2.utils import get_algod
from tinyman.v2.client import TinymanV2TestnetClient
from tinyman.compat import AssetTransferTxn

account = get_account()
algod = get_algod()
client = TinymanV2TestnetClient(algod_client=algod, user_address=account["address"])

ASSET_A_ID, ASSET_B_ID = get_assets()["ids"]
ASSET_A = client.fetch_asset(ASSET_A_ID)
ASSET_B = client.fetch_asset(ASSET_B_ID)
pool = client.fetch_pool(ASSET_A_ID, ASSET_B_ID)

position = pool.fetch_pool_position()

quote = pool.fetch_flash_loan_quote(
    loan_amount_a=AssetAmount(pool.asset_1, 1_000_000),
    loan_amount_b=AssetAmount(pool.asset_2, 0),
)

print("\nQuote:")
print(quote)

account_info = algod.account_info(account["address"])

for asset in account_info["assets"]:
    if asset["asset-id"] == pool.asset_1.id:
        asset_1_balance = asset["amount"]

# Transfer amount is equal to sum of initial account balance and loan amount
# this transaction demonstrates that you can use the total amount
transactions = [
    AssetTransferTxn(
        sender=account["address"],
        sp=algod.suggested_params(),
        receiver=account["address"],
        amt=asset_1_balance + quote.amounts_out[pool.asset_1].amount,
        index=pool.asset_1.id,
    )
]

txn_group = pool.prepare_flash_loan_transactions_from_quote(
    quote=quote, transactions=transactions
)

# Sign
txn_group.sign_with_private_key(account["address"], account["private_key"])

# Submit transactions to the network and wait for confirmation
txn_info = client.submit(txn_group, wait=True)
print("Transaction Info")
pprint(txn_info)

print(
    f"Check the transaction group on Algoexplorer: https://testnet.algoexplorer.io/tx/group/{quote_plus(txn_group.id)}"
)
