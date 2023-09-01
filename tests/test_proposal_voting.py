import unittest.mock
from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import WEEK, DAY
from tinyman.governance.vault.transactions import prepare_create_lock_transactions, prepare_withdraw_transactions, prepare_increase_lock_amount_transactions
from tinyman.governance.vault.utils import get_start_timestamp_of_week, get_bias, get_slope, get_start_time_of_day
from tinyman.utils import int_to_bytes

from common.constants import TINY_ASSET_ID
from common.constants import VAULT_APP_ID, PROPOSAL_VOTING_APP_ID
from common.utils import sign_txns, parse_box_proposal, parse_box_account_power
from proposal_voting.constants import PROPOSAL_BOX_PREFIX
from proposal_voting.transactions import prepare_create_proposal_transactions, prepare_cast_vote_transactions, prepare_get_proposal_transactions, prepare_cancel_proposal_transactions, prepare_execute_proposal_transactions, prepare_has_voted_transactions
from tests.common import BaseTestCase, VaultAppMixin, ProposalVotingAppMixin
from vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at


class ProposalVotingTestCase(VaultAppMixin, ProposalVotingAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.create_vault_app(self.app_creator_address)
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

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

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

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_2_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_2_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create proposal successfully
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        self.assertDictEqual(
            self.ledger.global_states[PROPOSAL_VOTING_APP_ID],
            {
                b'vault_app_id': VAULT_APP_ID,
                b'manager': decode_address(self.app_creator_address),
                b'proposal_manager': decode_address(self.app_creator_address),
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
        proposal_id = int_to_bytes(2) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_2_address, proposal_id, self.sp)
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

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # User 2
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_2_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_2_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_2_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )

        # print_boxes(self.ledger.boxes[VAULT_APP_ID])
        # breakpoint()
        # print(txn_group.transactions[0].__dict__)
        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # User 3
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_3_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_3_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_3_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_3_address, user_3_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        proposal_creation_timestamp = block_timestamp

        # Cast Vote
        block_timestamp = proposal_creation_timestamp + DAY

        # User 1
        vote = 1
        txn_group = prepare_cast_vote_transactions(self.ledger, user_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 2
        vote = 0
        txn_group = prepare_cast_vote_transactions(self.ledger, user_2_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # User 3
        vote = 2
        txn_group = prepare_cast_vote_transactions(self.ledger, user_3_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_3_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

    def test_cast_vote_after_withdraw(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 15 * WEEK
        amount = 100_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        proposal_creation_timestamp = block_timestamp

        # Withdraw
        block_timestamp = lock_end_timestamp + WEEK
        txn_group = prepare_withdraw_transactions(
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=user_address,
            account_state=get_account_state(self.ledger, user_address),
            suggested_params=self.sp,
            app_call_note=None,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Cast Vote
        block_timestamp = block_timestamp + DAY

        vote = 1
        txn_group = prepare_cast_vote_transactions(self.ledger, user_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        with self.assertRaises(LogicEvalError):
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

    def test_cast_vote_after_increase_lock_amount(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 15 * WEEK
        amount = 100_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        proposal_creation_timestamp = block_timestamp

        # Increase
        amount = 50_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_increase_lock_amount_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                suggested_params=self.sp,
                app_call_note=None,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Cast Vote
        block_timestamp = proposal_creation_timestamp + DAY

        vote = 1
        txn_group = prepare_cast_vote_transactions(self.ledger, user_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        account_powers = parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][decode_address(user_address) + int_to_bytes(0)])
        account_power = account_powers[-1]
        voting_power = account_power.bias - get_bias(account_power.slope, (block_timestamp - account_power.timestamp))

        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        self.assertEqual(parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])["for_vote_amount"], voting_power)

    def test_get_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 15 * WEEK
        amount = 100_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # Get Proposal Info
        block_timestamp += 1
        txn_group = prepare_get_proposal_transactions(user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

    def test_has_voted(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 15 * WEEK
        amount = 100_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # Check if it has voted
        block_timestamp += 1
        txn_group = prepare_has_voted_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

    def test_cancel_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 15 * WEEK
        amount = 100_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        proposal_creation_timestamp = block_timestamp

        # Cancel proposal
        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        proposal_manager_address = self.app_creator_address
        proposal_manager_sk = self.app_creator_sk
        block_timestamp += 1

        txn_group = prepare_cancel_proposal_transactions(proposal_manager_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, proposal_manager_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])["is_cancelled"], 1)

        # Try to cast vote
        block_timestamp = proposal_creation_timestamp + DAY

        vote = 1
        txn_group = prepare_cast_vote_transactions(self.ledger, user_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_cancelled == BYTES_FALSE)')

    def test_execute_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 15 * WEEK
        amount = 100_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create proposal
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.ledger, user_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        proposal_creation_timestamp = block_timestamp

        # Execute Proposal
        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        proposal_manager_address = self.app_creator_address
        proposal_manager_sk = self.app_creator_sk
        block_timestamp += 1

        txn_group = prepare_execute_proposal_transactions(proposal_manager_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, proposal_manager_sk)
        proposal_data = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], "assert(proposal.voting_end_timestamp < Global.LatestTimestamp)")

        block_timestamp = proposal_data["voting_end_timestamp"] + 1
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])["is_executed"], 1)

        # Try to cast vote
        block_timestamp = proposal_creation_timestamp + DAY

        vote = 1
        txn_group = prepare_cast_vote_transactions(self.ledger, user_address, proposal_id, vote, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        # TODO missing check for execution
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_executed == BYTES_FALSE)')
