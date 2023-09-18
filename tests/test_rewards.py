import time
import unittest.mock
from datetime import datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from algosdk.transaction import ApplicationCallTxn, OnComplete
from tinyman.governance.constants import TINY_ASSET_ID_KEY, VAULT_APP_ID_KEY
from tinyman.governance.constants import WEEK
from tinyman.governance.event import decode_logs
from tinyman.governance.rewards.constants import MANAGER_KEY, REWARD_HISTORY_COUNT_KEY, REWARD_PERIOD_COUNT_KEY, FIRST_PERIOD_TIMESTAMP, REWARDS_MANAGER_KEY
from tinyman.governance.rewards.events import rewards_events
from tinyman.governance.rewards.storage import get_reward_history_box_name, RewardClaimSheet, get_account_reward_claim_sheet_box_name, parse_box_reward_history, RewardHistory
from tinyman.governance.rewards.transactions import prepare_claim_reward_transactions, prepare_init_transactions, prepare_create_reward_period_transactions, prepare_set_reward_amount_transactions
from tinyman.governance.vault.constants import TOTAL_LOCKED_AMOUNT_KEY
from tinyman.governance.vault.storage import get_power_index_at
from tinyman.governance.vault.transactions import prepare_create_lock_transactions, prepare_increase_lock_amount_transactions
from tinyman.governance.vault.utils import get_start_timestamp_of_week
from tinyman.utils import TransactionGroup

from tests.common import BaseTestCase, VaultAppMixin, RewardsAppMixin
from tests.constants import TINY_ASSET_ID, rewards_approval_program, rewards_clear_state_program, VAULT_APP_ID, REWARDS_APP_ID
from tests.rewards.utils import get_rewards_app_global_state
from tests.utils import get_total_power_index_at, get_reward_history_index_at
from tests.vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at, get_account_powers


class RewardsTestCase(VaultAppMixin, RewardsAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_address = generate_account()
        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 50_000_000)

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

        self.ledger.set_account_balance(self.user_address, 120_000_000)

    def test_create_and_init_app(self):
        block_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        txn_group = TransactionGroup([
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=rewards_approval_program.bytecode,
                clear_program=rewards_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=5, num_byte_slices=2),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=3,
                app_args=[TINY_ASSET_ID, VAULT_APP_ID],
            )
        ])
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_id = block[b"txns"][0][b"apid"]

        self.assertDictEqual(
            self.ledger.global_states[app_id],
            {
                FIRST_PERIOD_TIMESTAMP: 0,
                VAULT_APP_ID_KEY: VAULT_APP_ID,
                MANAGER_KEY: decode_address(self.app_creator_address),
                REWARDS_MANAGER_KEY: decode_address(self.app_creator_address),
                REWARD_HISTORY_COUNT_KEY: 0,
                REWARD_PERIOD_COUNT_KEY: 0,
                TINY_ASSET_ID_KEY: TINY_ASSET_ID
            }
        )

        reward_amount = 1_000_000
        reward_histories_box_name = get_reward_history_box_name(box_index=0)
        txn_group = prepare_init_transactions(
            rewards_app_id=app_id,
            tiny_asset_id=TINY_ASSET_ID,
            reward_amount=reward_amount,
            sender=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=int(block_datetime.timestamp()))
        app_call_txn = block[b'txns'][1]
        opt_in_itx = app_call_txn[b'dt'][b'itx'][0][b'txn']
        self.assertDictEqual(
            opt_in_itx,
            {
                b'arcv': decode_address(get_application_address(app_id)),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(get_application_address(app_id)),
                b'type': b'axfer',
                b'xaid': TINY_ASSET_ID
            }
        )

        reward_histories = parse_box_reward_history(self.ledger.boxes[app_id][reward_histories_box_name])
        self.assertEqual(len(reward_histories), 1)
        reward_history = reward_histories[0]
        next_week_timestamp = get_start_timestamp_of_week(block_timestamp) + WEEK
        self.assertEqual(
            reward_history,
            RewardHistory(
                timestamp=next_week_timestamp,
                reward_amount=reward_amount
            )
        )

    def test_claim_rewards(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        reward_amount = 1_000_000
        self.create_rewards_app(self.app_creator_address)
        first_period_start_timestamp = get_start_timestamp_of_week(self.vault_app_creation_timestamp) + WEEK
        self.init_rewards_app(first_period_start_timestamp, reward_amount)
        self.ledger.move(
            reward_amount * 1_000,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=get_application_address(REWARDS_APP_ID)
        )

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 200 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount * 100,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        print("user", self.user_address)
        print("app_creator_address", self.app_creator_address)
        print("rewards_app", get_application_address(REWARDS_APP_ID))
        print("vault_app", get_application_address(VAULT_APP_ID))
        for period_index in range(5):
            print()
            print(period_index)

            # Create checkpoints
            block_timestamp = first_period_start_timestamp + (WEEK * (period_index + 1))
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

                txn_group = prepare_create_reward_period_transactions(
                    rewards_app_id=REWARDS_APP_ID,
                    vault_app_id=VAULT_APP_ID,
                    sender=self.user_address,
                    rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                    reward_history_index=get_reward_history_index_at(self.ledger, REWARDS_APP_ID, first_period_start_timestamp + (WEEK * period_index)),
                    total_power_period_start_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, first_period_start_timestamp + (WEEK * period_index)) or 0,
                    total_power_period_end_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, first_period_start_timestamp + (WEEK * (period_index + 1))),
                    suggested_params=self.sp,
                )
                txn_group.sign_with_private_key(self.user_address, self.user_sk)
                block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

                logs =[t for t in block[b'txns'] if t[b'txn'][b'type'] == b'appl'][0][b'dt'][b'lg']
                events = decode_logs(logs, events=rewards_events)
                for e in events:
                    print(e)

                if period_index % 2:
                    txn_group = prepare_increase_lock_amount_transactions(
                        vault_app_id=VAULT_APP_ID,
                        tiny_asset_id=TINY_ASSET_ID,
                        sender=self.user_address,
                        locked_amount=amount,
                        vault_app_global_state=get_vault_app_global_state(self.ledger),
                        account_state=get_account_state(self.ledger, self.user_address),
                        suggested_params=self.sp,
                        app_call_note=None,
                    )
                    txn_group.sign_with_private_key(self.user_address, self.user_sk)
                    self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

                if period_index % 4 == 0:
                    txn_group = prepare_set_reward_amount_transactions(
                        rewards_app_id=REWARDS_APP_ID,
                        rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                        reward_amount=reward_amount + period_index * 1_000,
                        sender=self.app_creator_address,
                        suggested_params=self.sp,
                    )
                    txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
                    self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        account_powers = get_account_powers(self.ledger, self.user_address)        
        period_index_start = 0
        period_count = 3
        account_power_indexes = [get_power_index_at(account_powers, first_period_start_timestamp + (WEEK * (period_index_start + i))) or 0 for i in range(period_count + 1)]

        txn_group = prepare_claim_reward_transactions(
            rewards_app_id=REWARDS_APP_ID,
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            period_index_start=period_index_start,
            period_count=period_count,
            account_power_indexes=account_power_indexes,
            create_reward_claim_sheet=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        box_name = get_account_reward_claim_sheet_box_name(address=self.user_address, box_index=0)
        raw_box = self.ledger.boxes[REWARDS_APP_ID][box_name]
        claim_sheet = RewardClaimSheet(value=raw_box)
        print(claim_sheet.claim_sheet[:10])

        txn_group = TransactionGroup(
            [
                ApplicationCallTxn(
                    sender=self.app_creator_address,
                    index=REWARDS_APP_ID,
                    sp=self.sp,
                    on_complete=OnComplete.NoOpOC,
                    app_args=["unset_claim_sheet", decode_address(self.user_address), 0],
                    boxes=[(REWARDS_APP_ID, box_name)]
                )
            ]
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        box_name = get_account_reward_claim_sheet_box_name(address=self.user_address, box_index=0)
        raw_box = self.ledger.boxes[REWARDS_APP_ID][box_name]
        claim_sheet = RewardClaimSheet(value=raw_box)
        print(claim_sheet.claim_sheet[:10])

        # app_calls = [t for t in block[b'txns'] if t[b'txn'][b'type'] == b'appl']
        # logs = app_calls[-1][b'dt'][b'lg']
        # 
        # print("OPCODE", bytes_to_int(logs[-1]))
        # print(app_calls)
        # events = decode_logs(logs, events=rewards_events)
        # for e in events:
        #     print(e)


def get_reward_period_indexes(account_powers, first_period_start_timestamp):
    reward_period_indexes = []
    period_timestamp_min = max([account_powers[0].timestamp, first_period_start_timestamp])
    period_timestamp_max = get_start_timestamp_of_week(min([account_powers[-1].lock_end_timestamp, int(time.time())]))
    
    for timestamp in range(period_timestamp_min, period_timestamp_max + WEEK, WEEK):
        timestamp_start = timestamp
        timestamp_end = timestamp_start + WEEK
        
        index_start = get_power_index_at(account_powers, timestamp_start)
        cumulative_power_start = account_powers[index_start].cumulative_power_at(timestamp_start)
        
        index_end = get_power_index_at(account_powers, timestamp_end)
        cumulative_power_end = account_powers[index_end].cumulative_power_at(timestamp_end)

        cumulative_power_delta = cumulative_power_end - cumulative_power_start
        if cumulative_power_delta:
            reward_period_index = timestamp_start // WEEK - first_period_start_timestamp // WEEK
            reward_period_indexes.append(reward_period_index)
    
    return reward_period_indexes


def group_adjacent_period_indexes(indexes: list[int]):
    if not indexes:  # Handle empty list
        return []

    grouped = []
    current_group = [indexes[0]]

    for i in range(1, len(indexes)):
        # Check if the current number is adjacent to the previous number
        if indexes[i] - indexes[i - 1] == 1:
            current_group.append(indexes[i])
        else:
            grouped.append(current_group)
            current_group = [indexes[i]]

    # Append the last group after exiting the loop
    grouped.append(current_group)

    return grouped


def get_claimed_reward_period_indexes(address, ledger):
    box_name = get_account_reward_claim_sheet_box_name(address, 0)
    if box_name in ledger.boxes[REWARDS_APP_ID]:
        raw_box_value = ledger.boxes[REWARDS_APP_ID][box_name]
        claim_sheet = RewardClaimSheet(value=raw_box_value).claim_sheet
        claimed_reward_period_indexes = [index for index, value in enumerate(claim_sheet) if value]
        return claimed_reward_period_indexes
    else:
        return []
