from datetime import datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.common import BaseTestCase, LockingAppMixin, get_budget_increase_txn
from tests.constants import TOTAL_POWERS, WEEK, rewards_approval_program, rewards_clear_state_program, REWARD_HISTORY, REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT, REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_HISTORY_BOX_SIZE, ACCOUNT_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_ARRAY_LEN
from tests.utils import get_start_timestamp_of_week, print_boxes, itob, sign_txns, parse_box_reward_history, get_reward_history_index_at, get_total_power_index_at, get_account_power_index_at


class RewardsTestCase(LockingAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.locking_app_id = 9000
        cls.rewards_app_id = 10999
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_address = generate_account()
        # cls.user_2_sk, cls.user_2_address = generate_account()
        # cls.user_3_sk, cls.user_3_address = generate_account()
        cls.locking_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

        self.create_locking_app(self.locking_app_id, self.app_creator_address, self.locking_app_creation_timestamp)
        self.init_locking_app(self.locking_app_id, self.locking_app_creation_timestamp + 30)
        # self.create_rewards_app(self.rewards_app_id, self.app_creator_address, self.locking_app_id)

        self.ledger.set_account_balance(self.user_address, 100_000_000)
        # # self.ledger.set_account_balance(self.user_2_address, 100_000_000)
        # # self.ledger.set_account_balance(self.user_3_address, 100_000_000)
        #
        # self.ledger.set_account_balance(get_application_address(self.rewards_app_id), 1_000_000)
        # self.ledger.move(
        #     100_000_000,
        #     asset_id=self.tiny_asset_id,
        #     sender=self.ledger.assets[self.tiny_asset_id]["creator"],
        #     receiver=get_application_address(self.rewards_app_id)
        # )

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
                foreign_assets=[self.tiny_asset_id],
                foreign_apps=[self.locking_app_id],
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        app_id = block[b"txns"][0][b"apid"]

        self.assertDictEqual(
            self.ledger.global_states[app_id],
            {
                b'creation_timestamp': self.locking_app_creation_timestamp,
                b'locking_app_id': self.locking_app_id,
                b'manager': decode_address(self.app_creator_address),
                b'reward_history_count': 0,
                b'tiny_asset_id': self.tiny_asset_id
            }
        )

        reward_amount = 1_000_000
        reward_histories_box_name = REWARD_HISTORY + itob(0)
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
                    self.tiny_asset_id
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
                b'xaid': self.tiny_asset_id
            }
        )

        reward_histories = parse_box_reward_history(self.ledger.boxes[app_id][reward_histories_box_name])
        self.assertEqual(len(reward_histories), 1)
        reward_history = reward_histories[0]
        self.assertDictEqual(
            reward_history,
            {
                'timestamp': self.locking_app_creation_timestamp,
                'reward_amount': reward_amount
            }
        )

    def get_claim_rewards_txn_group(self, user_address, timestamp, rewards_app_id, locking_app_id):
        account_power_index_1 = get_account_power_index_at(self.ledger, self.locking_app_id, user_address, timestamp)
        account_power_box_index_1 = account_power_index_1 // ACCOUNT_POWER_BOX_ARRAY_LEN
        account_power_index_2 = get_account_power_index_at(self.ledger, self.locking_app_id, user_address, timestamp + WEEK)
        account_power_box_index_2 = account_power_index_2 // ACCOUNT_POWER_BOX_ARRAY_LEN

        total_power_index_1 = get_total_power_index_at(self.ledger, self.locking_app_id, timestamp)
        total_power_box_index_1 = total_power_index_1 // TOTAL_POWER_BOX_ARRAY_LEN
        total_power_index_2 = get_total_power_index_at(self.ledger, self.locking_app_id, timestamp + WEEK)
        total_power_box_index_2 = total_power_index_2 // TOTAL_POWER_BOX_ARRAY_LEN

        reward_amount_index = get_reward_history_index_at(self.ledger, rewards_app_id, timestamp)
        reward_period_index = timestamp // WEEK - self.ledger.global_states[rewards_app_id][b"creation_timestamp"] // WEEK
        reward_period_box_index = reward_period_index // REWARD_HISTORY_BOX_ARRAY_LEN
        account_rewards_sheet_box_name = decode_address(user_address) + itob(reward_period_box_index)

        boxes = [
            (0, account_rewards_sheet_box_name),
            (0, REWARD_HISTORY + itob(reward_amount_index)),
            (locking_app_id, decode_address(user_address)),
            (locking_app_id, decode_address(user_address) + itob(account_power_box_index_1)),
            (locking_app_id, decode_address(user_address) + itob(account_power_box_index_2)),
            (locking_app_id, TOTAL_POWERS + itob(total_power_box_index_1)),
            (locking_app_id, TOTAL_POWERS + itob(total_power_box_index_2)),
        ]
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=rewards_app_id,
                app_args=[
                    "claim_rewards",
                    timestamp,
                    account_power_index_1,
                    account_power_index_2,
                    total_power_index_1,
                    total_power_index_2,
                    reward_amount_index
                ],
                foreign_apps=[locking_app_id],
                foreign_assets=[self.tiny_asset_id],
                boxes=boxes[:6]
            ),
            get_budget_increase_txn(user_address, sp=self.sp, index=self.locking_app_id, boxes=boxes[6:]),
        ]
        txn_group[0].fee *= 3

        if account_rewards_sheet_box_name not in self.ledger.boxes[self.rewards_app_id]:
            amount = 2_500 + 400 * (len(account_rewards_sheet_box_name) + REWARD_HISTORY_BOX_SIZE)
            txn_group = [
                transaction.PaymentTxn(
                    sender=user_address,
                    sp=self.sp,
                    receiver=get_application_address(rewards_app_id),
                    amt=amount,
                ),
            ] + txn_group

        return txn_group
    def test_claim_rewards(self):

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        # last_checkpoint_timestamp = block_timestamp - 10
        # self.create_locking_app(self.locking_app_id, self.app_creator_address, self.locking_app_creation_timestamp)
        # self.init_locking_app(self.locking_app_id, timestamp=last_checkpoint_timestamp)
        reward_amount = 100_000_000
        self.create_rewards_app(self.rewards_app_id, self.app_creator_address, self.locking_app_id, self.locking_app_creation_timestamp)
        self.init_rewards_app(self.rewards_app_id, self.locking_app_creation_timestamp, reward_amount)

        self.ledger.move(
            reward_amount * 10,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=get_application_address(self.rewards_app_id)
        )

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, app_id=self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.locking_app_id][b'total_locked_amount'], amount)
        lock_start_timestamp = block_timestamp

        # Create checkpoints
        block_timestamp = lock_end_timestamp + 1
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp, self.locking_app_id)

        # Withdraw
        txn_group = self.get_withdraw_txn_group(self.user_address, app_id=self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.locking_app_id][b'total_locked_amount'], 0)

        # Create lock
        lock_end_timestamp = lock_end_timestamp + 5 * WEEK
        amount = 10_000_000
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, app_id=self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.locking_app_id][b'total_locked_amount'], amount)
        print("test_create_lock_after_withdraw")
        print_boxes(self.ledger.boxes[self.locking_app_id])

        # for t in [1646870401, 1646870400, 1646352000, 1646092800, 1646179200]:
        #     txn_group = self.get_get_cumulative_power_of_at_txn_group(self.user_address, t, app_id=self.locking_app_id)
        #     transaction.assign_group_id(txn_group)
        #     signed_txns = sign_txns(txn_group, self.user_sk)
        #     block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        #     print(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))
        #
        #     txn_group = self.get_get_total_cumulative_power_at_txn_group(self.user_address, t, app_id=self.locking_app_id)
        #     transaction.assign_group_id(txn_group)
        #     signed_txns = sign_txns(txn_group, self.user_sk)
        #     block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        #     print(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        timestamp = get_start_timestamp_of_week(lock_start_timestamp) + WEEK
        print(timestamp)
        txn_group = self.get_claim_rewards_txn_group(self.user_address, timestamp, self.rewards_app_id, self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        # r = self.low_level_eval(signed_txns, block_timestamp=block_timestamp)
        # print(r)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        # for i, l in enumerate(block[b'txns'][0][b'dt'][b'lg']):
        #     print(i, btoi(l))

        txn_group = self.get_claim_rewards_txn_group(self.user_address, timestamp, self.rewards_app_id, self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        with self.assertRaises(LogicEvalError):
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
