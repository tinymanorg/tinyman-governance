import unittest.mock
from datetime import datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.abi import BoolType
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import WEEK, DAY
from tinyman.governance.event import decode_logs
from tinyman.governance import proposal_voting
from tinyman.governance.proposal_voting.events import proposal_voting_events
from tinyman.governance.proposal_voting.storage import ProposalVotingAppGlobalState
from tinyman.governance.proposal_voting.storage import get_proposal_box_name, Proposal, parse_box_proposal
from tinyman.governance.proposal_voting.transactions import prepare_disable_approval_requirement_transactions, prepare_create_proposal_transactions, prepare_cast_vote_transactions, \
    prepare_get_proposal_transactions, prepare_has_voted_transactions, prepare_cancel_proposal_transactions, prepare_execute_proposal_transactions, prepare_approve_proposal_transactions, \
    prepare_set_proposal_manager_transactions, prepare_set_manager_transactions, prepare_set_voting_delay_transactions, prepare_set_voting_duration_transactions, \
    prepare_set_proposal_threshold_transactions, prepare_set_quorum_numerator_transactions, generate_proposal_metadata, prepare_get_proposal_state_transactions
from tinyman.governance.transactions import _prepare_budget_increase_transaction
from tinyman.governance.utils import generate_cid_from_proposal_metadata, serialize_metadata
from tinyman.governance.vault.transactions import prepare_create_lock_transactions, prepare_withdraw_transactions, prepare_increase_lock_amount_transactions
from tinyman.governance.vault.utils import get_start_timestamp_of_week, get_bias, get_slope
from tinyman.utils import int_to_bytes, bytes_to_int, TransactionGroup

from tests.common import BaseTestCase, VaultAppMixin, ProposalVotingAppMixin
from tests.constants import TINY_ASSET_ID, VAULT_APP_ID, PROPOSAL_VOTING_APP_ID, proposal_voting_approval_program, proposal_voting_clear_state_program
from tests.proposal_voting.utils import get_proposal_voting_app_global_state
from tests.utils import get_first_app_call_txn
from tests.utils import parse_box_account_power, get_account_power_index_at
from tests.vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at


class ProposalVotingTestCase(VaultAppMixin, ProposalVotingAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.manager_sk, cls.manager_address = generate_account()
        cls.proposal_manager_sk, cls.proposal_manager_address = generate_account()
        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.manager_address, 10_000_000)
        self.ledger.set_account_balance(self.proposal_manager_address, 10_000_000)
        self.create_vault_app(self.manager_address)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

    def assert_on_check_proposal_state(self, proposal_id, expected_state, sender, sender_sk, block_timestamp):
        txn_group = prepare_get_proposal_state_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=sender,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(sender, sender_sk)
        
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        self.assertEqual(len(logs), 1)
        proposal_state = logs[0][4:]
        self.assertEqual(bytes_to_int(proposal_state), expected_state)

    def test_create_app(self):
        block_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        txn_group = TransactionGroup([
            transaction.ApplicationCreateTxn(
                sender=self.manager_address,
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
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_id = block[b"txns"][0][b"apid"]
        
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, app_id)
        self.assertEqual(
            proposal_voting_app_global_state,
            ProposalVotingAppGlobalState(
                vault_app_id=VAULT_APP_ID,
                proposal_id_counter=0,
                voting_delay=2,
                voting_duration=7,
                proposal_threshold=5,
                quorum_numerator=50,
                approval_requirement=1,
                manager=decode_address(self.manager_address),
                proposal_manager=decode_address(self.manager_address),
            )
        )

    def test_update_app(self):
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
                sender=self.manager_address,
                index=PROPOSAL_VOTING_APP_ID,
                sp=self.sp,
                approval_program=proposal_voting_clear_state_program.bytecode,
                clear_program=proposal_voting_clear_state_program.bytecode,
            )
        ])
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions)
        
    def test_create_proposal(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        proposal_metadata = generate_proposal_metadata(
            title="Proposal 1",
            description="",
            category="",
            discussion_url="",
            poll_url="",
        )

        proposal_id = generate_cid_from_proposal_metadata(proposal_metadata)
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
            'voting_start_timestamp': 0,
            'voting_end_timestamp': 0,
            'snapshot_total_voting_power': bias_1 + bias_2,
            'vote_count': 0,
            'is_approved': False,
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
                'proposal_id': list(proposal_id.encode()),
                **box_data
            }
        )
        self.assertDictEqual(
            events[1],
            {
                'event_name': 'create_proposal',
                'user_address': user_address,
                'proposal_id': list(proposal_id.encode())
            }
        )
        
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_id_counter, 1)

        # Box
        proposal_box_name = get_proposal_box_name(proposal_id)
        self.assertEqual(
            parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name]),
            Proposal(**box_data)
        )
        
        # Disable approval requirement. Voting timestamps should be set.
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][b'approval_requirement'] = 0
        # Update settings
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][b'voting_delay'] = 5
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][b'voting_duration'] = 10
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][b'quorum_numerator'] = 20

        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 2"})
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
        
        # Start and end timestamps are assigned
        self.assertTrue(proposal.voting_start_timestamp != 0)
        self.assertTrue(proposal.voting_end_timestamp != 0)
        # Voting Delay
        self.assertEqual((proposal.voting_start_timestamp - block_timestamp) // DAY, 6)
        # Voting Duration
        self.assertEqual(proposal.voting_end_timestamp - proposal.voting_start_timestamp, DAY * 10)
        # Quorum numerator
        self.assertEqual(proposal.quorum_numerator, 20)
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            self.assertEqual(proposal.state, proposal_voting.constants.PROPOSAL_STATE_PENDING)
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_PENDING, user_address, user_sk, block_timestamp=block_timestamp)

        # Creating a proposal with the same id fails
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'box<Proposal> proposal = CreateBox(proposal_box_name)')

        # User 2 doesn't have enough voting power for creating a proposal
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 3"})
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
        user_4_sk, user_4_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.ledger.set_account_balance(user_3_address, 10_000_000)
        self.ledger.set_account_balance(user_4_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        slope = get_slope(amount)
        user_1_bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

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
        slope = get_slope(amount)
        user_2_bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

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
        slope = get_slope(amount)
        user_3_bias = get_bias(slope, (lock_end_timestamp - block_timestamp))
        
        # Create lock 4
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_4_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_4_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_4_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_4_address, user_4_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        slope = get_slope(amount)
        user_4_bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        # Create proposal
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
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
        self.assertEqual(proposal.is_approved, False)
        self.assertEqual(proposal.voting_start_timestamp, 0)
        self.assertEqual(proposal.voting_end_timestamp, 0)
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            self.assertEqual(proposal.state, proposal_voting.constants.PROPOSAL_STATE_WAITING_FOR_APPROVAL)
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_WAITING_FOR_APPROVAL, user_address, user_sk, block_timestamp=block_timestamp)

        # Approve proposal
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Logs
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_name"], "proposal")
        self.assertDictEqual(
            events[1], 
            {
                "event_name": "approve_proposal",
                "user_address": self.proposal_manager_address,
                "proposal_id": list(proposal_id.encode()),
            }
        )
    
        # Box
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.is_approved, True)
        self.assertTrue(proposal.voting_start_timestamp != 0)
        self.assertTrue(proposal.voting_end_timestamp != 0)
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            self.assertEqual(proposal.state, proposal_voting.constants.PROPOSAL_STATE_PENDING)
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_PENDING, user_address, user_sk, block_timestamp=block_timestamp)

        # Cast Vote
        proposal_creation_timestamp = proposal.creation_timestamp
        block_timestamp = proposal.voting_start_timestamp

        with unittest.mock.patch("time.time", return_value=proposal.voting_start_timestamp):
            self.assertEqual(proposal.state, proposal_voting.constants.PROPOSAL_STATE_ACTIVE)
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_ACTIVE, user_address, user_sk, block_timestamp=proposal.voting_start_timestamp)

        # User 4
        vote = 1
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_4_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=vote,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_4_address, proposal_creation_timestamp),
            create_attendance_sheet_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_4_address, user_4_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.against_voting_power, 0)
        self.assertEqual(proposal.for_voting_power, user_4_bias)
        self.assertEqual(proposal.abstain_voting_power, 0)
        self.assertEqual(proposal.is_quorum_reached, False)

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
            create_attendance_sheet_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.against_voting_power, user_2_bias)
        self.assertEqual(proposal.for_voting_power, user_4_bias)
        self.assertEqual(proposal.abstain_voting_power, 0)
        self.assertEqual(proposal.is_quorum_reached, False)

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
            create_attendance_sheet_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_3_address, user_3_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.against_voting_power, user_2_bias)
        self.assertEqual(proposal.for_voting_power, user_4_bias)
        self.assertEqual(proposal.abstain_voting_power, user_3_bias)
        self.assertEqual(proposal.is_quorum_reached, False)
        
        with unittest.mock.patch("time.time", return_value=proposal.voting_end_timestamp + 10):
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_DEFEATED, user_address, user_sk, block_timestamp=proposal.voting_end_timestamp + 10)

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
            create_attendance_sheet_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Logs
        logs = block[b'txns'][1][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_name"], "proposal")
        self.assertDictEqual(
            events[1],
            {
                "event_name": "cast_vote",
                "user_address": user_address,
                "proposal_id": list(proposal_id.encode()),
                "vote": vote,
                "voting_power": ANY
            }
        )

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.against_voting_power, user_2_bias)
        self.assertEqual(proposal.for_voting_power, user_4_bias + user_1_bias)
        self.assertEqual(proposal.abstain_voting_power, user_3_bias)
        self.assertEqual(proposal.is_quorum_reached, True)
        
        with unittest.mock.patch("time.time", return_value=proposal.voting_end_timestamp + 10):
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_SUCCEEDED, user_address, user_sk, block_timestamp=proposal.voting_end_timestamp + 10)

    def test_cast_vote_after_withdraw(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][b'approval_requirement'] = 0

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
        block_timestamp = lock_end_timestamp - DAY
        self.create_checkpoints(user_address, user_sk, block_timestamp)
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
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

        # Withdraw
        block_timestamp = lock_end_timestamp + DAY
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
        block_timestamp = block_timestamp + WEEK

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
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal.creation_timestamp),
            create_attendance_sheet_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        account_powers = parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][decode_address(user_address) + int_to_bytes(0)])
        account_power = account_powers[0]
        voting_power = account_power.bias - get_bias(account_power.slope, (proposal.creation_timestamp - account_power.timestamp))
        self.assertEqual(proposal.for_voting_power, voting_power)

    def test_cast_vote_after_increase_lock_amount(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
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

        # Approve proposal
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

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
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal.creation_timestamp),
            create_attendance_sheet_box=True,
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
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
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
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        self.assertEqual(len(logs), 1)
        proposal_data = logs[-1][4:]
        proposal = parse_box_proposal(proposal_data)
        self.assertEqual(proposal.proposer_address, user_address)

    def test_has_voted(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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

        # Disable approval requirement. Voting timestamps should be set.
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][b'approval_requirement'] = 0

        # Create proposal
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
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
        
        test_cases = [
            # Sender, Address to check, PK, expected output
            (user_address, user_address, user_sk, False),
            (user_address, user_2_address, user_sk, False),
            (user_2_address, user_address, user_2_sk, False),
            (user_2_address, user_2_address, user_2_sk, False),
        ]
        for test_case in test_cases:
            txn_group = prepare_has_voted_transactions(
                proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
                sender=test_case[0],
                address_to_check=test_case[1],
                proposal_id=proposal_id,
                proposal=proposal,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(test_case[0], test_case[2])
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            app_call_txn = get_first_app_call_txn(block[b'txns'])
            logs = app_call_txn[b'dt'][b'lg']
            self.assertEqual(len(logs), 1)
            self.assertEqual(BoolType().decode(logs[-1][4:]), test_case[3])
        
        # Cast Vote
        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        block_timestamp += 3 * DAY
        vote = 1
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=vote,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal.creation_timestamp),
            create_attendance_sheet_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])

        test_cases = [
            # Sender, Address to check, PK, expected output
            (user_address, user_address, user_sk, 1),
            (user_address, user_2_address, user_sk, 0),
            (user_2_address, user_address, user_2_sk, 1),
            (user_2_address, user_2_address, user_2_sk, 0),
        ]
        for test_case in test_cases:
            txn_group = prepare_has_voted_transactions(
                proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
                sender=test_case[0],
                address_to_check=test_case[1],
                proposal_id=proposal_id,
                proposal=proposal,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(test_case[0], test_case[2])
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            app_call_txn = get_first_app_call_txn(block[b'txns'])
            logs = app_call_txn[b'dt'][b'lg']
            self.assertEqual(len(logs), 1)
            self.assertEqual(BoolType().decode(logs[-1][4:]), test_case[3])

    def test_cancel_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
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

        # Approve proposal
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Cancel proposal
        proposal_box_name = get_proposal_box_name(proposal_id)
        block_timestamp += 1

        txn_group = prepare_cancel_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        # Logs
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event_name'], 'proposal')
        self.assertEqual(
            events[1],
            {
                'event_name': 'cancel_proposal',
                'user_address': self.proposal_manager_address,
                'proposal_id': list(proposal_id.encode()),
            }
        )
        
        # Box
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.is_cancelled, True)
        
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            self.assertEqual(proposal.state, proposal_voting.constants.PROPOSAL_STATE_CANCELLED)
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_CANCELLED, user_address, user_sk, block_timestamp=block_timestamp)
        
        # It runs once
        block_timestamp += DAY
        txn_group = prepare_cancel_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_cancelled == BYTES_FALSE)')

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
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal.creation_timestamp),
            create_attendance_sheet_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_cancelled == BYTES_FALSE)')

    def test_execute_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
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
        
        # Approve proposal
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        proposal_box_name = get_proposal_box_name(proposal_id)
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])

        block_timestamp = proposal.voting_start_timestamp + 1
        txn_group = prepare_cast_vote_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            vote=1,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal.creation_timestamp),
            create_attendance_sheet_box=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Execute Proposal
        proposal_box_name = get_proposal_box_name(proposal_id)
        block_timestamp += 1

        txn_group = prepare_execute_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], "assert(proposal.voting_end_timestamp < Global.LatestTimestamp)")

        block_timestamp = proposal.voting_end_timestamp + 1
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Logs
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event_name'], 'proposal')
        self.assertEqual(
            events[1],
            {
                'event_name': 'execute_proposal',
                'user_address': self.proposal_manager_address,
                'proposal_id': list(proposal_id.encode()),
            }
        )

        # Box
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.is_executed, True)
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            self.assertEqual(proposal.state, proposal_voting.constants.PROPOSAL_STATE_EXECUTED)
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_EXECUTED, user_address, user_sk, block_timestamp=block_timestamp)


        # It runs once
        block_timestamp += DAY
        txn_group = prepare_cancel_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_executed == BYTES_FALSE)')

    def test_approve_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

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

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.is_approved, False)
        self.assertEqual(proposal.voting_start_timestamp, 0)
        self.assertEqual(proposal.voting_end_timestamp, 0)

        # Approve proposal
        block_timestamp += DAY
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        # Logs
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event_name'], 'proposal')
        self.assertEqual(
            events[1],
            {
                'event_name': 'approve_proposal',
                'user_address': self.proposal_manager_address,
                'proposal_id': list(proposal_id.encode()),
            }
        )

        voting_start_timestamp = DAY * ((block_timestamp // DAY) + 3)
        voting_end_timestamp = voting_start_timestamp + 7 * DAY
        
        # Box
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.is_approved, True)
        self.assertEqual(proposal.voting_start_timestamp, voting_start_timestamp)
        self.assertEqual(proposal.voting_end_timestamp, voting_end_timestamp)
        
        # It runs once
        block_timestamp += DAY
        self.create_checkpoints(user_address, user_sk, block_timestamp)
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_approved == BYTES_FALSE)')

        # User cannot call it
        block_timestamp += DAY
        self.create_checkpoints(user_address, user_sk, block_timestamp)
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        # Disable approval requirement. Voting timestamps should be set.
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][b'approval_requirement'] = 0
        
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 2"})
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
        
        block_timestamp += DAY
        self.create_checkpoints(user_address, user_sk, block_timestamp)
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(!proposal.voting_start_timestamp)')

    def test_prepare_disable_approval_requirement_transactions(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)
        
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        txn_group = prepare_disable_approval_requirement_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        txn_group = prepare_disable_approval_requirement_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)

        # Logs
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0],
            {
                'event_name': 'disable_approval_requirement',
            }
        )
        txn_group = prepare_disable_approval_requirement_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(app_global_get(APPROVAL_REQUIREMENT_KEY))')

    def test_set_manager(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Test address validation
        txn_group = prepare_set_manager_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(MANAGER_KEY))')

        # Set user as manager
        txn_group = prepare_set_manager_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.manager_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_manager',
                'manager': user_address
            }
        )
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.manager, decode_address(user_address))

        # Set back app creator as manager
        txn_group = prepare_set_manager_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            new_manager_address=self.manager_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_manager',
                'manager': self.manager_address
            }
        )
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.manager, decode_address(self.manager_address))

    def test_set_proposal_manager(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Test address validation
        txn_group = prepare_set_proposal_manager_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(MANAGER_KEY))')

        # Set user as manager
        txn_group = prepare_set_proposal_manager_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.manager_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_proposal_manager',
                'proposal_manager': user_address
            }
        )
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_manager, decode_address(user_address))

        # Set back app creator as manager
        txn_group = prepare_set_proposal_manager_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.manager_address,
            new_manager_address=self.manager_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_proposal_manager',
                'proposal_manager': self.manager_address
            }
        )
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_manager, decode_address(self.manager_address))

    def test_set_voting_delay(self):
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Permission
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        txn_group = prepare_set_voting_delay_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            new_voting_delay=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')
        
        txn_group = prepare_set_voting_delay_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.manager_address,
            new_voting_delay=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')        

        # Success
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.voting_delay, 2)

        txn_group = prepare_set_voting_delay_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            new_voting_delay=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_voting_delay',
                'voting_delay': 10,
            }
        )

        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.voting_delay, 10)

    def test_set_voting_duration(self):
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Permission
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        txn_group = prepare_set_voting_duration_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            new_voting_duration=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        txn_group = prepare_set_voting_duration_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.manager_address,
            new_voting_duration=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        # Success
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.voting_duration, 7)

        txn_group = prepare_set_voting_duration_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            new_voting_duration=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_voting_duration',
                'voting_duration': 10,
            }
        )

        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.voting_duration, 10)
        
    def test_set_proposal_threshold(self):
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Permission
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        txn_group = prepare_set_proposal_threshold_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            new_proposal_threshold=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        txn_group = prepare_set_proposal_threshold_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.manager_address,
            new_proposal_threshold=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        # Success
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_threshold, 5)

        txn_group = prepare_set_proposal_threshold_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            new_proposal_threshold=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_proposal_threshold',
                'proposal_threshold': 10,
            }
        )

        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_threshold, 10)
        
    def test_set_quorum_numerator(self):
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Permission
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        txn_group = prepare_set_quorum_numerator_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=user_address,
            new_quorum_numerator=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        txn_group = prepare_set_quorum_numerator_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.manager_address,
            new_quorum_numerator=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        # Success
        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.quorum_numerator, 50)

        txn_group = prepare_set_quorum_numerator_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            new_quorum_numerator=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=proposal_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_quorum_numerator',
                'quorum_numerator': 10,
            }
        )

        # Global state
        proposal_voting_app_global_state = get_proposal_voting_app_global_state(self.ledger, PROPOSAL_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.quorum_numerator, 10)

    def test_budget_increase(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(PROPOSAL_VOTING_APP_ID), proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        txn = _prepare_budget_increase_transaction(
            sender=user_address,
            sp=self.sp,
            index=PROPOSAL_VOTING_APP_ID,
        )
        txn_group = TransactionGroup([txn])
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions)

        txn = _prepare_budget_increase_transaction(
            sender=user_address,
            sp=self.sp,
            index=PROPOSAL_VOTING_APP_ID,
            foreign_apps=[VAULT_APP_ID],
            extra_app_args=[2],
        )
        txn.fee *= 3
        txn_group = TransactionGroup([txn])
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        app_call_txn = get_first_app_call_txn(block[b'txns'], ignore_budget_increase=False)
        inner_txns = app_call_txn[b'dt'][b'itx']
        self.assertEqual(len(inner_txns), 2)
