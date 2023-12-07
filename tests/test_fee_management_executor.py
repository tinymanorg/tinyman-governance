from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from algojig import get_suggested_params
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance import proposal_voting
from tinyman.governance.proposal_voting.executor_transactions import get_set_fee_setter_transactions_execution_hash, prepare_set_fee_setter_transactions, \
    get_set_fee_manager_transactions_execution_hash, prepare_set_fee_manager_transactions, get_set_fee_collector_transactions_execution_hash, prepare_set_fee_collector_transactions, \
    get_set_fee_for_pool_transactions_execution_hash, prepare_set_fee_for_pool_transactions
from tinyman.governance.proposal_voting.storage import (
    Proposal,
    get_proposal_box_name,
    parse_box_proposal,
)
from tinyman.governance.utils import (
    generate_cid_from_proposal_metadata,
)
from tinyman.utils import TransactionGroup, int_to_bytes

from tests.common import (
    BaseTestCase,
    ProposalVotingAppMixin,
    VaultAppMixin,
    FeeManagementExecutorMixin,
    get_rawbox_from_proposal,
    lpad,
)
from tests.constants import (
    AMM_V2_APP_ID,
    PROPOSAL_VOTING_APP_ID,
    FEE_MANAGEMENT_EXECUTOR_APP_ID,
    amm_pool_template,
    fee_management_executor_approval_program,
    fee_management_executor_clear_state_program,
)


class FeeManagementExecutorTestCase(
    VaultAppMixin, ProposalVotingAppMixin, FeeManagementExecutorMixin, BaseTestCase
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
        self.create_amm_app(self.manager_address)
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
                    approval_program=fee_management_executor_approval_program.bytecode,
                    clear_program=fee_management_executor_clear_state_program.bytecode,
                    global_schema=transaction.StateSchema(
                        num_uints=16, num_byte_slices=16
                    ),
                    local_schema=transaction.StateSchema(
                        num_uints=0, num_byte_slices=0
                    ),
                    extra_pages=3,
                    app_args=[AMM_V2_APP_ID, PROPOSAL_VOTING_APP_ID],
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
                b"amm_app_id": AMM_V2_APP_ID,
                b"proposal_voting_app_id": PROPOSAL_VOTING_APP_ID,
            },
        )

    def test_set_fee_setter(self):
        user_sk, user_address = generate_account()
        _, new_fee_setter_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(new_fee_setter_address, 10_000_000)

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

        # Create proposal
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        execution_hash = get_set_fee_setter_transactions_execution_hash(new_fee_setter_address)

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
            execution_hash=execution_hash,
            executor_address=get_application_address(FEE_MANAGEMENT_EXECUTOR_APP_ID)
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal)
        }

        self.create_fee_management_executor_app(self.manager_address)

        # Execute proposal
        self.ledger.global_states[AMM_V2_APP_ID][b"fee_manager"] = decode_address(get_application_address(FEE_MANAGEMENT_EXECUTOR_APP_ID))

        sp = get_suggested_params()
        sp.fee = 10000
        txn_group = prepare_set_fee_setter_transactions(
            fee_management_executor_app_id=FEE_MANAGEMENT_EXECUTOR_APP_ID,
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            amm_app_id=AMM_V2_APP_ID,
            proposal_id=proposal_id,
            new_fee_setter=new_fee_setter_address,
            sender=user_address,
            suggested_params=sp,
        )

        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(
            txn_group.signed_transactions,
            block_timestamp=proposal.voting_end_timestamp + 10,
        )
    
        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertTrue(proposal.is_executed)
    
    def test_set_fee_manager_execution(self):
        user_sk, user_address = generate_account()
        _, new_fee_manager_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(new_fee_manager_address, 10_000_000)

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

        # Create proposal
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        execution_hash = get_set_fee_manager_transactions_execution_hash(new_fee_manager_address)

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
            execution_hash=execution_hash,
            executor_address=get_application_address(FEE_MANAGEMENT_EXECUTOR_APP_ID)
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal)
        }

        self.create_fee_management_executor_app(self.manager_address)

        # Execute proposal
        self.ledger.global_states[AMM_V2_APP_ID][b"fee_manager"] = decode_address(get_application_address(FEE_MANAGEMENT_EXECUTOR_APP_ID))

        sp = get_suggested_params()
        sp.fee = 10000
        txn_group = prepare_set_fee_manager_transactions(
            fee_management_executor_app_id=FEE_MANAGEMENT_EXECUTOR_APP_ID,
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            amm_app_id=AMM_V2_APP_ID,
            proposal_id=proposal_id,
            new_fee_manager=new_fee_manager_address,
            sender=user_address,
            suggested_params=sp,
        )

        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(
            txn_group.signed_transactions,
            block_timestamp=proposal.voting_end_timestamp + 10,
        )
    
    def test_set_fee_collector_execution(self):
        user_sk, user_address = generate_account()
        _, new_fee_collector_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(new_fee_collector_address, 10_000_000)

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

        # Create proposal
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        execution_hash = get_set_fee_collector_transactions_execution_hash(new_fee_collector_address)

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
            execution_hash=execution_hash,
            executor_address=get_application_address(FEE_MANAGEMENT_EXECUTOR_APP_ID)
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal)
        }

        self.create_fee_management_executor_app(self.manager_address)

        # Execute proposal
        self.ledger.global_states[AMM_V2_APP_ID][b"fee_manager"] = decode_address(get_application_address(FEE_MANAGEMENT_EXECUTOR_APP_ID))

        sp = get_suggested_params()
        sp.fee = 10000

        txn_group = prepare_set_fee_collector_transactions(
            fee_management_executor_app_id=FEE_MANAGEMENT_EXECUTOR_APP_ID,
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            amm_app_id=AMM_V2_APP_ID,
            proposal_id=proposal_id,
            new_fee_collector=new_fee_collector_address,
            sender=user_address,
            suggested_params=sp,
        )

        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(
            txn_group.signed_transactions,
            block_timestamp=proposal.voting_end_timestamp + 10,
        )

    # Taken from AMM V2 tests.
    def get_pool_logicsig_bytecode(self, pool_template, app_id, asset_1_id, asset_2_id):
        # These are the bytes of the logicsig template. This needs to be updated if the logicsig is updated.
        program = bytearray(pool_template.bytecode)

        template = b'\x06\x80\x18\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C'
        assert program == bytearray(template)

        program[3:11] = app_id.to_bytes(8, 'big')
        program[11:19] = asset_1_id.to_bytes(8, 'big')
        program[19:27] = asset_2_id.to_bytes(8, 'big')
        return transaction.LogicSigAccount(program)

    def test_set_fee_for_pool_execution(self):
        user_sk, user_address = generate_account()

        total_fee_share = 50
        protocol_fee_ratio = 4

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(AMM_V2_APP_ID), 10_000_000)

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

        # Opt-in pool
        self.asset_1_id = self.ledger.create_asset(asset_id=5, params=dict(unit_name="BTC"))
        self.asset_2_id = self.ledger.create_asset(asset_id=2, params=dict(unit_name="USD"))

        lsig = self.get_pool_logicsig_bytecode(amm_pool_template, AMM_V2_APP_ID, self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()

        self.ledger.set_account_balance(pool_address, 1_000_000)
        self.ledger.accounts[pool_address]["local_states"][AMM_V2_APP_ID] = {
            b"total_fee_share": 30,
            b"protocol_fee_ratio": 3
        }

        # Create proposal
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        execution_hash = get_set_fee_for_pool_transactions_execution_hash(pool_address, total_fee_share, protocol_fee_ratio)

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
            execution_hash=execution_hash,
            executor_address=get_application_address(FEE_MANAGEMENT_EXECUTOR_APP_ID)
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal)
        }

        self.create_fee_management_executor_app(self.manager_address)
        self.ledger.set_account_balance(pool_address, 100_000)

        self.ledger.global_states[AMM_V2_APP_ID][b"fee_setter"] = decode_address(get_application_address(FEE_MANAGEMENT_EXECUTOR_APP_ID))

        sp = get_suggested_params()
        sp.fee = 10000

        txn_group = prepare_set_fee_for_pool_transactions(
            fee_management_executor_app_id=FEE_MANAGEMENT_EXECUTOR_APP_ID,
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            amm_app_id=AMM_V2_APP_ID,
            proposal_id=proposal_id,
            pool_address=pool_address,
            pool_total_fee_share=total_fee_share,
            pool_protocol_fee_ratio=protocol_fee_ratio,
            sender=user_address,
            suggested_params=sp,
        )

        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(
            txn_group.signed_transactions,
            block_timestamp=proposal.voting_end_timestamp + 10,
        )

        self.assertEqual(self.ledger.accounts[pool_address]["local_states"][AMM_V2_APP_ID][b"total_fee_share"], 50)
        self.assertEqual(self.ledger.accounts[pool_address]["local_states"][AMM_V2_APP_ID][b"protocol_fee_ratio"], 4)
