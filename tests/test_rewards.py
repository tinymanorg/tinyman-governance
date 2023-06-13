from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.common import BaseTestCase, LockingAppMixin
from tests.constants import TOTAL_POWERS, DAY, WEEK
from tests.utils import get_start_timestamp_of_week, print_boxes, itob, sign_txns, get_start_time_of_day, btoi


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

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.create_locking_app(self.locking_app_id, self.app_creator_address)
        self.create_rewards_app(self.rewards_app_id, self.app_creator_address, self.locking_app_id)

        self.ledger.set_account_balance(self.user_address, 100_000_000)
        # self.ledger.set_account_balance(self.user_2_address, 100_000_000)
        # self.ledger.set_account_balance(self.user_3_address, 100_000_000)

        self.ledger.set_account_balance(get_application_address(self.rewards_app_id), 1_000_000)
        self.ledger.move(
            100_000_000,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=get_application_address(self.rewards_app_id)
        )

    def get_claim_rewards_txn_group(self, user_address, timestamp, rewards_app_id, locking_app_id):
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=rewards_app_id,
                app_args=[
                    "claim_rewards",
                    timestamp,
                    0,
                    0,
                    0,
                    2,
                ],
                foreign_apps=[locking_app_id],
                foreign_assets=[self.tiny_asset_id],
                boxes=[
                    (0, decode_address(user_address) + itob(18)),
                    (locking_app_id, decode_address(user_address)),
                    (locking_app_id, decode_address(user_address) + itob(0)),
                    (locking_app_id, TOTAL_POWERS + itob(0)),
                ]
            ),
        ]
        txn_group[0].fee *= 6
        return txn_group
    def test_claim_rewards(self):

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.locking_app_id, self.app_creator_address)
        self.init_locking_app(self.locking_app_id, timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
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
        self.create_checkpoints(block_timestamp, self.locking_app_id)

        # Withdraw
        txn_group = self.get_withdraw_txn_group(self.user_address, app_id=self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.locking_app_id][b'total_locked_amount'], 0)

        # Create lock
        lock_end_timestamp = lock_end_timestamp + 2 * WEEK
        amount = 10_000_000
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, app_id=self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.locking_app_id][b'total_locked_amount'], amount)
        print("test_create_lock_after_withdraw")
        print_boxes(self.ledger.boxes[self.locking_app_id])

        for t in [1646870401, 1646870400, 1646352000, 1646092800, 1646179200]:
            txn_group = self.get_get_cumulative_power_of_at_txn_group(self.user_address, t, app_id=self.locking_app_id)
            transaction.assign_group_id(txn_group)
            signed_txns = sign_txns(txn_group, self.user_sk)
            block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
            print(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

            txn_group = self.get_get_total_cumulative_power_at_txn_group(self.user_address, t, app_id=self.locking_app_id)
            transaction.assign_group_id(txn_group)
            signed_txns = sign_txns(txn_group, self.user_sk)
            block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
            print(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        timestamp = get_start_time_of_day(lock_start_timestamp)
        txn_group = self.get_claim_rewards_txn_group(self.user_address, timestamp, self.rewards_app_id, self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        for i, l in enumerate(block[b'txns'][0][b'dt'][b'lg']):
            print(i, btoi(l))

        txn_group = self.get_claim_rewards_txn_group(self.user_address, timestamp, self.rewards_app_id, self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        with self.assertRaises(LogicEvalError):
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
