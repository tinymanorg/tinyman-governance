from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.common import BaseTestCase, LockingAppMixin
from tests.constants import PROPOSAL_BOX_PREFIX, DAY, WEEK, ACCOUNT_POWER_BOX_ARRAY_LEN, ATTENDANCE_BOX_PREFIX, TOTAL_POWERS
from tests.utils import get_start_timestamp_of_week, itob, sign_txns, get_account_power_index_at, parse_box_staking_proposal, btoi, get_required_minimum_balance_of_box, get_latest_total_powers_indexes, parse_box_proposal, get_start_time_of_day, get_bias, get_slope


class ProposalVotingTestCase(LockingAppMixin, BaseTestCase):

    def get_create_proposal_txn_group(self, user_address, proposal_id):
        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        account_state_box_name = decode_address(user_address)
        latest_total_powers_box_index, _ = get_latest_total_powers_indexes(self.ledger, self.locking_app_id)
        latest_total_powers_box_name = TOTAL_POWERS + itob(latest_total_powers_box_index)
        
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=self.proposal_voting_app_id,
                app_args=["create_proposal", proposal_id],
                foreign_apps=[self.locking_app_id],
                boxes=[
                    (self.proposal_voting_app_id, proposal_box_name),
                    (self.locking_app_id, account_state_box_name),
                    (self.locking_app_id, latest_total_powers_box_name)
                ]
            )
        ]
        # 2 inner txns
        txn_group[0].fee *= 3
        return txn_group

    def get_cast_vote_txn_group(self, user_address, proposal_id, vote, proposal_creation_timestamp):
        assert vote in [0, 1, 2]

        account_power_index = get_account_power_index_at(self.ledger, self.locking_app_id, user_address, proposal_creation_timestamp)
        # assert account_power_index is not None
        account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        proposal_index = parse_box_staking_proposal(self.ledger.boxes[self.proposal_voting_app_id][proposal_box_name])["index"]
        account_attendance_box_index = proposal_index // 1024
        account_attendance_box_name = ATTENDANCE_BOX_PREFIX + decode_address(user_address) + itob(account_attendance_box_index)

        boxes=[
            (self.proposal_voting_app_id, proposal_box_name),
            (self.proposal_voting_app_id, account_attendance_box_name),
            (self.locking_app_id, decode_address(user_address)),
            (self.locking_app_id, decode_address(user_address) + itob(account_power_box_index)),
        ]
        if (account_power_index + 1) % ACCOUNT_POWER_BOX_ARRAY_LEN:
            boxes.append(
                (self.locking_app_id, decode_address(user_address) + itob(account_power_box_index + 1)),
            )

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=self.proposal_voting_app_id,
                app_args=["cast_vote", proposal_id, vote, account_power_index],
                foreign_apps=[self.locking_app_id],
                boxes=boxes
            ),
        ]
        txn_group[0].fee *= 2

        payment_amount = 0
        if account_attendance_box_name not in self.ledger.boxes[self.proposal_voting_app_id]:
            payment_amount += get_required_minimum_balance_of_box(account_attendance_box_name, 24)


        if payment_amount:
            txn_group = [
                transaction.PaymentTxn(
                    sender=user_address,
                    sp=self.sp,
                    receiver=get_application_address(self.proposal_voting_app_id),
                    amt=payment_amount,
                )
            ] + txn_group
        return txn_group

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.locking_app_id = 6000
        cls.proposal_voting_app_id = 9000
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.locking_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.create_locking_app(self.locking_app_id, self.app_creator_address, self.locking_app_creation_timestamp)
        self.init_locking_app(self.locking_app_id, self.locking_app_creation_timestamp + 30)

    def test_create_proposal(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(self.proposal_voting_app_id), 1_000_000)

        self.create_proposal_voting_app(self.proposal_voting_app_id, self.app_creator_address, self.locking_app_id)
        self.ledger.set_account_balance(get_application_address(self.proposal_voting_app_id), 1_000_000)

        block_timestamp = self.locking_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp, self.locking_app_id)

        # Create lock 1
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 52 * WEEK
        amount = 100_000_000
        bias_1 = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

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

        # User 2
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 10_000_000
        bias_2 = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

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

        # Create proposal successfully
        proposal_id = itob(1) * 4
        txn_group = self.get_create_proposal_txn_group(user_address, proposal_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        print(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        self.assertDictEqual(
            self.ledger.global_states[self.proposal_voting_app_id],
            {
                b'locking_app_id': self.locking_app_id,
                b'manager': decode_address(self.app_creator_address),
                b'proposal_id_counter': 1,
                b'proposal_threshold': 10, # %10
                b'quorum_numerator': 50,
                b'tiny_asset_id': self.tiny_asset_id,
                b'voting_delay': 1,
                b'voting_duration': 7
            }
        )

        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        self.assertDictEqual(
            parse_box_proposal(self.ledger.boxes[self.proposal_voting_app_id][proposal_box_name]),
            {
                'index': 0,
                'creation_timestamp': block_timestamp,
                'voting_start_timestamp': get_start_time_of_day(block_timestamp) + DAY,
                'voting_end_timestamp': get_start_time_of_day(block_timestamp) + DAY + WEEK,
                'snapshot_total_voting_power': bias_1 + bias_2,
                'vote_count': 0,
                'is_cancelled': 0,
                'is_executed': 0,
                'is_quorum_reached': 0,
                'quorum_numerator': 50,
                'against_vote_amount': 0,
                'for_vote_amount': 0,
                'abstain_vote_amount': 0,
                'proposer': user_address
            }
        )

        # Creating a proposal with the same id fails
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(!exists)')

        # User 2 doesn't have enough voting power for creating a proposal
        proposal_id = itob(2) * 4
        txn_group = self.get_create_proposal_txn_group(user_2_address, proposal_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert((itob(account_voting_power) b* itob(100)) b>= (itob(total_voting_power) b* itob(app_global_get(PROPOSAL_THRESHOLD_KEY))))')

    def test_cast_vote(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()
        user_3_sk, user_3_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.ledger.set_account_balance(user_3_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(self.proposal_voting_app_id), 1_000_000)

        self.create_proposal_voting_app(self.proposal_voting_app_id, self.app_creator_address, self.locking_app_id)
        self.ledger.set_account_balance(get_application_address(self.proposal_voting_app_id), 1_000_000)

        block_timestamp = self.locking_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp, self.locking_app_id)

        # Create lock 1
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 15 * WEEK
        amount = 100_000_000
        bias_1 = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

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

        # User 2
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 10_000_000
        bias_2 = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

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

        # User 3
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        bias_3 = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=user_3_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=user_3_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, app_id=self.locking_app_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_3_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = itob(1) * 4
        txn_group = self.get_create_proposal_txn_group(user_address, proposal_id)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        proposal_creation_timestamp = block_timestamp

        # Cast Vote
        block_timestamp = proposal_creation_timestamp + DAY

        # User 1
        vote = 1
        txn_group = self.get_cast_vote_txn_group(user_address, proposal_id, vote, proposal_creation_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 2
        vote = 0
        txn_group = self.get_cast_vote_txn_group(user_2_address, proposal_id, vote, proposal_creation_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 3
        vote = 2
        txn_group = self.get_cast_vote_txn_group(user_3_address, proposal_id, vote, proposal_creation_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_3_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
