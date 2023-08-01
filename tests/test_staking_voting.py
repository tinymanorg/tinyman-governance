import unittest.mock
from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import WEEK, DAY
from tinyman.governance.vault.transactions import prepare_create_lock_transactions, prepare_increase_lock_amount_transactions
from tinyman.governance.vault.utils import get_slope, get_bias, get_start_timestamp_of_week
from tinyman.utils import int_to_bytes, bytes_to_int

from common.constants import TINY_ASSET_ID, STAKING_VOTING_APP_ID, VAULT_APP_ID
from common.utils import sign_txns, parse_box_staking_proposal, parse_box_account_power
from staking_voting.constants import PROPOSAL_BOX_PREFIX, VOTE_BOX_PREFIX
from staking_voting.transactions import prepare_create_proposal_transactions, prepare_cast_vote_transactions, prepare_cancel_proposal_transactions
from tests.common import BaseTestCase, VaultAppMixin, StakingVotingAppMixin
from vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at


class StakingVotingTestCase(VaultAppMixin, StakingVotingAppMixin, BaseTestCase):

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
        self.ledger.set_account_balance(user_address, 1_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.app_creator_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        # print_boxes(self.ledger.boxes[STAKING_VOTING_APP_ID])

    def test_cast_vote(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), 1_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock 1
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
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

        amount = 35_000_000
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

        # Create proposal
        proposal_id = int_to_bytes(1) * 4
        txn_group = prepare_create_proposal_transactions(self.app_creator_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        # print_boxes(self.ledger.boxes[STAKING_VOTING_APP_ID])
        proposal_creation_timestamp = block_timestamp

        # Cast Vote
        block_timestamp = proposal_creation_timestamp + DAY
        # votes = [10, 15, 20, 25, 30]
        # asset_ids = [1, 2, 3, 4, 50]
        # votes = [10, 10, 10, 10, 10, 10, 10, 10, 10, 5, 5]
        votes = [10] * 5 + [5] * 9 + [3, 2]
        asset_ids = list(range(1, len(votes) + 1))

        txn_group = prepare_cast_vote_transactions(self.ledger, user_address, proposal_id, votes, asset_ids, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        # output = self.low_level_eval(signed_txns, block_timestamp=block_timestamp)
        # print(output)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        # print(bytes_to_int(block[b'txns'][1][b'dt'][b'lg'][-1]))
        # print_boxes(self.ledger.boxes[STAKING_VOTING_APP_ID])

        # Check if all the votes are correct
        slope = get_slope(100_000_000)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        proposal_index = parse_box_staking_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])["index"]

        self.assertEqual(parse_box_staking_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])["vote_count"], 1)
        for i in range(0, len(votes)):
            asset_id = asset_ids[i]
            vote_as_percentage = votes[i]

            vote_box_name = VOTE_BOX_PREFIX + int_to_bytes(proposal_index) + int_to_bytes(asset_id)
            vote_box_amount = bytes_to_int(self.ledger.boxes[STAKING_VOTING_APP_ID][vote_box_name])

            self.assertEqual(vote_box_amount, int((bias // 100) * vote_as_percentage))

        votes = [20] * 5
        asset_ids = list(range(1, len(votes) + 1))
        txn_group = prepare_cast_vote_transactions(self.ledger, user_2_address, proposal_id, votes, asset_ids, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        # print(bytes_to_int(block[b'txns'][1][b'dt'][b'lg'][-1]))
        # print_boxes(self.ledger.boxes[STAKING_VOTING_APP_ID])

    def test_cast_vote_after_increase_lock_amount(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), 1_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock 1
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
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
        txn_group = prepare_create_proposal_transactions(self.app_creator_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        # print_boxes(self.ledger.boxes[STAKING_VOTING_APP_ID])
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
                current_lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_address),
                suggested_params=self.sp,
                app_call_note=None,
            )
        txn_group.sign_with_private_key(user_address, user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Cast Vote
        block_timestamp = proposal_creation_timestamp + DAY
        votes = [10, 15, 20, 25, 30]
        asset_ids = list(range(1, len(votes) + 1))

        txn_group = prepare_cast_vote_transactions(self.ledger, user_address, proposal_id, votes, asset_ids, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # Check if all the votes are correct
        account_powers = parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][decode_address(user_address) + int_to_bytes(0)])
        account_power = account_powers[-1]
        voting_power = account_power.bias - get_bias(account_power.slope, (block_timestamp - account_power.timestamp))

        proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
        proposal_index = parse_box_staking_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])["index"]

        self.assertEqual(parse_box_staking_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])["vote_count"], 1)
        for i in range(0, len(votes)):
            asset_id = asset_ids[i]
            vote_as_percentage = votes[i]

            vote_box_name = VOTE_BOX_PREFIX + int_to_bytes(proposal_index) + int_to_bytes(asset_id)
            vote_box_amount = bytes_to_int(self.ledger.boxes[STAKING_VOTING_APP_ID][vote_box_name])

            self.assertEqual(vote_box_amount, int((voting_power // 100) * vote_as_percentage))

    def test_cancel_proposal(self):
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), 1_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), 1_000_000)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        self.create_checkpoints(user_address, user_sk, block_timestamp)

        # Create lock 1
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
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
        txn_group = prepare_create_proposal_transactions(self.app_creator_address, proposal_id, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)
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
        self.assertEqual(parse_box_staking_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])["is_cancelled"], 1)

        # Try to cast Vote
        block_timestamp = proposal_creation_timestamp + DAY
        votes = [10, 15, 20, 25, 30]
        asset_ids = list(range(1, len(votes) + 1))

        txn_group = prepare_cast_vote_transactions(self.ledger, user_address, proposal_id, votes, asset_ids, proposal_creation_timestamp, self.sp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_cancelled == BYTES_ZERO)')
