import unittest.mock
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import DAY, WEEK
from tinyman.governance.event import decode_logs
from tinyman.governance.staking_voting.constants import \
    STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT
from tinyman.governance.staking_voting.events import staking_voting_events
from tinyman.governance.staking_voting.storage import (
    StakingDistributionProposal, get_staking_distribution_proposal_box_name,
    get_staking_vote_box_name, parse_box_staking_distribution_proposal)
from tinyman.governance.staking_voting.transactions import (
    generate_staking_distribution_proposal_metadata,
    prepare_cancel_staking_distribution_proposal_transactions,
    prepare_cast_vote_for_staking_distribution_proposal_transactions,
    prepare_create_staking_distribution_proposal_transactions,
    prepare_get_box_transaction, prepare_set_manager_transactions,
    prepare_set_proposal_manager_transactions,
    prepare_set_voting_delay_transactions,
    prepare_set_voting_duration_transactions)
from tinyman.governance.utils import generate_cid_from_proposal_metadata
from tinyman.governance.vault.storage import parse_box_account_power
from tinyman.governance.vault.transactions import (
    prepare_create_lock_transactions,
    prepare_increase_lock_amount_transactions)
from tinyman.governance.vault.utils import (get_bias, get_slope,
                                            get_start_timestamp_of_week)
from tinyman.utils import bytes_to_int, int_to_bytes

from tests.common import BaseTestCase, StakingVotingAppMixin, VaultAppMixin
from tests.constants import STAKING_VOTING_APP_ID, TINY_ASSET_ID, VAULT_APP_ID
from tests.staking_voting.utils import get_staking_voting_app_global_state
from tests.utils import (get_account_power_index_at, get_app_box_names,
                         get_first_app_call_txn)
from tests.vault.utils import (get_account_state, get_slope_change_at,
                               get_vault_app_global_state)


class StakingVotingTestCase(VaultAppMixin, StakingVotingAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.manager_sk, cls.manager_address = generate_account()
        cls.proposal_manager_sk, cls.proposal_manager_address = generate_account()
        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, hour=15, minute=7, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.manager_address, 1_000_000)
        self.ledger.set_account_balance(self.proposal_manager_address, 1_000_000)
        self.create_vault_app(self.manager_address)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

    def test_create_proposal(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 1_000_000)

        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Global state
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_index_counter, 0)
        
        # Create proposal
        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        metadata = {
            "name": "Proposal 1",
            "description": "proposal description",
            "start_timestamp": int(block_timestamp),
            "end_timestamp":int(block_timestamp),
        }
        proposal_id = generate_cid_from_proposal_metadata(metadata)
        txn_group = prepare_create_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.proposal_manager_address, private_key=self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        
        # Logs
        proposal_box_data = {
            'index': 0,
            'creation_timestamp': 1647356820,
            'voting_start_timestamp': 1647475200,
            'voting_end_timestamp': 1648080000,
            'voting_power': 0,
            'vote_count': 0,
            'is_cancelled': False
        }
        self.assertEqual(len(events), 2)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'proposal',
                'proposal_id': list(proposal_id.encode()),
                **proposal_box_data
            }
        )
        self.assertDictEqual(
            events[1],
            {
                'event_name': 'create_proposal',
                'user_address': self.proposal_manager_address,
                'proposal_id': list(proposal_id.encode())
            }
        )
        
        # Box
        self.assertEqual(
            parse_box_staking_distribution_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][get_staking_distribution_proposal_box_name(proposal_id)]),
            StakingDistributionProposal(**proposal_box_data)
        )
        
        # Global state
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_index_counter, 1)

        block_timestamp += 10 * DAY
        
        # Create another proposal
        metadata["name"] = "Proposal 2"
        proposal_id = generate_cid_from_proposal_metadata(metadata)
        txn_group = prepare_create_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.proposal_manager_address, private_key=self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        proposal_box_data = {
            'index': 1,
            'creation_timestamp': 1648220820,
            'voting_start_timestamp': 1648339200,
            'voting_end_timestamp': 1648944000,
            'voting_power': 0,
            'vote_count': 0,
            'is_cancelled': False
        }
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 2)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'proposal',
                'proposal_id': list(proposal_id.encode()),
                **proposal_box_data
            }
        )
        self.assertDictEqual(
            events[1],
            {
                'event_name': 'create_proposal',
                'user_address': self.proposal_manager_address,
                'proposal_id': list(proposal_id.encode())
            }
        )

        # Generating a proposal with the same hash/id is not allowed
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'box<Proposal> proposal = CreateBox(proposal_box_name)')
        
        # proposal_id must be 32 bytes, test with 31 bytes
        proposal_id = proposal_id[:-1]
        txn_group = prepare_create_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.proposal_manager_address, private_key=self.proposal_manager_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(len(proposal_id) == 59)')

    def test_cast_vote(self):
        user_sk, user_address = generate_account()
        user_2_sk, user_2_address = generate_account()

        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.set_account_balance(user_2_address, 10_000_000)

        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
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
        user_voting_power = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

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
        user_2_voting_power = get_bias(get_slope(amount), (lock_end_timestamp - block_timestamp))

        # Create proposal
        proposal_metadata = generate_staking_distribution_proposal_metadata(
            title="Proposal 1",
            description="",
            staking_program_start_time=block_timestamp,
            staking_program_end_time=block_timestamp + 4 * WEEK,
            staking_program_cycle_duration=1,
            staking_program_reward_asset=1,
        )
        proposal_id = generate_cid_from_proposal_metadata(proposal_metadata)
        txn_group = prepare_create_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.proposal_manager_address, private_key=self.proposal_manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        # Cast Vote
        block_timestamp += DAY * 3
        proposal_box_name = get_staking_distribution_proposal_box_name(proposal_id)
        proposal = parse_box_staking_distribution_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        block_timestamp = proposal.voting_start_timestamp
        # votes = [10, 15, 20, 25, 30]
        # asset_ids = [1, 2, 3, 4, 50]
        # votes = [10, 10, 10, 10, 10, 10, 10, 10, 10, 5, 5]
        votes = [10] * 5 + [5] * 9 + [3, 2]
        asset_ids = list(range(1, len(votes) + 1))
        txn_group = prepare_cast_vote_for_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            votes=votes,
            asset_ids=asset_ids,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal.creation_timestamp),
            app_box_names=get_app_box_names(self.ledger, STAKING_VOTING_APP_ID),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), len(votes) + 2)
        vote_events = events[:-2]
        proposal_event = events[-2]
        cast_vote_event = events[-1]
        asset_voting_powers = defaultdict(int)
        for i, vote_as_percentage in enumerate(votes):
            asset_voting_power = int(user_voting_power // 100) * vote_as_percentage
            
            self.assertEqual(
                vote_events[i],
                {'event_name': 'vote', 'asset_id': asset_ids[i], 'voting_power': asset_voting_power, 'vote_percentage': vote_as_percentage}
            )
            asset_voting_powers[asset_ids[i]] += asset_voting_power
            
            # Box
            vote_box_name = get_staking_vote_box_name(proposal.index, asset_ids[i])
            vote_box_amount = bytes_to_int(self.ledger.boxes[STAKING_VOTING_APP_ID][vote_box_name])
            self.assertEqual(vote_box_amount, asset_voting_power)
        
        self.assertEqual(
            proposal_event,
            {
                'event_name': 'proposal',
                'proposal_id': list(proposal_id.encode()),
                'index': 0,
                'creation_timestamp': 1647356820,
                'voting_start_timestamp': 1647475200,
                'voting_end_timestamp': 1648080000,
                'voting_power': user_voting_power,
                'vote_count': 1,
                'is_cancelled': False
            }
        )
        self.assertEqual(
            cast_vote_event,
            {
                'event_name': 'cast_vote',
                'user_address': user_address,
                'proposal_id': list(proposal_id.encode()),
                'voting_power': user_voting_power
            }
        )
        
        # Check if all the votes are correct
        proposal = parse_box_staking_distribution_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        self.assertEqual(proposal.vote_count, 1)

        # Another vote
        block_timestamp += DAY * 3
        votes = [20] * 5
        asset_ids = list(range(1, len(votes) + 1))
        txn_group = prepare_cast_vote_for_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_2_address,
            proposal_id=proposal_id,
            proposal=proposal,
            votes=votes,
            asset_ids=asset_ids,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_2_address, proposal.creation_timestamp),
            app_box_names=get_app_box_names(self.ledger, STAKING_VOTING_APP_ID),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), len(votes) + 2)
        vote_events = events[:-2]
        proposal_event = events[-2]
        cast_vote_event = events[-1]
        for i, vote_as_percentage in enumerate(votes):
            asset_voting_power = int(user_2_voting_power // 100) * vote_as_percentage

            self.assertEqual(
                vote_events[i],
                {'event_name': 'vote', 'asset_id': asset_ids[i], 'voting_power': asset_voting_power, 'vote_percentage': vote_as_percentage}
            )

            # Box
            vote_box_name = get_staking_vote_box_name(proposal.index, asset_ids[i])
            vote_box_amount = bytes_to_int(self.ledger.boxes[STAKING_VOTING_APP_ID][vote_box_name])
            self.assertEqual(vote_box_amount, asset_voting_power + asset_voting_powers[asset_ids[i]])

        self.assertEqual(
            proposal_event,
            {
                'event_name': 'proposal',
                'proposal_id': list(proposal_id.encode()),
                'index': 0,
                'creation_timestamp': 1647356820,
                'voting_start_timestamp': 1647475200,
                'voting_end_timestamp': 1648080000,
                'voting_power': user_voting_power + user_2_voting_power,
                'vote_count': 2,
                'is_cancelled': False
            }
        )
        self.assertEqual(
            cast_vote_event,
            {
                'event_name': 'cast_vote',
                'user_address': user_2_address,
                'proposal_id': list(proposal_id.encode()),
                'voting_power': user_2_voting_power
            }
        )

    def test_cast_vote_after_increase_lock_amount(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
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
        proposal_id = generate_cid_from_proposal_metadata({})
        txn_group = prepare_create_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.proposal_manager_address, private_key=self.proposal_manager_sk)
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
        proposal_box_name = get_staking_distribution_proposal_box_name(proposal_id)
        proposal = parse_box_staking_distribution_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        block_timestamp = proposal.voting_start_timestamp
        votes = [10, 15, 20, 25, 30]
        asset_ids = list(range(1, len(votes) + 1))
        txn_group = prepare_cast_vote_for_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            votes=votes,
            asset_ids=asset_ids,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal.creation_timestamp),
            app_box_names=get_app_box_names(self.ledger, STAKING_VOTING_APP_ID),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), len(votes) + 2)
        
        # Check if all the votes are correct
        account_powers = parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][decode_address(user_address) + int_to_bytes(0)])
        account_power = account_powers[-1]
        user_voting_power = account_power.bias - get_bias(account_power.slope, (proposal.creation_timestamp - account_power.timestamp))

        proposal_box_name = get_staking_distribution_proposal_box_name(proposal_id)
        proposal = parse_box_staking_distribution_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        
        vote_events = events[:-2]
        cast_vote_event = events[-1]
        for i, vote_as_percentage in enumerate(votes):
            asset_voting_power = int(user_voting_power // 100) * vote_as_percentage

            self.assertEqual(
                vote_events[i],
                {'event_name': 'vote', 'asset_id': asset_ids[i], 'voting_power': asset_voting_power, 'vote_percentage': vote_as_percentage}
            )

            # Box
            vote_box_name = get_staking_vote_box_name(proposal.index, asset_ids[i])
            vote_box_amount = bytes_to_int(self.ledger.boxes[STAKING_VOTING_APP_ID][vote_box_name])
            self.assertEqual(vote_box_amount, asset_voting_power)

        self.assertEqual(
            cast_vote_event,
            {
                'event_name': 'cast_vote',
                'user_address': user_address,
                'proposal_id': list(proposal_id.encode()),
                'voting_power': user_voting_power
            }
        )

        # self.assertTrue(proposal.vote_count)
        # for i in range(0, len(votes)):
        #     asset_id = asset_ids[i]
        #     vote_as_percentage = votes[i]
        # 
        #     vote_box_name = get_staking_vote_box_name(proposal.index, asset_id)
        #     vote_box_amount = bytes_to_int(self.ledger.boxes[STAKING_VOTING_APP_ID][vote_box_name])
        # 
        #     self.assertEqual(vote_box_amount, int((voting_power // 100) * vote_as_percentage))

    def test_cancel_proposal(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
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
        proposal_id = generate_cid_from_proposal_metadata({"name": "Proposal 1"})
        txn_group = prepare_create_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Cancel proposal
        proposal_box_name = get_staking_distribution_proposal_box_name(proposal_id)
        block_timestamp += 1

        txn_group = prepare_cancel_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        proposal = parse_box_staking_distribution_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        self.assertTrue(proposal.is_cancelled)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
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

        # Try to cast Vote
        block_timestamp = proposal.voting_start_timestamp + 1
        votes = [10, 15, 20, 25, 30]
        asset_ids = list(range(1, len(votes) + 1))

        proposal = parse_box_staking_distribution_proposal(self.ledger.boxes[STAKING_VOTING_APP_ID][proposal_box_name])
        txn_group = prepare_cast_vote_for_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            vault_app_id=VAULT_APP_ID,
            sender=user_address,
            proposal_id=proposal_id,
            proposal=proposal,
            votes=votes,
            asset_ids=asset_ids,
            account_power_index=get_account_power_index_at(self.ledger, VAULT_APP_ID, user_address, proposal.creation_timestamp),
            app_box_names=get_app_box_names(self.ledger, STAKING_VOTING_APP_ID),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(proposal.is_cancelled == BYTES_FALSE)')

    def test_set_manager(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
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
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(MANAGER_KEY))')
        
        # Set user as manager
        txn_group = prepare_set_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.manager_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_manager', 'manager': user_address}
        )
        # Global state
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.manager, decode_address(user_address))

        # Set back app creator as manager
        txn_group = prepare_set_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=user_address,
            new_manager_address=self.manager_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_manager', 'manager': self.manager_address}
        )
        # Global state
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.manager, decode_address(self.manager_address))

    def test_set_proposal_manager(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
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
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(MANAGER_KEY))')

        # Set user as manager
        txn_group = prepare_set_proposal_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.manager_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_proposal_manager', 'proposal_manager': user_address}
        )
        # Global state
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_manager, decode_address(user_address))

        # Set back app creator as manager
        txn_group = prepare_set_proposal_manager_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.manager_address,
            new_manager_address=self.manager_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.manager_address, self.manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_proposal_manager', 'proposal_manager': self.manager_address}
        )
        # Global state
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.proposal_manager, decode_address(self.manager_address))

    def test_set_voting_delay(self):
        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Permission
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        txn_group = prepare_set_voting_delay_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=user_address,
            new_voting_delay=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        txn_group = prepare_set_voting_delay_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
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
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.voting_delay, 1)

        txn_group = prepare_set_voting_delay_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            new_voting_delay=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_voting_delay',
                'voting_delay': 10,
            }
        )

        # Global state
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.voting_delay, 10)

    def test_set_voting_duration(self):
        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        # Permission
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        txn_group = prepare_set_voting_duration_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=user_address,
            new_voting_duration=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(PROPOSAL_MANAGER_KEY))')

        txn_group = prepare_set_voting_duration_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
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
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.voting_duration, 7)

        txn_group = prepare_set_voting_duration_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            new_voting_duration=10,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.proposal_manager_address, self.proposal_manager_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=staking_voting_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'set_voting_duration',
                'voting_duration': 10,
            }
        )

        # Global state
        proposal_voting_app_global_state = get_staking_voting_app_global_state(self.ledger, STAKING_VOTING_APP_ID)
        self.assertEqual(proposal_voting_app_global_state.voting_duration, 10)

    def test_get_box(self):
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 1_000_000)

        self.create_staking_voting_app(self.manager_address, self.proposal_manager_address)
        self.ledger.set_account_balance(get_application_address(STAKING_VOTING_APP_ID), STAKING_VOTING_APP_MINIMUM_BALANCE_REQUIREMENT)

        block_timestamp = self.vault_app_creation_timestamp + 2 * WEEK
        metadata = {
            "name": "Proposal 1",
            "description": "proposal description",
            "start_timestamp": int(block_timestamp),
            "end_timestamp": int(block_timestamp),
        }
        proposal_id = generate_cid_from_proposal_metadata(metadata)
        
        txn_group = prepare_get_box_transaction(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=user_address,
            box_name=get_staking_distribution_proposal_box_name(proposal_id),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=user_address, private_key=user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        self.assertEqual(len(logs), 1)
        return_data = logs[0][4:]
        is_box_exists = bytes_to_int(return_data[:8])
        box_size = bytes_to_int(return_data[8:10])
        box_data = return_data[10:]
        self.assertEqual(is_box_exists, 0)
        self.assertEqual(box_size, 0)
        self.assertEqual(len(box_data), 0)

        # Create box
        txn_group = prepare_create_staking_distribution_proposal_transactions(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=self.proposal_manager_address,
            proposal_id=proposal_id,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=self.proposal_manager_address, private_key=self.proposal_manager_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        
        txn_group = prepare_get_box_transaction(
            staking_voting_app_id=STAKING_VOTING_APP_ID,
            sender=user_address,
            box_name=get_staking_distribution_proposal_box_name(proposal_id),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(address=user_address, private_key=user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        self.assertEqual(len(logs), 1)
        return_data = logs[0][4:]
        is_box_exists = bytes_to_int(return_data[:8])
        box_size = bytes_to_int(return_data[8:10])
        box_data = return_data[10:]
        self.assertEqual(is_box_exists, 1)
        self.assertEqual(box_size, 49)
        self.assertEqual(len(box_data), 49)

        proposal_box_data = {
            'index': 0,
            'creation_timestamp': 1647356820,
            'voting_start_timestamp': 1647475200,
            'voting_end_timestamp': 1648080000,
            'voting_power': 0,
            'vote_count': 0,
            'is_cancelled': False
        }
        self.assertEqual(
            parse_box_staking_distribution_proposal(box_data),
            StakingDistributionProposal(**proposal_box_data)
        )
