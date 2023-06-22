from algosdk import transaction
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from common.constants import WEEK, LOCKING_APP_ID, REWARDS_APP_ID, TINY_ASSET_ID
from common.utils import get_account_power_index_at, get_total_power_index_at, get_reward_history_index_at, itob, get_required_minimum_balance_of_box
from locking.constants import ACCOUNT_POWER_BOX_ARRAY_LEN, TOTAL_POWERS, TOTAL_POWER_BOX_ARRAY_LEN
from locking.transactions import prepare_budget_increase_txn
from rewards.constants import REWARD_HISTORY_BOX_SIZE, REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_HISTORY


def prepare_claim_rewards_txn_group(ledger, user_address, timestamp, sp):
    account_power_index_1 = get_account_power_index_at(ledger, LOCKING_APP_ID, user_address, timestamp)
    account_power_box_index_1 = account_power_index_1 // ACCOUNT_POWER_BOX_ARRAY_LEN
    account_power_index_2 = get_account_power_index_at(ledger, LOCKING_APP_ID, user_address, timestamp + WEEK)
    account_power_box_index_2 = account_power_index_2 // ACCOUNT_POWER_BOX_ARRAY_LEN

    total_power_index_1 = get_total_power_index_at(ledger, LOCKING_APP_ID, timestamp)
    total_power_box_index_1 = total_power_index_1 // TOTAL_POWER_BOX_ARRAY_LEN
    total_power_index_2 = get_total_power_index_at(ledger, LOCKING_APP_ID, timestamp + WEEK)
    total_power_box_index_2 = total_power_index_2 // TOTAL_POWER_BOX_ARRAY_LEN

    reward_amount_index = get_reward_history_index_at(ledger, REWARDS_APP_ID, timestamp)
    reward_period_index = timestamp // WEEK - ledger.global_states[REWARDS_APP_ID][b"creation_timestamp"] // WEEK
    reward_period_box_index = reward_period_index // REWARD_HISTORY_BOX_ARRAY_LEN
    account_rewards_sheet_box_name = decode_address(user_address) + itob(reward_period_box_index)

    boxes = [
        (0, account_rewards_sheet_box_name),
        (0, REWARD_HISTORY + itob(reward_amount_index)),
        (LOCKING_APP_ID, decode_address(user_address)),
        (LOCKING_APP_ID, decode_address(user_address) + itob(account_power_box_index_1)),
        (LOCKING_APP_ID, decode_address(user_address) + itob(account_power_box_index_2)),
        (LOCKING_APP_ID, TOTAL_POWERS + itob(total_power_box_index_1)),
        (LOCKING_APP_ID, TOTAL_POWERS + itob(total_power_box_index_2)),
    ]
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=REWARDS_APP_ID,
            app_args=[
                "claim_rewards",
                timestamp,
                account_power_index_1,
                account_power_index_2,
                total_power_index_1,
                total_power_index_2,
                reward_amount_index
            ],
            foreign_apps=[LOCKING_APP_ID],
            foreign_assets=[TINY_ASSET_ID],
            boxes=boxes[:6]
        ),
        prepare_budget_increase_txn(user_address, sp=sp, index=LOCKING_APP_ID, boxes=boxes[6:]),
    ]
    txn_group[0].fee *= 3

    if account_rewards_sheet_box_name not in ledger.boxes[REWARDS_APP_ID]:
        amount = get_required_minimum_balance_of_box(account_rewards_sheet_box_name, REWARD_HISTORY_BOX_SIZE)
        txn_group = [
            transaction.PaymentTxn(
                sender=user_address,
                sp=sp,
                receiver=get_application_address(REWARDS_APP_ID),
                amt=amount,
            ),
        ] + txn_group

    return txn_group
