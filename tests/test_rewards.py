import unittest.mock
from datetime import datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import WEEK
from tinyman.governance.vault.transactions import prepare_create_lock_transactions, prepare_withdraw_transactions
from tinyman.governance.vault.utils import get_start_timestamp_of_week
from tinyman.utils import int_to_bytes

from common.constants import TINY_ASSET_ID, rewards_approval_program, rewards_clear_state_program, VAULT_APP_ID, REWARDS_APP_ID
from common.utils import sign_txns, parse_box_reward_history
from rewards.constants import CREATION_TIMESTAMP_KEY, TINY_ASSET_ID_KEY, VAULT_APP_ID_KEY, MANAGER_KEY, REWARD_HISTORY_COUNT_KEY, REWARD_HISTORY_BOX_PREFIX, REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT
from rewards.transactions import prepare_claim_rewards_transactions
from tests.common import BaseTestCase, VaultAppMixin, RewardsAppMixin
from tinyman.governance.vault.constants import TOTAL_LOCKED_AMOUNT_KEY
from vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at


class RewardsTestCase(VaultAppMixin, RewardsAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_address = generate_account()
        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

        self.ledger.set_account_balance(self.user_address, 100_000_000)

    def test_create_and_init_app(self):
        block_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        txn_group = [
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=rewards_approval_program.bytecode,
                clear_program=rewards_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=4, num_byte_slices=1),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=0,
                app_args=[TINY_ASSET_ID],
                foreign_apps=[VAULT_APP_ID],
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        app_id = block[b"txns"][0][b"apid"]

        self.assertDictEqual(
            self.ledger.global_states[app_id],
            {
                CREATION_TIMESTAMP_KEY: self.vault_app_creation_timestamp,
                VAULT_APP_ID_KEY: VAULT_APP_ID,
                MANAGER_KEY: decode_address(self.app_creator_address),
                REWARD_HISTORY_COUNT_KEY: 0,
                TINY_ASSET_ID_KEY: TINY_ASSET_ID
            }
        )

        reward_amount = 1_000_000
        reward_histories_box_name = REWARD_HISTORY_BOX_PREFIX + int_to_bytes(0)
        txn_group = [
            transaction.PaymentTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                receiver=get_application_address(app_id),
                amt=REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                index=app_id,
                app_args=[
                    "init",
                    reward_amount
                ],
                foreign_assets=[
                    TINY_ASSET_ID
                ],
                boxes=[
                    (0, reward_histories_box_name),
                ]
            ),
        ]
        txn_group[1].fee *= 2

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
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
        self.assertDictEqual(
            reward_history,
            {
                'timestamp': self.vault_app_creation_timestamp,
                'reward_amount': reward_amount
            }
        )

    def test_claim_rewards(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        # last_checkpoint_timestamp = block_timestamp - 10
        # self.create_vault_app(VAULT_APP_ID, self.app_creator_address, self.vault_app_creation_timestamp)
        # self.init_vault_app(VAULT_APP_ID, timestamp=last_checkpoint_timestamp)
        reward_amount = 100_000_000
        self.create_rewards_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_rewards_app(self.vault_app_creation_timestamp, reward_amount)

        self.ledger.move(
            reward_amount * 10,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=get_application_address(REWARDS_APP_ID)
        )

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
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
        lock_start_timestamp = block_timestamp

        # Create checkpoints
        block_timestamp = lock_end_timestamp + 1
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        # Withdraw
        txn_group = prepare_withdraw_transactions(
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            account_state=get_account_state(self.ledger, self.user_address),
            suggested_params=self.sp,
            app_call_note=None,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], 0)

        # Create lock
        lock_end_timestamp = lock_end_timestamp + 5 * WEEK
        amount = 10_000_000
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

        timestamp = get_start_timestamp_of_week(lock_start_timestamp) + WEEK
        txn_group = prepare_claim_rewards_transactions(self.ledger, self.user_address, timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        txn_group = prepare_claim_rewards_transactions(self.ledger, self.user_address, timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], "assert(!getbit(sheet, array_index))")
