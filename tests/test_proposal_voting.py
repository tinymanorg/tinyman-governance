from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from common.constants import TINY_ASSET_ID, WEEK, DAY
from common.utils import get_start_timestamp_of_week, itob, sign_txns, parse_box_proposal, get_start_time_of_day, get_bias, get_slope
from vault.transactions import prepare_create_lock_txn_group
from proposal_voting.constants import PROPOSAL_BOX_PREFIX
from proposal_voting.transactions import prepare_create_proposal_txn_group, prepare_cast_vote_txn_group
from tests.common import BaseTestCase, VaultAppMixin, ProposalVotingAppMixin
from common.constants import VAULT_APP_ID, PROPOSAL_VOTING_APP_ID

class ProposalVotingTestCase(VaultAppMixin, ProposalVotingAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

    def test_create_proposal(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock 1
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 52 * WEEK
        amount = 100_000_000
        bias_1 = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )
        txn_group = prepare_create_lock_txn_group(self.ledger, user_address=user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, sp=self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 2
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 10_000_000
        bias_2 = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_2_address
        )
        txn_group = prepare_create_lock_txn_group(self.ledger, user_address=user_2_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, sp=self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # Create proposal successfully
        proposal_id = itob(1) * 4
        txn_group = prepare_create_proposal_txn_group(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        # print(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        self.assertDictEqual(
            self.ledger.global_states[PROPOSAL_VOTING_APP_ID],
            {
                b'vault_app_id': VAULT_APP_ID,
                b'manager': decode_address(self.app_creator_address),
                b'proposal_id_counter': 1,
                b'proposal_threshold': 10,  # %10
                b'quorum_numerator': 50,
                b'voting_delay': 1,
                b'voting_duration': 7
            }
        )

        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        self.assertDictEqual(
            parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name]),
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
        txn_group = prepare_create_proposal_txn_group(self.ledger, user_2_address, proposal_id, self.sp)
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
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock 1
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 15 * WEEK
        amount = 100_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )
        txn_group = prepare_create_lock_txn_group(self.ledger, user_address=user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, sp=self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 2
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_2_address
        )
        txn_group = prepare_create_lock_txn_group(self.ledger, user_address=user_2_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, sp=self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 3
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_3_address
        )
        txn_group = prepare_create_lock_txn_group(self.ledger, user_address=user_3_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp, sp=self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_3_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = itob(1) * 4
        txn_group = prepare_create_proposal_txn_group(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        proposal_creation_timestamp = block_timestamp

        # Cast Vote
        block_timestamp = proposal_creation_timestamp + DAY

        # User 1
        vote = 1
        txn_group = prepare_cast_vote_txn_group(self.ledger, user_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 2
        vote = 0
        txn_group = prepare_cast_vote_txn_group(self.ledger, user_2_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 3
        vote = 2
        txn_group = prepare_cast_vote_txn_group(self.ledger, user_3_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_3_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
