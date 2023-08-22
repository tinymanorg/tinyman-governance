import unittest

from algojig import get_suggested_params, JigLedger
from algojig.gojig import run
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import TINY_ASSET_ID_KEY, VAULT_APP_ID_KEY, WEEK
from tinyman.governance.vault.constants import TOTAL_LOCKED_AMOUNT_KEY, TOTAL_POWER_COUNT_KEY, CREATION_TIMESTAMP_KEY, VAULT_APP_MINIMUM_BALANCE_REQUIREMENT, TOTAL_POWER_BOX_ARRAY_LEN, TOTAL_POWERS, TOTAL_POWER_BOX_SIZE, TOTAL_POWER_SIZE, LAST_TOTAL_POWER_TIMESTAMP_KEY
from tinyman.governance.vault.transactions import prepare_create_checkpoints_transactions
from tinyman.utils import int_to_bytes

from common.constants import staking_voting_approval_program, rewards_approval_program, vault_approval_program, proposal_voting_approval_program, TINY_ASSET_ID, VAULT_APP_ID, PROPOSAL_VOTING_APP_ID, REWARDS_APP_ID, STAKING_VOTING_APP_ID
from tinyman.governance.rewards.constants import REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_HISTORY_BOX_SIZE, REWARD_HISTORY_SIZE, REWARD_HISTORY_BOX_PREFIX, REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT, REWARD_PERIOD_COUNT_KEY, REWARD_HISTORY_COUNT_KEY, MANAGER_KEY, FIRST_PERIOD_TIMESTAMP
from vault.utils import get_vault_app_global_state


class BaseTestCase(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.sp = get_suggested_params()

        cls.tiny_asset_creator_sk, cls.tiny_asset_creator_address = generate_account()
        total = 10 ** 15
        cls.tiny_params = dict(
            total=total,
            decimals=6,
            name="Tinyman",
            unit_name="TINY",
            creator=cls.tiny_asset_creator_address
        )

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_asset(TINY_ASSET_ID, params=dict())

    def low_level_eval(self, signed_txns, block_timestamp):
        self.ledger.init_ledger_db(block_timestamp)
        self.ledger.write()
        self.ledger.write_transactions(signed_txns)
        output = run("eval")
        return output

    def init_app_boxes(self, app_id):
        if app_id not in self.ledger.boxes:
            self.ledger.boxes[app_id] = {}


class VaultAppMixin:

    def create_vault_app(self, app_creator_address, creation_timestamp):
        if app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(app_creator_address, 1_000_000)

        # TODO: Update int and byte counts
        self.ledger.create_app(
            app_id=VAULT_APP_ID,
            approval_program=vault_approval_program,
            creator=app_creator_address,
            local_ints=0,
            local_bytes=0,
            global_ints=16,
            global_bytes=0
        )

        self.ledger.set_global_state(
            VAULT_APP_ID,
            {
                TINY_ASSET_ID_KEY: TINY_ASSET_ID,
                TOTAL_LOCKED_AMOUNT_KEY: 0,
                TOTAL_POWER_COUNT_KEY: 0,
                CREATION_TIMESTAMP_KEY: creation_timestamp,
                LAST_TOTAL_POWER_TIMESTAMP_KEY: 0
            }
        )

    def init_vault_app(self, timestamp):
        # Min balance requirement
        self.ledger.set_account_balance(get_application_address(VAULT_APP_ID), VAULT_APP_MINIMUM_BALANCE_REQUIREMENT)
        # Opt-in
        self.ledger.set_account_balance(get_application_address(VAULT_APP_ID), 0, asset_id=TINY_ASSET_ID)
        self.set_box_total_power(index=0, bias=0, timestamp=timestamp, slope=0, cumulative_power=0)

        self.ledger.update_global_state(
            VAULT_APP_ID,
            {
                TOTAL_POWER_COUNT_KEY: 1,
                LAST_TOTAL_POWER_TIMESTAMP_KEY: timestamp
            }
        )

    def create_checkpoints(self, user_address, user_sk, block_timestamp):
        vault_app_global_state = get_vault_app_global_state(self.ledger)
        while vault_app_global_state.last_total_power_timestamp != block_timestamp:
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                txn_group = prepare_create_checkpoints_transactions(
                    vault_app_id=VAULT_APP_ID,
                    sender=user_address,
                    vault_app_global_state=vault_app_global_state,
                    suggested_params=self.sp,
                )
            txn_group.sign_with_private_key(user_address, user_sk)
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            vault_app_global_state = get_vault_app_global_state(self.ledger)

    def set_box_total_power(self, index, bias, timestamp, slope, cumulative_power):
        self.init_app_boxes(VAULT_APP_ID)

        box_index = index // TOTAL_POWER_BOX_ARRAY_LEN
        array_index = index % TOTAL_POWER_BOX_ARRAY_LEN

        box_name = TOTAL_POWERS + int_to_bytes(box_index)
        if box_name not in self.ledger.boxes[VAULT_APP_ID]:
            self.ledger.boxes[VAULT_APP_ID][box_name] = int_to_bytes(0, 1) * TOTAL_POWER_BOX_SIZE

        total_power = int_to_bytes(bias) + int_to_bytes(timestamp) + int_to_bytes(slope, 16) + int_to_bytes(cumulative_power, 16)
        start = array_index * TOTAL_POWER_SIZE
        end = start + TOTAL_POWER_SIZE
        data = bytearray(self.ledger.boxes[VAULT_APP_ID][box_name])
        data[start:end] = total_power
        self.ledger.boxes[VAULT_APP_ID][box_name] = bytes(data)


class RewardsAppMixin:
    def create_rewards_app(self, app_creator_address, creation_timestamp):
        if app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(app_creator_address, 1_000_000)

        # TODO: Update int and byte counts
        self.ledger.create_app(
            app_id=REWARDS_APP_ID,
            approval_program=rewards_approval_program,
            creator=app_creator_address,
            local_ints=0,
            local_bytes=0,
            global_ints=16,
            global_bytes=16
        )

        # 100_000 for basic min balance requirement
        # self.ledger.set_account_balance(get_application_address(app_id), 1_000_000)
        self.ledger.set_global_state(
            REWARDS_APP_ID,
            {
                TINY_ASSET_ID_KEY: TINY_ASSET_ID,
                VAULT_APP_ID_KEY: VAULT_APP_ID,
                REWARD_PERIOD_COUNT_KEY: 0,
                FIRST_PERIOD_TIMESTAMP: 0,
                MANAGER_KEY: decode_address(app_creator_address),
            }
        )

    def init_rewards_app(self, first_period_timestamp, reward_amount=100_000_000):
        assert first_period_timestamp % WEEK == 0
        # Min balance requirement
        self.ledger.set_account_balance(get_application_address(REWARDS_APP_ID), REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT)
        # Opt-in
        self.ledger.set_account_balance(get_application_address(REWARDS_APP_ID), 0, asset_id=TINY_ASSET_ID)
        self.set_box_reward_history(index=0, timestamp=first_period_timestamp, reward_amount=reward_amount)

        self.ledger.update_global_state(
            REWARDS_APP_ID,
            {
                FIRST_PERIOD_TIMESTAMP: first_period_timestamp,
                REWARD_HISTORY_COUNT_KEY: 1,
            }
        )

    def set_box_reward_history(self, index, timestamp, reward_amount):
        self.init_app_boxes(REWARDS_APP_ID)

        box_index = index // REWARD_HISTORY_BOX_ARRAY_LEN
        array_index = index % REWARD_HISTORY_BOX_ARRAY_LEN

        box_name = REWARD_HISTORY_BOX_PREFIX + int_to_bytes(box_index)
        if box_name not in self.ledger.boxes[REWARDS_APP_ID]:
            self.ledger.boxes[REWARDS_APP_ID][box_name] = int_to_bytes(0, 1) * REWARD_HISTORY_BOX_SIZE

        reward_history = int_to_bytes(timestamp) + int_to_bytes(reward_amount)
        start = array_index * REWARD_HISTORY_SIZE
        end = start + REWARD_HISTORY_SIZE
        data = bytearray(self.ledger.boxes[REWARDS_APP_ID][box_name])
        data[start:end] = reward_history
        self.ledger.boxes[REWARDS_APP_ID][box_name] = bytes(data)


class StakingVotingAppMixin:

    def create_staking_voting_app(self, app_creator_address):
        if app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(app_creator_address, 1_000_000)

        # TODO: Update int and byte counts
        self.ledger.create_app(
            app_id=STAKING_VOTING_APP_ID,
            approval_program=staking_voting_approval_program,
            creator=app_creator_address,
            local_ints=0,
            local_bytes=0,
            global_ints=16,
            global_bytes=16
        )

        # 100_000 for basic min balance requirement
        # self.ledger.set_account_balance(get_application_address(app_id), 1_000_000)
        self.ledger.set_global_state(
            STAKING_VOTING_APP_ID,
            {
                TINY_ASSET_ID_KEY: TINY_ASSET_ID,
                b'vault_app_id': VAULT_APP_ID,
                b'proposal_id_counter': 0,
                b'voting_delay': 1,
                b'voting_duration': 7,
                b'manager': decode_address(app_creator_address),
                b'proposal_manager': decode_address(app_creator_address)
            }
        )


class ProposalVotingAppMixin:

    def create_proposal_voting_app(self, app_creator_address):
        if app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(app_creator_address, 1_000_000)

        # TODO: Update int and byte counts
        self.ledger.create_app(
            app_id=PROPOSAL_VOTING_APP_ID,
            approval_program=proposal_voting_approval_program,
            creator=app_creator_address,
            local_ints=0,
            local_bytes=0,
            global_ints=16,
            global_bytes=16
        )

        # 100_000 for basic min balance requirement
        self.ledger.set_global_state(
            PROPOSAL_VOTING_APP_ID,
            {
                b'vault_app_id': VAULT_APP_ID,
                b'proposal_id_counter': 0,
                b'proposal_threshold': 10,
                b'voting_delay': 1,
                b'voting_duration': 7,
                b'quorum_numerator': 50,
                b'manager': decode_address(app_creator_address),
                b'proposal_manager': decode_address(app_creator_address)
            }
        )
