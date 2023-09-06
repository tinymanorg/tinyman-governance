import unittest.mock
from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import WEEK, DAY
from tinyman.governance.event import decode_logs
from tinyman.governance.staking_voting.constants import STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT
from tinyman.governance.staking_voting.events import staking_voting_events
from tinyman.governance.staking_voting.storage import StakingVotingProposal, get_staking_proposal_box_name, parse_box_staking_voting_proposal, get_staking_vote_box_name
from tinyman.governance.staking_voting.transactions import prepare_create_staking_proposal_transactions, prepare_cancel_staking_proposal_transactions, prepare_set_manager_transactions, prepare_set_proposal_manager_transactions, prepare_cast_vote_transactions
from tinyman.governance.utils import hash_metadata
from tinyman.governance.vault.storage import parse_box_account_power
from tinyman.governance.vault.transactions import prepare_create_lock_transactions, prepare_increase_lock_amount_transactions
from tinyman.governance.vault.utils import get_slope, get_bias, get_start_timestamp_of_week
from tinyman.utils import int_to_bytes, bytes_to_int

from common.constants import TINY_ASSET_ID, STAKING_VOTING_APP_ID, VAULT_APP_ID
from common.utils import get_account_power_index_at
from staking_voting.utils import is_account_attendance_box_exists, get_new_asset_count
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
        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

    def test_create_proposal(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 1_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        
        metadata = {
            "name": "Proposal 1",
            "description": "proposal description",
            "start_timestamp": int(block_timestamp),
            "end_timestamp":int(block_timestamp),
        }
        proposal_id = hash_metadata(metadata)
        txn_group = prepare_create_staking_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.app_creator_address, private_key=self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][1][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 2)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'proposal',
                'proposal_id': [123, 112, 120, 239, 120, 9, 193, 165, 26, 121, 130, 223, 101, 75, 166, 98, 53, 148, 168, 94, 247, 80, 161, 37, 133, 111, 139, 4, 78, 1, 41, 162],
                'index': 0,
                'creation_timestamp': 1647302400,
                'voting_start_timestamp': 1647388800,
                'voting_end_timestamp': 1647993600,
                'voting_power': 0,
                'vote_count': 0,
                'is_cancelled': False
            }
        )
        self.assertDictEqual(
            events[1],
            {
                'event_name': 'create_proposal',
                'user_address': self.app_creator_address,
                'proposal_id': [123, 112, 120, 239, 120, 9, 193, 165, 26, 121, 130, 223, 101, 75, 166, 98, 53, 148, 168, 94, 247, 80, 161, 37, 133, 111, 139, 4, 78, 1, 41, 162]
            }
        )
        self.assertEqual(
            parse_box_staking_voting_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][get_staking_proposal_box_name(proposal_id)]),
            StakingVotingProposal(
                index= 0,
                creation_timestamp=1647302400,
                voting_start_timestamp=1647388800,
                voting_end_timestamp=1647993600,
                voting_power=0,
                vote_count=0,
                is_cancelled=False
            )
        )

        block_timestamp += 10 * DAY
        # Create another proposal
        metadata["name"] = "Proposal 2"
        proposal_id = hash_metadata(metadata)
        txn_group = prepare_create_staking_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.app_creator_address, private_key=self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][1][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 2)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'proposal',
                'proposal_id': [253, 218, 88, 117, 106, 182, 231, 142, 113, 18, 170, 213, 83, 211, 181, 75, 218, 68, 228, 28, 102, 78, 219, 66, 188, 37, 172, 44, 54, 139, 75, 39],
                'index': 1,
                'creation_timestamp': 1648166400,
                'voting_start_timestamp': 1648252800,
                'voting_end_timestamp': 1648857600,
                'voting_power': 0,
                'vote_count': 0,
                'is_cancelled': False
            }
        )
        self.assertDictEqual(
            events[1],
            {
                'event_name': 'create_proposal',
                'user_address': self.app_creator_address,
                'proposal_id': [253, 218, 88, 117, 106, 182, 231, 142, 113, 18, 170, 213, 83, 211, 181, 75, 218, 68, 228, 28, 102, 78, 219, 66, 188, 37, 172, 44, 54, 139, 75, 39]
            }
        )

        # Generating a proposal with the same hash/id is not allowed
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(!proposal_exists(proposal_id))')
        
        # proposal_id must be 32 bytes, test with 31 bytes
        proposal_id = proposal_id[:-1]
        txn_group = prepare_create_staking_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.app_creator_address, private_key=self.app_creator_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(len(proposal_id) == 32)')

    def test_cast_vote(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        txn_group = prepare_create_staking_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.app_creator_address, private_key=self.app_creator_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        proposal_creation_timestamp = block_timestamp

        # Cast Vote
        block_timestamp = proposal_creation_timestamp + DAY
        # votes = [10, 15, 20, 25, 30]
        # asset_ids = [1, 2, 3, 4, 50]
        # votes = [10, 10, 10, 10, 10, 10, 10, 10, 10, 5, 5]
        votes = [10] * 5 + [5] * 9 + [3, 2]
        asset_ids = list(range(1, len(votes) + 1))

        proposal_box_name = get_staking_proposal_box_name(proposal_id)
        proposal = parse_box_staking_voting_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        txn_group = prepare_cast_vote_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            votes=votes,
            asset_ids=asset_ids,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp),
            new_asset_count=get_new_asset_count(self.ledger, proposal.index, asset_ids),
            create_attendance_sheet=is_account_attendance_box_exists(self.ledger, user_address, proposal.index),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][1][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        for event in events:
            print(event)
        print()

        # Check if all the votes are correct
        slope = get_slope(100_000_000)
        bias = get_bias(slope, (lock_end_timestamp - proposal.creation_timestamp))

        proposal_box_name = get_staking_proposal_box_name(proposal_id)
        proposal = parse_box_staking_voting_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])

        self.assertEqual(parse_box_staking_voting_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name]).vote_count, 1)
        for i in range(0, len(votes)):
            asset_id = asset_ids[i]
            vote_as_percentage = votes[i]

            vote_box_name = get_staking_vote_box_name(proposal.index, asset_id)
            vote_box_amount = bytes_to_int(self.ledger.boxes[STAKING_VOTING_APP_ID][vote_box_name])

            self.assertEqual(vote_box_amount, int((bias // 100) * vote_as_percentage))

        votes = [20] * 5
        asset_ids = list(range(1, len(votes) + 1))
        txn_group = prepare_cast_vote_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_2_address,
            proposal_id=proposal_id,
            proposal=proposal,
            votes=votes,
            asset_ids=asset_ids,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_2_address, proposal_creation_timestamp),
            new_asset_count=get_new_asset_count(self.ledger, proposal.index, asset_ids),
            create_attendance_sheet=is_account_attendance_box_exists(self.ledger, user_2_address, proposal.index),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][1][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        for event in events:
            print(event)
        print()

    def test_cast_vote_after_increase_lock_amount(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        proposal_id = hash_metadata({})
        txn_group = prepare_create_staking_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.app_creator_address, private_key=self.app_creator_sk)
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
        block_timestamp = proposal_creation_timestamp + DAY
        votes = [10, 15, 20, 25, 30]
        asset_ids = list(range(1, len(votes) + 1))

        proposal_box_name = get_staking_proposal_box_name(proposal_id)
        proposal = parse_box_staking_voting_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        txn_group = prepare_cast_vote_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            votes=votes,
            asset_ids=asset_ids,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp),
            new_asset_count=get_new_asset_count(self.ledger, proposal.index, asset_ids),
            create_attendance_sheet=is_account_attendance_box_exists(self.ledger, user_address, proposal.index),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][1][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        for event in events:
            print(event)
        print()

        # Check if all the votes are correct
        account_powers = parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][decode_address(user_address) + int_to_bytes(0)])
        account_power = account_powers[-1]
        voting_power = account_power.bias - get_bias(account_power.slope, (proposal.creation_timestamp - account_power.timestamp))

        proposal_box_name = get_staking_proposal_box_name(proposal_id)
        proposal = parse_box_staking_voting_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])

        self.assertTrue(proposal.vote_count)
        for i in range(0, len(votes)):
            asset_id = asset_ids[i]
            vote_as_percentage = votes[i]

            vote_box_name = get_staking_vote_box_name(proposal.index, asset_id)
            vote_box_amount = bytes_to_int(self.ledger.boxes[STAKING_VOTING_APP_ID][vote_box_name])

            self.assertEqual(vote_box_amount, int((voting_power // 100) * vote_as_percentage))

    def test_cancel_proposal(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

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
        txn_group = prepare_create_staking_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        proposal_creation_timestamp = block_timestamp

        # Cancel proposal
        proposal_box_name = get_staking_proposal_box_name(proposal_id)
        proposal_manager_address = self.app_creator_address
        proposal_manager_sk = self.app_creator_sk
        block_timestamp += 1

        txn_group = prepare_cancel_staking_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(proposal_manager_address, proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        proposal = parse_box_staking_voting_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        self.assertTrue(proposal.is_cancelled)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        for event in events:
            print(event)
        print()

        # Try to cast Vote
        block_timestamp = proposal_creation_timestamp + DAY
        votes = [10, 15, 20, 25, 30]
        asset_ids = list(range(1, len(votes) + 1))

        proposal = parse_box_staking_voting_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        txn_group = prepare_cast_vote_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            votes=votes,
            asset_ids=asset_ids,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp),
            new_asset_count=get_new_asset_count(self.ledger, proposal.index, asset_ids),
            create_attendance_sheet=is_account_attendance_box_exists(self.ledger, user_address, proposal.index),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_cancelled == BYTES_FALSE)')


    def test_set_manager(self):
        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)
        
        # Test address validation
        txn_group = prepare_set_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=user_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(MANAGER_KEY))')
        
        # Set user as manager
        txn_group = prepare_set_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_manager', 'manager': user_address}
        )

        # Set back app creator as manager
        txn_group = prepare_set_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=user_address,
            new_manager_address=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_manager', 'manager': self.app_creator_address}
        )

    def test_set_proposal_manager(self):
        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        user_sk, user_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_staking_voting_app(self.app_creator_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Test address validation
        txn_group = prepare_set_proposal_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=user_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(MANAGER_KEY))')

        # Set user as manager
        txn_group = prepare_set_proposal_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_proposal_manager', 'manager': user_address}
        )

        # Set back app creator as manager
        txn_group = prepare_set_proposal_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_proposal_manager', 'manager': self.app_creator_address}
        )
