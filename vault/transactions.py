import uuid

from algosdk import transaction
from algosdk.encoding import decode_address
from tinyman.governance.vault.constants import ACCOUNT_POWER_BOX_ARRAY_LEN, TOTAL_POWERS, TOTAL_POWER_BOX_ARRAY_LEN
from tinyman.utils import int_to_bytes

from common.constants import VAULT_APP_ID
from common.utils import get_account_power_index_at, get_total_power_index_at


def prepare_budget_increase_txn(sender, sp, index, foreign_apps=None, boxes=None):
    if foreign_apps is None:
        foreign_apps = []

    if boxes is None:
        boxes = []
    boxes = boxes + ([(0, "")] * ((8 - len(foreign_apps)) - len(boxes)))

    return transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=sp,
        index=index,
        app_args=["increase_budget"],
        foreign_apps=foreign_apps,
        boxes=boxes,
        # Make transactions unique to avoid "transaction already in ledger" error
        note=uuid.uuid4().bytes
    )


def prepare_get_total_cumulative_power_at_transactions(ledger, user_address, timestamp, sp):
    total_power_index = get_total_power_index_at(ledger, VAULT_APP_ID, timestamp)
    if total_power_index is None:
        total_power_index = 0
        total_power_box_index = 0
    else:
        total_power_box_index = total_power_index // TOTAL_POWER_BOX_ARRAY_LEN

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["get_total_cumulative_power_at", timestamp, total_power_index],
            boxes=[
                (0, TOTAL_POWERS + int_to_bytes(total_power_box_index)),
                (0, TOTAL_POWERS + int_to_bytes(total_power_box_index + 1)),
            ]
        )
    ]
    return txn_group


def prepare_get_cumulative_power_of_at_transactions(ledger, user_address, timestamp, sp):
    account_power_index = get_account_power_index_at(ledger, VAULT_APP_ID, user_address, timestamp)
    if account_power_index is None:
        account_power_index = 0
        account_power_box_index = 0
    else:
        account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["get_cumulative_power_of_at", decode_address(user_address), timestamp, account_power_index],
            boxes=[
                (0, decode_address(user_address)),
                (0, decode_address(user_address) + int_to_bytes(account_power_box_index)),
                (0, decode_address(user_address) + int_to_bytes(account_power_box_index + 1)),
            ]
        )
    ]
    return txn_group
