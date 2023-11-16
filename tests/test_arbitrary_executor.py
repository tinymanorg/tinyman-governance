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
                             VAULT_APP_ID, proposal_voting_approval_program,
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