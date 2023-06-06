from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.common import BaseTestCase
from tests.constants import TOTAL_POWERS, PROPOSALS, DAY, WEEK, HOUR
from tests.utils import get_start_timestamp_of_week, get_slope, get_voting_power, print_boxes, itob, sign_txns, get_start_time_of_day


class VotingTestCase(BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.locking_app_id = 9000
        cls.voting_app_id = 9876
        cls.app_creator_sk, cls.app_creator_address = generate_account()

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.create_locking_app(self.locking_app_id, self.app_creator_address)
        self.create_voting_app(self.voting_app_id, self.app_creator_address, self.locking_app_id)

    def test_create_proposal(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 1_000_000)
        self.ledger.set_account_balance(get_application_address(self.voting_app_id), 1_000_000)

        account_first_index = account_last_index = 0
        locked_amount = 100_000_000
        lock_start_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        lock_start_time = int(lock_start_datetime.timestamp())
        lock_end_time = get_start_timestamp_of_week(int((lock_start_datetime + timedelta(days=50)).timestamp()))
        slope = get_slope(locked_amount)
        remaining_time = lock_end_time - lock_start_time
        voting_power = get_voting_power(slope, remaining_time)

        self.set_box_account_state(self.locking_app_id, user_address, locked_amount, lock_end_time, account_first_index, account_last_index)
        self.set_box_account_power(self.locking_app_id, user_address, index=0, locked_amount=locked_amount, locked_round=2, start_time=lock_start_time, end_time=lock_end_time)
        self.set_box_total_power(self.locking_app_id, lock_start_time, bias=voting_power, slope=slope, cumulative_power=0)
        self.init_global_indexes(self.locking_app_id, index=lock_start_time)
        self.set_box_slope_change(self.locking_app_id, lock_end_time, slope)

        option_count = 5
        box_index = 0
        proposal_id = itob(1) * 4
        block_time = lock_start_time
        today = get_start_time_of_day(block_time)
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=self.voting_app_id,
                app_args=["create_proposal", proposal_id, option_count, box_index],
                foreign_apps=[self.locking_app_id],
                boxes=[
                    (0, PROPOSALS + proposal_id),

                    (self.locking_app_id, decode_address(user_address)),
                    (self.locking_app_id, decode_address(user_address) + itob(box_index)),
                    (self.locking_app_id, TOTAL_POWERS + itob(today)),
                ]
            )
        ]
        txn_group[0].fee *= 3

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_time)

        print("---TXN---")
        print(block)
        print_boxes(self.ledger.boxes[self.voting_app_id])

    def test_cast_vote(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 1_000_000)
        self.ledger.set_account_balance(get_application_address(self.voting_app_id), 1_000_000)

        account_first_index = account_last_index = 0
        locked_amount = 100_000_000
        lock_start_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        lock_start_time = int(lock_start_datetime.timestamp())
        lock_end_time = get_start_timestamp_of_week(int((lock_start_datetime + timedelta(days=50)).timestamp()))
        slope = get_slope(locked_amount)
        remaining_time = lock_end_time - lock_start_time
        voting_power = get_voting_power(slope, remaining_time)

        self.set_box_account_state(self.locking_app_id, user_address, locked_amount, lock_end_time, account_first_index, account_last_index)
        self.set_box_account_power(self.locking_app_id, user_address, index=0, locked_amount=locked_amount, locked_round=2, start_time=lock_start_time, end_time=lock_end_time)
        self.set_box_total_power(self.locking_app_id, lock_start_time, bias=voting_power, slope=slope, cumulative_power=0)
        self.init_global_indexes(self.locking_app_id, index=lock_start_time)
        self.set_box_slope_change(self.locking_app_id, lock_end_time, slope)

        option_count = 5
        proposal_id = itob(1) * 4
        voting_start_time = lock_start_time + (2 * DAY)
        voting_end_time = lock_start_time + (2 * DAY) + WEEK

        self.set_box_proposal(
            self.voting_app_id,
            proposal_id=proposal_id,
            creation_time=lock_start_time,
            voting_start_time=voting_start_time,
            voting_end_time=voting_end_time,
            option_count=option_count,
        )
        print_boxes(self.ledger.boxes[self.locking_app_id])

        box_index = 0
        proposal_id = itob(1) * 4
        block_time = voting_start_time + HOUR
        today = get_start_time_of_day(block_time)
        votes = b"".join([itob(10), itob(15), itob(20), itob(25), itob(30)])
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=self.voting_app_id,
                app_args=["cast_vote", proposal_id, votes, box_index],
                foreign_apps=[self.locking_app_id],
                boxes=[
                    (0, PROPOSALS + proposal_id),
                    (0, proposal_id + decode_address(user_address)),

                    (self.locking_app_id, decode_address(user_address)),
                    (self.locking_app_id, decode_address(user_address) + itob(box_index)),
                    (self.locking_app_id, TOTAL_POWERS + itob(today)),
                ]
            )
        ]
        txn_group[0].fee *= 2

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        try:
            block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_time)
        except Exception as exc:
            breakpoint()
            print(exc)
            raise exc

        print("---TXN---")
        print_boxes(self.ledger.boxes[self.voting_app_id])
