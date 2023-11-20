import unittest.mock
from base64 import b64encode, b64decode, b32decode
from datetime import datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError, get_suggested_params
from algosdk import transaction
from algosdk.abi import BoolType
from algosdk.account import generate_account
from algosdk.encoding import decode_address, _correct_padding
from algosdk.logic import get_application_address
from tinyman.governance import proposal_voting
from tinyman.governance.constants import DAY, WEEK
from tinyman.governance.event import decode_logs
from tinyman.governance.proposal_voting.events import proposal_voting_events
from tinyman.governance.proposal_voting.storage import (
    Proposal, ProposalVotingAppGlobalState, get_proposal_box_name,
    parse_box_proposal)
from tinyman.governance.proposal_voting.transactions import (
    generate_proposal_metadata, prepare_approve_proposal_transactions,
    prepare_cancel_proposal_transactions, prepare_cast_vote_transactions,
    prepare_create_proposal_transactions,
    prepare_disable_approval_requirement_transactions,
    prepare_execute_proposal_transactions,
    prepare_get_proposal_state_transactions, prepare_get_proposal_transactions,
    prepare_has_voted_transactions, prepare_set_manager_transactions,
    prepare_set_proposal_manager_transactions,
    prepare_set_proposal_threshold_numerator_transactions,
    prepare_set_proposal_threshold_transactions,
    prepare_set_quorum_threshold_transactions,
    prepare_set_voting_delay_transactions,
    prepare_set_voting_duration_transactions)
from tinyman.governance.transactions import \
    _prepare_budget_increase_transaction
from tinyman.governance.utils import (generate_cid_from_proposal_metadata,
                                      serialize_metadata)
from tinyman.governance.vault.transactions import (
    prepare_create_lock_transactions,
    prepare_increase_lock_amount_transactions, prepare_withdraw_transactions)
from tinyman.governance.vault.utils import (get_bias, get_slope,
                                            get_start_timestamp_of_week)
from tinyman.utils import TransactionGroup, bytes_to_int, int_to_bytes

from tests.common import BaseTestCase, ProposalVotingAppMixin, VaultAppMixin, ArbitraryExecutorAppMixin
from tests.constants import (PROPOSAL_VOTING_APP_ID, TINY_ASSET_ID,
                             VAULT_APP_ID, ARBITRARY_EXECUTOR_APP_ID,
                             proposal_voting_approval_program,
                             proposal_voting_clear_state_program,
                             arbitrary_executor_approval_program,
                             arbitrary_executor_clear_state_program)
from tests.proposal_voting.utils import get_proposal_voting_app_global_state
from tests.utils import (get_account_power_index_at, get_first_app_call_txn,
                         parse_box_account_power)
from tests.vault.utils import (get_account_state, get_slope_change_at,
                               get_vault_app_global_state)


class ArbitraryExecutorTestCase(VaultAppMixin, ProposalVotingAppMixin, ArbitraryExecutorAppMixin, BaseTestCase):

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
        self.create_proposal_voting_app(self.manager_address)
    
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
                approval_program=arbitrary_executor_approval_program.bytecode,
                clear_program=arbitrary_executor_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=16, num_byte_slices=16),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=3,
                app_args=[PROPOSAL_VOTING_APP_ID],
            )
        ])
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_id = block[b"txns"][0][b"apid"]
        
        # Global state
        self.assertEqual(
            self.ledger.global_states[app_id],
            {
                b'manager': decode_address(self.manager_address),
                b'proposal_voting_app_id': PROPOSAL_VOTING_APP_ID,
            }
        )
    
    def test_execute_proposal(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()
        user_3_sk, user_3_address = generate_account()
        user_4_sk, user_4_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.ledger.set_account_balance(user_3_address, 10_000_000)
        self.ledger.set_account_balance(user_4_address, 10_000_000)
        self.create_proposal_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][b'quorum_threshold'] = 7_000_000
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

        # Create the arbitrary executor transactions
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        arbitrary_transaction_sp = get_suggested_params()
        arbitrary_transaction_sp.fee = 10000
        arbitrary_transaction = transaction.AssetConfigTxn(
            sender=user_address,
            sp=arbitrary_transaction_sp,
            total=1000,
            default_frozen=False,
            unit_name="TINYRING",
            asset_name="Tiny Ring",
            manager=user_address,
            reserve=user_address,
            freeze=user_address,
            clawback=user_address,
            url="https://ipfs.io/ipfs/RANDOM_CID", 
            decimals=0,
        )

        executor_transaction = transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=self.sp,
            index=ARBITRARY_EXECUTOR_APP_ID,
            app_args=["validate_transaction", proposal_id],
            foreign_apps=[PROPOSAL_VOTING_APP_ID],
            boxes=[
                (PROPOSAL_VOTING_APP_ID, proposal_box_name)
            ],
        )
        arbitrary_executor_txn_group = TransactionGroup([
            executor_transaction,
            arbitrary_transaction
        ])

        # Create proposal
        txn_group = prepare_create_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            execution_hash=b32decode(_correct_padding(arbitrary_transaction.get_txid())),
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])

        # Approve proposal
        txn_group = prepare_approve_proposal_transactions(
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])

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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        
        with unittest.mock.patch("time.time", return_value=proposal.voting_end_timestamp + 10):
            self.assert_on_check_proposal_state(proposal_id, proposal_voting.constants.PROPOSAL_STATE_SUCCEEDED, user_address, user_sk, block_timestamp=proposal.voting_end_timestamp + 10)

        # Execute proposal
        self.create_arbitrary_executor_app(self.manager_address)

        arbitrary_executor_txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(arbitrary_executor_txn_group.signed_transactions, block_timestamp=proposal.voting_end_timestamp + 10)

        # Logs
        self.assertIsNotNone(self.ledger.assets[block[b"txns"][1][b"caid"]])
