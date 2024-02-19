from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import get_suggested_params, LogicSigReject
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance import proposal_voting
from tinyman.governance.proposal_voting.executor_transactions import get_arbitrary_transaction_execution_hash, get_arbitrary_transaction_group_execution_hash, prepare_asset_optin_transactions, prepare_validate_transaction_transactions, prepare_validate_group_transactions
from tinyman.governance.proposal_voting.storage import (
    Proposal,
    get_proposal_box_name,
    parse_box_proposal,
)
from tinyman.governance.utils import generate_cid_from_proposal_metadata
from tinyman.utils import TransactionGroup

from tests.common import (
    BaseTestCase,
    ProposalVotingAppMixin,
    VaultAppMixin,
    ArbitraryExecutorAppMixin,
    get_rawbox_from_proposal,
)
from tests.constants import (
    PROPOSAL_VOTING_APP_ID,
    ARBITRARY_EXECUTOR_APP_ID,
    arbitrary_executor_approval_program,
    arbitrary_executor_clear_state_program,
    arbitrary_executor_logic_signature
)


class ArbitraryExecutorTestCase(
    VaultAppMixin, ProposalVotingAppMixin, ArbitraryExecutorAppMixin, BaseTestCase
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
                    approval_program=arbitrary_executor_approval_program.bytecode,
                    clear_program=arbitrary_executor_clear_state_program.bytecode,
                    global_schema=transaction.StateSchema(
                        num_uints=16, num_byte_slices=16
                    ),
                    local_schema=transaction.StateSchema(
                        num_uints=0, num_byte_slices=0
                    ),
                    extra_pages=3,
                    app_args=[b"create_application", PROPOSAL_VOTING_APP_ID],
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

    def test_execute_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)

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

        # Create the arbitrary executor transactions
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        arbitrary_transaction_sp = get_suggested_params()
        arbitrary_transaction_sp.fee = 15000
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

        arbitrary_executor_txn_group = prepare_validate_transaction_transactions(
            arbitrary_executor_app_id=ARBITRARY_EXECUTOR_APP_ID,
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            proposal_id=proposal_id,
            transaction_to_validate=arbitrary_transaction,
            sender=user_address,
            suggested_params=self.sp
        )

        execution_hash = get_arbitrary_transaction_execution_hash(arbitrary_transaction)

        # Create proposal
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
            executor_address=get_application_address(ARBITRARY_EXECUTOR_APP_ID)
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal)
        }

        # Execute proposal
        self.create_arbitrary_executor_app(self.manager_address)

        arbitrary_executor_txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(
            arbitrary_executor_txn_group.signed_transactions,
            block_timestamp=proposal.voting_end_timestamp + 10,
        )

        # Logs
        self.assertIsNotNone(self.ledger.assets[block[b"txns"][1][b"caid"]])

    def test_logic_sig(self):
        user_sk, user_address = generate_account()
        tinyman_algo_sk, tinyman_algo_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(tinyman_algo_address, 10_000_000)

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

        global_states = self.ledger.global_states.copy()

        # Create logic sig account
        logic_sig_account = transaction.LogicSigAccount(arbitrary_executor_logic_signature.bytecode)

        # Rekey tinyman_algo account to logic sig account
        self.ledger.set_account_balance(logic_sig_account.address(), 10_000_000)

        txn_group = TransactionGroup([
            transaction.PaymentTxn(
                sender=tinyman_algo_address,
                sp=self.sp,
                receiver=logic_sig_account.address(),
                amt=1000, 
                rekey_to=logic_sig_account.address()
            )
        ])
        txn_group.sign_with_private_key(tinyman_algo_address, tinyman_algo_sk)
        self.ledger.eval_transactions(
            txn_group.signed_transactions, block_timestamp=1647561600
        )

        self.ledger.global_states = global_states

        # Create the arbitrary executor transactions
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        arbitrary_transaction_sp = get_suggested_params()
        arbitrary_transaction_sp.fee = 15000
        arbitrary_transaction = transaction.AssetConfigTxn(
            sender=tinyman_algo_address,
            sp=arbitrary_transaction_sp,
            total=1000,
            default_frozen=False,
            unit_name="TINYRING",
            asset_name="Tiny Ring",
            manager=tinyman_algo_address,
            reserve=tinyman_algo_address,
            freeze=tinyman_algo_address,
            clawback=tinyman_algo_address,
            url="https://ipfs.io/ipfs/RANDOM_CID",
            decimals=0,
        )

        arbitrary_executor_txn_group = prepare_validate_transaction_transactions(
            arbitrary_executor_app_id=ARBITRARY_EXECUTOR_APP_ID,
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            proposal_id=proposal_id,
            transaction_to_validate=arbitrary_transaction,
            sender=user_address,
            suggested_params=self.sp
        )

        execution_hash = get_arbitrary_transaction_execution_hash(arbitrary_transaction)

        # Create proposal
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
            executor_address=get_application_address(ARBITRARY_EXECUTOR_APP_ID)
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal)
        }

        # Execute proposal
        self.create_arbitrary_executor_app(self.manager_address)

        arbitrary_executor_txn_group.sign_with_private_key(user_address, user_sk)
        arbitrary_executor_txn_group.sign_with_logicsig(logic_sig_account, address=tinyman_algo_address)

        block = self.ledger.eval_transactions(
            arbitrary_executor_txn_group.signed_transactions,
            block_timestamp=proposal.voting_end_timestamp + 10,
        )

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertTrue(proposal.is_executed)
    
    def test_logic_sig_with_rekey(self):
        user_sk, user_address = generate_account()
        tinyman_algo_sk, tinyman_algo_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(tinyman_algo_address, 10_000_000)

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

        global_states = self.ledger.global_states.copy()

        # Create logic sig account
        logic_sig_account = transaction.LogicSigAccount(arbitrary_executor_logic_signature.bytecode)

        # Rekey tinyman_algo account to logic sig account
        self.ledger.set_account_balance(logic_sig_account.address(), 10_000_000)

        txn_group = TransactionGroup([
            transaction.PaymentTxn(
                sender=tinyman_algo_address,
                sp=self.sp,
                receiver=logic_sig_account.address(),
                amt=1000, 
                rekey_to=logic_sig_account.address()
            )
        ])
        txn_group.sign_with_private_key(tinyman_algo_address, tinyman_algo_sk)
        self.ledger.eval_transactions(
            txn_group.signed_transactions, block_timestamp=1647561600
        )

        self.ledger.global_states = global_states

        # Create the arbitrary executor transactions
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        # Transaction with rekey
        arbitrary_transaction_sp = get_suggested_params()
        arbitrary_transaction_sp.fee = 15000
        arbitrary_transaction = transaction.PaymentTxn(
            sender=tinyman_algo_address,
            sp=arbitrary_transaction_sp,
            receiver=user_address,
            amt=0,
            rekey_to=user_address
        )

        arbitrary_executor_txn_group = prepare_validate_transaction_transactions(
            arbitrary_executor_app_id=ARBITRARY_EXECUTOR_APP_ID,
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            proposal_id=proposal_id,
            transaction_to_validate=arbitrary_transaction,
            sender=user_address,
            suggested_params=self.sp
        )

        execution_hash = get_arbitrary_transaction_execution_hash(arbitrary_transaction)

        # Create proposal
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
            executor_address=get_application_address(ARBITRARY_EXECUTOR_APP_ID)
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal)
        }

        # Execute proposal
        self.create_arbitrary_executor_app(self.manager_address)

        arbitrary_executor_txn_group.sign_with_private_key(user_address, user_sk)
        arbitrary_executor_txn_group.sign_with_logicsig(logic_sig_account, address=tinyman_algo_address)

        with self.assertRaises(LogicSigReject) as e:
            self.ledger.eval_transactions(
                arbitrary_executor_txn_group.signed_transactions,
                block_timestamp=proposal.voting_end_timestamp + 10,
            )
        self.assertEqual(e.exception.error, 'assert failed')
    
    def test_validate_group(self):
        user_sk, user_address = generate_account()
        tinyman_algo_sk, tinyman_algo_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(tinyman_algo_address, 10_000_000)

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

        global_states = self.ledger.global_states.copy()

        # Create logic sig account
        logic_sig_account = transaction.LogicSigAccount(arbitrary_executor_logic_signature.bytecode)

        # Rekey tinyman_algo account to logic sig account
        self.ledger.set_account_balance(logic_sig_account.address(), 10_000_000)

        txn_group = TransactionGroup([
            transaction.PaymentTxn(
                sender=tinyman_algo_address,
                sp=self.sp,
                receiver=logic_sig_account.address(),
                amt=1000, 
                rekey_to=logic_sig_account.address()
            )
        ])
        txn_group.sign_with_private_key(tinyman_algo_address, tinyman_algo_sk)
        self.ledger.eval_transactions(
            txn_group.signed_transactions, block_timestamp=1647561600
        )

        self.ledger.global_states = global_states

        # Create the arbitrary executor transactions
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        proposal_box_name = get_proposal_box_name(proposal_id)

        arbitrary_transaction_sp = get_suggested_params()
        arbitrary_transaction_sp.fee = 15000
        arbitrary_transactions = TransactionGroup([
            transaction.AssetConfigTxn(
                sender=tinyman_algo_address,
                sp=arbitrary_transaction_sp,
                total=1000,
                default_frozen=False,
                unit_name="TINYRING",
                asset_name="Tiny Ring",
                manager=tinyman_algo_address,
                reserve=tinyman_algo_address,
                freeze=tinyman_algo_address,
                clawback=tinyman_algo_address,
                url="https://ipfs.io/ipfs/RANDOM_CID",
                decimals=0,
            ),
            # An arbitrary transaction.
            transaction.PaymentTxn(
                sender=user_address,
                sp=arbitrary_transaction_sp,
                receiver=tinyman_algo_address,
                amt=1000,
            )
        ])
        

        arbitrary_executor_txn_group = prepare_validate_group_transactions(
            arbitrary_executor_app_id=ARBITRARY_EXECUTOR_APP_ID,
            proposal_voting_app_id=PROPOSAL_VOTING_APP_ID,
            proposal_id=proposal_id,
            group_to_validate=arbitrary_transactions,
            sender=user_address,
            suggested_params=self.sp
        )

        execution_hash = get_arbitrary_transaction_group_execution_hash(arbitrary_transactions)

        # Create proposal
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
            executor_address=get_application_address(ARBITRARY_EXECUTOR_APP_ID)
        )

        self.ledger.boxes[PROPOSAL_VOTING_APP_ID] = {
            proposal_box_name: get_rawbox_from_proposal(proposal)
        }

        # Execute proposal
        self.create_arbitrary_executor_app(self.manager_address)

        arbitrary_executor_txn_group.sign_with_private_key(user_address, user_sk)
        arbitrary_executor_txn_group.sign_with_logicsig(logic_sig_account, address=tinyman_algo_address)

        block = self.ledger.eval_transactions(
            arbitrary_executor_txn_group.signed_transactions,
            block_timestamp=proposal.voting_end_timestamp + 10,
        )

        proposal = parse_box_proposal(self.ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])
        self.assertTrue(proposal.is_executed)

    def test_asset_optin(self):
        block_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        self.create_arbitrary_executor_app(self.manager_address)

        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(ARBITRARY_EXECUTOR_APP_ID), 100000)

        self.ledger.create_asset(5, params=dict())
        txn_group = prepare_asset_optin_transactions(
            sender=user_address,
            suggested_params=self.sp,
            app_id=ARBITRARY_EXECUTOR_APP_ID,
            asset_id=5,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(
            txn_group.signed_transactions, block_timestamp=block_timestamp
        )

        self.ledger.create_asset(6, params=dict())
        txn_group = prepare_asset_optin_transactions(
            sender=user_address,
            suggested_params=self.sp,
            app_id=ARBITRARY_EXECUTOR_APP_ID,
            asset_id=6,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(
            txn_group.signed_transactions, block_timestamp=block_timestamp
        )
