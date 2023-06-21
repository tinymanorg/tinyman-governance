from datetime import datetime
from zoneinfo import ZoneInfo

from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.common import BaseTestCase, LockingAppMixin, get_budget_increase_txn
from tests.constants import PROPOSAL_BOX_PREFIX, DAY, WEEK, ACCOUNT_POWER_BOX_ARRAY_LEN, ATTENDANCE_BOX_PREFIX
from tests.utils import get_start_timestamp_of_week, print_boxes, itob, sign_txns, get_account_power_index_at, parse_box_staking_proposal, btoi, get_required_minimum_balance_of_box


class StakingVotingTestCase(LockingAppMixin, BaseTestCase):

    def get_create_proposal_txn_group(self, user_address, proposal_id):
        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=self.voting_app_id,
                app_args=["create_proposal", proposal_id],
                boxes=[
                    (0, proposal_box_name),
                ]
            )
        ]
        return txn_group

    def get_cast_vote_txn_group(self, user_address, proposal_id, votes, asset_ids, proposal_creation_timestamp):
        assert(len(votes) == len(asset_ids))
        arg_votes = b"".join([itob(vote) for vote in votes])
        arg_asset_ids = b"".join([itob(asset_id) for asset_id in asset_ids])


        account_power_index = get_account_power_index_at(self.ledger, self.locking_app_id, user_address, proposal_creation_timestamp)
        # assert account_power_index is not None
        account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        proposal_index = parse_box_staking_proposal(self.ledger.boxes[self.voting_app_id][proposal_box_name])["index"]
        account_attendance_box_index = proposal_index // 1024
        account_attendance_box_name = ATTENDANCE_BOX_PREFIX + decode_address(user_address) + itob(account_attendance_box_index)
        boxes=[
            (self.voting_app_id, proposal_box_name),
            (self.voting_app_id, account_attendance_box_name),
            *[(self.voting_app_id, b"v" + itob(proposal_index) + itob(asset_id)) for asset_id in asset_ids],
            (self.locking_app_id, decode_address(user_address)),
            (self.locking_app_id, decode_address(user_address) + itob(account_power_box_index)),
            (self.locking_app_id, decode_address(user_address) + itob(account_power_box_index + 1)),
        ]
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=self.voting_app_id,
                app_args=["cast_vote", proposal_id, arg_votes, arg_asset_ids, account_power_index],
                foreign_apps=[self.locking_app_id],
                boxes=boxes[:7]
            ),
        ]
        txn_group[0].fee *= 2

        if len(boxes) >= 7:
            txn_group.append(
                get_budget_increase_txn(user_address, sp=self.sp, index=self.locking_app_id, foreign_apps=[self.voting_app_id], boxes=boxes[7:14]),
            )
        if len(boxes) >= 14:
            txn_group.append(
                get_budget_increase_txn(user_address, sp=self.sp, index=self.locking_app_id, foreign_apps=[self.voting_app_id], boxes=boxes[14:]),
            )

        payment_amount = 0
        if account_attendance_box_name not in self.ledger.boxes[self.voting_app_id]:
            payment_amount += get_required_minimum_balance_of_box(account_attendance_box_name, 24)

        for asset_id in asset_ids:
            box_name = itob(proposal_index) + itob(asset_id)
            if box_name not in self.ledger.boxes[self.voting_app_id]:
                payment_amount += get_required_minimum_balance_of_box(box_name, 8)

        if payment_amount:
            txn_group = [
                transaction.PaymentTxn(
                    sender=user_address,
                    sp=self.sp,
                    receiver=get_application_address(self.voting_app_id),
                    amt=payment_amount,
                )
            ] + txn_group
        return txn_group


    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.locking_app_id = 6000
        cls.voting_app_id = 8000
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.locking_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.create_locking_app(self.locking_app_id, self.app_creator_address, self.locking_app_creation_timestamp)
        self.init_locking_app(self.locking_app_id, self.locking_app_creation_timestamp + 30)

    def test_create_proposal(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 1_000_000)

        self.create_staking_voting_app(self.voting_app_id, self.app_creator_address, self.locking_app_id)
        self.ledger.set_account_balance(get_application_address(self.voting_app_id), 1_000_000)

        block_timestamp = self.locking_app_creation_timestamp + 2 * WEEK
        proposal_id = itob(1) * 4
        txn_group = self.get_create_proposal_txn_group(self.app_creator_address, proposal_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        print_boxes(self.ledger.boxes[self.voting_app_id])

    def test_cast_vote(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(self.voting_app_id), 1_000_000)

        self.create_staking_voting_app(self.voting_app_id, self.app_creator_address, self.locking_app_id)
        self.ledger.set_account_balance(get_application_address(self.voting_app_id), 1_000_000)

        block_timestamp = self.locking_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp, self.locking_app_id)

        # Create lock 1
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 100_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, app_id=self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        amount = 35_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=user_2_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=user_2_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, app_id=self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = itob(1) * 4
        txn_group = self.get_create_proposal_txn_group(self.app_creator_address, proposal_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        print_boxes(self.ledger.boxes[self.voting_app_id])
        proposal_creation_timestamp = block_timestamp

        # Cast Vote
        block_timestamp = proposal_creation_timestamp + DAY
        # votes = [10, 15, 20, 25, 30]
        # asset_ids = [1, 2, 3, 4, 50]
        # votes = [10, 10, 10, 10, 10, 10, 10, 10, 10, 5, 5]
        votes = [10] * 5 + [5] * 9 + [3, 2]
        asset_ids = list(range(1, len(votes) + 1))

        txn_group = self.get_cast_vote_txn_group(user_address, proposal_id, votes, asset_ids, proposal_creation_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        # output = self.low_level_eval(signed_txns, block_timestamp=block_timestamp)
        # print(output)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        print(btoi(block[b'txns'][1][b'dt'][b'lg'][-1]))
        print_boxes(self.ledger.boxes[self.voting_app_id])

        votes = [20] * 5
        asset_ids = list(range(1, len(votes) + 1))
        txn_group = self.get_cast_vote_txn_group(user_2_address, proposal_id, votes, asset_ids, proposal_creation_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        print(btoi(block[b'txns'][1][b'dt'][b'lg'][-1]))
        print_boxes(self.ledger.boxes[self.voting_app_id])