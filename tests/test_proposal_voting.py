import unittest.mock
from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import WEEK, DAY, VAULT_APP_ID_KEY
from tinyman.governance.event import decode_logs
from tinyman.governance.proposal_voting.constants import PROPOSAL_ID_COUNTER_KEY, VOTING_DELAY_KEY, VOTING_DURATION_KEY, PROPOSAL_THRESHOLD_KEY, QUORUM_NUMERATOR_KEY, MANAGER_KEY, PROPOSAL_MANAGER_KEY, PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT
from tinyman.governance.proposal_voting.events import proposal_voting_events
from tinyman.governance.proposal_voting.storage import get_proposal_box_name, Proposal, parse_box_proposal
from tinyman.governance.proposal_voting.transactions import prepare_create_proposal_transactions, prepare_cast_vote_transactions, prepare_get_proposal_transactions, prepare_has_voted_transactions, prepare_cancel_proposal_transactions, prepare_execute_proposal_transactions
from tinyman.governance.vault.transactions import prepare_create_lock_transactions, prepare_withdraw_transactions, prepare_increase_lock_amount_transactions
from tinyman.governance.vault.utils import get_start_timestamp_of_week, get_bias, get_slope
from tinyman.utils import int_to_bytes, TransactionGroup

from tests.common import BaseTestCase, VaultAppMixin, ProposalVotingAppMixin
from tests.constants import TINY_ASSET_ID, VAULT_APP_ID, PROPOSAL_VOTING_APP_ID, proposal_voting_approval_program, proposal_voting_clear_state_program
from tests.proposal_voting.utils import get_end_timestamp_of_day
from tests.utils import parse_box_account_power, get_account_power_index_at
from tests.vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at


class ProposalVotingTestCase(VaultAppMixin, ProposalVotingAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 10_000_000)
        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

    def test_create_and_update_app(self):
        block_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        txn_group = TransactionGroup([
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=proposal_voting_approval_program.bytecode,
                clear_program=proposal_voting_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=16, num_byte_slices=16),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=3,
                app_args=[VAULT_APP_ID],
            )
        ])
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_id = block[b"txns"][0][b"apid"]

        self.assertDictEqual(
            self.ledger.global_states[app_id],
            {
                MANAGER_KEY: decode_address(self.app_creator_address),
                PROPOSAL_ID_COUNTER_KEY: 0,
                PROPOSAL_MANAGER_KEY: decode_address(self.app_creator_address),
                PROPOSAL_THRESHOLD_KEY: 5,
                QUORUM_NUMERATOR_KEY: 50,
                VAULT_APP_ID_KEY: VAULT_APP_ID,
                VOTING_DELAY_KEY: 2,
                VOTING_DURATION_KEY: 7,
            }
        )
        
        # Update
        # user_sk, user_address = generate_account()
        # self.ledger.set_account_balance(user_address, 10_000_000)
        # 
        # txn_group = TransactionGroup([
        #     transaction.ApplicationUpdateTxn(
        #         sender=user_address,
        #         index=app_id,
        #         sp=self.sp,
        #         approval_program=proposal_voting_clear_state_program.bytecode,
        #         clear_program=proposal_voting_clear_state_program.bytecode,
        #     )
        # ])
        # txn_group.sign_with_private_key(user_address, user_sk)
        # with self.assertRaises(Exception) as e:
        #     self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        txn_group = TransactionGroup([
            transaction.ApplicationUpdateTxn(
                sender=self.app_creator_address,
                index=app_id,
                sp=self.sp,
                approval_program=proposal_voting_clear_state_program.bytecode,
                clear_program=proposal_voting_clear_state_program.bytecode,
                app_args=[VAULT_APP_ID],
            )
        ])
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

    def test_create_proposal(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        box_data = {
            'index': 0,
            'creation_timestamp': block_timestamp,
            'voting_start_timestamp': get_end_timestamp_of_day(block_timestamp) + (2 * DAY),
            'voting_end_timestamp': get_end_timestamp_of_day(block_timestamp) + (2 * DAY) + WEEK,
            'snapshot_total_voting_power': bias_1 + bias_2,
            'vote_count': 0,
            'is_cancelled': False,
            'is_executed': False,
            'is_quorum_reached': False,
            'quorum_numerator': 50,
            'against_voting_power': 0,
            'for_voting_power': 0,
            'abstain_voting_power': 0,
            'proposer_address': user_address
        }

        # Logs
        logs = block[b'txns'][1][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 2)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'proposal',
                'proposal_id': list(proposal_id),
                **box_data
            }
        )
        self.assertDictEqual(
            events[1],
            {
                'event_name': 'create_proposal',
                'user_address': user_address,
                'proposal_id': list(proposal_id)
            }
        )
        
        # Global State
        self.assertDictEqual(
            self.ledger.global_states[PROPOSAL_VOTING_APP_ID],
            {
                VAULT_APP_ID_KEY: VAULT_APP_ID,
                MANAGER_KEY: decode_address(self.app_creator_address),
                PROPOSAL_MANAGER_KEY: decode_address(self.app_creator_address),
                PROPOSAL_ID_COUNTER_KEY: 1,
                PROPOSAL_THRESHOLD_KEY: 5,  # %5
                QUORUM_NUMERATOR_KEY: 50,
                VOTING_DELAY_KEY: 2,
                VOTING_DURATION_KEY: 7,
            },
        )
        
        # Box
        proposal_box_name = get_proposal_box_name(proposal_id)
        self.assertEqual(
            parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name]),
            Proposal(**box_data)
        )

        # Creating a proposal with the same id fails
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(!exists)')

        # User 2 doesn't have enough voting power for creating a proposal
        proposal_id = int_to_bytes(2) * 4
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_2_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert((itob(account_voting_power) b* itob(100)) b>= (itob(total_voting_power) b* itob(app_global_get(PROPOSAL_THRESHOLD_KEY))))')

    def test_cast_vote(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()
        user_3_sk, user_3_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.ledger.set_account_balance(user_3_address, 10_000_000)
        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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

        #  Create lock 2
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

        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create lock 3
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
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        
        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])

        # Cast Vote
        proposal_creation_timestamp = proposal.creation_timestamp
        block_timestamp = proposal.voting_start_timestamp

        # User 1
        vote = 1
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=vote,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp),
            create_attendance_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # User 2
        vote = 0
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_2_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=vote,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_2_address, proposal_creation_timestamp),
            create_attendance_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # User 3
        vote = 2
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_3_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=vote,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_3_address, proposal_creation_timestamp),
            create_attendance_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_3_address, user_3_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

    def test_cast_vote_after_withdraw(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

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

        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        vote = 1
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=vote,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp),
            create_attendance_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError):
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

    def test_cast_vote_after_increase_lock_amount(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

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
        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        block_timestamp = proposal.voting_start_timestamp

        vote = 1
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=vote,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp),
            create_attendance_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        account_powers = parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][decode_address(user_address) + int_to_bytes(0)])
        account_power = account_powers[-1]
        voting_power = account_power.bias - get_bias(account_power.slope, (proposal.creation_timestamp - account_power.timestamp))

        self.assertEqual(proposal.for_voting_power, voting_power)

    def test_get_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Get Proposal Info
        block_timestamp += 1
        txn_group = prepare_get_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

    def test_has_voted(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Check if it has voted
        block_timestamp += 1

        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        txn_group = prepare_has_voted_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

    def test_cancel_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        proposal_creation_timestamp = block_timestamp

        # Cancel proposal
        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal_manager_address = self.app_creator_address
        proposal_manager_sk = self.app_creator_sk
        block_timestamp += 1

        txn_group = prepare_cancel_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(proposal_manager_address, proposal_manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.is_cancelled, True)

        # Try to cast vote
        block_timestamp = proposal.voting_start_timestamp + DAY
       
        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        vote = 1
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=vote,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp),
            create_attendance_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_cancelled == BYTES_FALSE)')

    def test_execute_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])

        # Execute Proposal
        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal_manager_address = self.app_creator_address
        proposal_manager_sk = self.app_creator_sk
        block_timestamp += 1

        txn_group = prepare_execute_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(proposal_manager_address, proposal_manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], "assert(proposal.voting_end_timestamp < Global.LatestTimestamp)")

        block_timestamp = proposal.voting_end_timestamp + 1
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.is_executed, True)
