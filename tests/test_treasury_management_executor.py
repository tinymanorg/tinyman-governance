import unittest.mock
from base64 import b64encode, b64decode, b32decode
from datetime import datetime
from hashlib import sha256
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError, get_suggested_params
from algosdk import transaction
from algosdk.abi import BoolType
from algosdk.account import generate_account
from algosdk.encoding import decode_address, encode_address, _correct_padding
from algosdk.logic import get_application_address
from tinyman.governance import proposal_voting
from tinyman.governance.constants import DAY, WEEK
from tinyman.governance.event import decode_logs
from tinyman.governance.proposal_voting.events import proposal_voting_events
from tinyman.governance.proposal_voting.storage import (
    Proposal,
    ProposalVotingAppGlobalState,
    get_proposal_box_name,
    parse_box_proposal,
)
from tinyman.governance.proposal_voting.transactions import (
    generate_proposal_metadata,
    prepare_approve_proposal_transactions,
    prepare_cancel_proposal_transactions,
    prepare_cast_vote_transactions,
    prepare_create_proposal_transactions,
    prepare_disable_approval_requirement_transactions,
    prepare_execute_proposal_transactions,
    prepare_get_proposal_state_transactions,
    prepare_get_proposal_transactions,
    prepare_has_voted_transactions,
    prepare_set_manager_transactions,
    prepare_set_proposal_manager_transactions,
    prepare_set_proposal_threshold_numerator_transactions,
    prepare_set_proposal_threshold_transactions,
    prepare_set_quorum_threshold_transactions,
    prepare_set_voting_delay_transactions,
    prepare_set_voting_duration_transactions,
)
from tinyman.governance.transactions import _prepare_budget_increase_transaction
from tinyman.governance.utils import (
    generate_cid_from_proposal_metadata,
    serialize_metadata,
)
from tinyman.governance.vault.transactions import (
    prepare_create_lock_transactions,
    prepare_increase_lock_amount_transactions,
    prepare_withdraw_transactions,
)
from tinyman.governance.vault.utils import (
    get_bias,
    get_slope,
    get_start_timestamp_of_week,
)
from tinyman.utils import TransactionGroup, bytes_to_int, int_to_bytes

from tests.common import (
    BaseTestCase,
    ProposalVotingAppMixin,
    VaultAppMixin,
    TreasuryManagementExecutorMixin,
    get_rawbox_from_proposal,
    lpad,
)
from tests.constants import (
    AMM_V2_APP_ID,
    PROPOSAL_VOTING_APP_ID,
    TINY_ASSET_ID,
    VAULT_APP_ID,
    TREASURY_MANAGEMENT_EXECUTOR_APP_ID,
    amm_pool_template,
    treasury_management_executor_approval_program,
    treasury_management_executor_clear_state_program,
)
from tests.proposal_voting.utils import get_proposal_voting_app_global_state
from tests.utils import (
    get_account_power_index_at,
    get_first_app_call_txn,
    parse_box_account_power,
)
from tests.vault.utils import (
    get_account_state,
    get_slope_change_at,
    get_vault_app_global_state,
)


class TreasuryManagementExecutorTestCase(
    VaultAppMixin, ProposalVotingAppMixin, TreasuryManagementExecutorMixin, BaseTestCase
):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.manager_sk, cls.manager_address = generate_account()
        cls.proposal_manager_sk, cls.proposal_manager_address = generate_account()
        cls.vault_app_creation_timestamp = int(
            datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp()
        )

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

        txn_group = TransactionGroup(
            [
                transaction.ApplicationCreateTxn(
                    sender=self.manager_address,
                    sp=self.sp,
                    on_complete=transaction.OnComplete.NoOpOC,
                    approval_program=treasury_management_executor_approval_program.bytecode,
                    clear_program=treasury_management_executor_clear_state_program.bytecode,
                    global_schema=transaction.StateSchema(
                        num_uints=16, num_byte_slices=16
                    ),
                    local_schema=transaction.StateSchema(
                        num_uints=0, num_byte_slices=0
                    ),
                    extra_pages=3,
                    app_args=[PROPOSAL_VOTING_APP_ID],
                )
            ]
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(
            txn_group.signed_transactions, block_timestamp=block_timestamp
        )
        app_id = block[b"txns"][0][b"apid"]

        # Global state
        self.assertEqual(
            self.ledger.global_states[app_id],
            {
                b"manager": decode_address(self.manager_address),
                b"proposal_voting_app_id": PROPOSAL_VOTING_APP_ID,
            },
        )
    
    def test_send_transaction(self):
        user_sk, user_address = generate_account()
        sender_sk, sender_address = generate_account()
        _, receiver_address = generate_account()

        amount = 1000
        asset_id = 0

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(sender_address, 10_000_000)
        self.ledger.set_account_balance(receiver_address, 1_000_000)

        self.create_proposal_voting_app(
            self.manager_address, self.proposal_manager_address
        )
        self.ledger.global_states[PROPOSAL_VOTING_APP_ID][
            b"quorum_threshold"
        ] = 7_000_000
        self.ledger.set_account_balance(
            get_application_address(PROPOSAL_VOTING_APP_ID),
            proposal_voting.constants.PROPOSAL_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT,
        )

        # Rekey sender account to application account
        txn_group =  TransactionGroup([
            transaction.PaymentTxn(
                sender=sender_address,
                sp=self.sp,
                receiver=get_application_address(TREASURY_MANAGEMENT_EXECUTOR_APP_ID),
                amt=0,
                rekey_to=get_application_address(TREASURY_MANAGEMENT_EXECUTOR_APP_ID),
            )
        ])
        txn_group.sign_with_private_key(sender_address, sender_sk)
        self.ledger.eval_transactions(
            txn_group.signed_transactions,
            block_timestamp=1647561600,
        )

        # Create proposal
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        execution_hash = bytes("send", "utf-8") + decode_address(sender_address) + decode_address(receiver_address) + int_to_bytes(amount) + int_to_bytes(asset_id)
        execution_hash = sha256(execution_hash).digest()
        execution_hash = lpad(execution_hash, 128)

        proposal = Proposal(
            index=0,
            creation_timestamp=1647302400,
            voting_start_timestamp=1647561600,
            voting_end_timestamp=1648166400,
            snapshot_total_voting_power=7671231,
            vote_count=4,
            quorum_threshold=7000000,
            against_voting_power=205479,
            for_voting_power=7054794,
            abstain_voting_power=410958,
            is_approved=True,
            is_cancelled=False,
            is_executed=False,
            is_quorum_reached=True,
            proposer_address=user_address,
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal) + execution_hash
        }

        self.create_treasury_management_executor_app(self.manager_address)

        # Execute proposal
        sp = get_suggested_params()
        sp.fee = 10000
        send_transaction = transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=TREASURY_MANAGEMENT_EXECUTOR_APP_ID,
            app_args=["send", proposal_id, decode_address(sender_address), decode_address(receiver_address), amount, asset_id],
            foreign_apps=[PROPOSAL_VOTING_APP_ID],
            boxes=[(PROPOSAL_VOTING_APP_ID, proposal_box_name)],
            accounts=[sender_address, receiver_address]
        )
        txn_group = TransactionGroup([send_transaction])

        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(
            txn_group.signed_transactions,
            block_timestamp=proposal.voting_end_timestamp + 10,
        )

        self.assertEqual(self.ledger.accounts[receiver_address]["balances"][0][0], 1_001_000)
