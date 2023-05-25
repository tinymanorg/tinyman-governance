import unittest

from algojig import get_suggested_params, JigLedger
from algosdk.account import generate_account
from algosdk.constants import ZERO_ADDRESS
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.constants import TOTAL_POWERS, SLOPE_CHANGES, DAY, WEEK, locking_approval_program, voting_approval_program, PROPOSALS, MAX_OPTION_COUNT
from tests.utils import itob


class DummyAlgod:
    def suggested_params(self):
        return get_suggested_params()


class BaseTestCase(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.sp = get_suggested_params()

        cls.tiny_asset_creator_sk, cls.tiny_asset_creator_address = generate_account()
        cls.tiny_asset_id = 12345
        cls.tiny_params = dict(
            total=100_000_000_000_000_000_000_000_000_000,
            decimals=6,
            name="Tinyman",
            unit_name="TINY",
            creator=cls.tiny_asset_creator_address
        )

        # cls.app_id = 9000
        # cls.app_creator_sk, cls.app_creator_address = generate_account()
        # cls.user_sk, cls.user_address = generate_account()
        # cls.user_2_sk, cls.user_2_address = generate_account()

    def setUp(self):
        self.ledger = JigLedger()
        # self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        # self.ledger.set_account_balance(self.user_address, 10_000_000)
        # self.ledger.set_account_balance(self.user_2_address, 10_000_000)
        self.ledger.create_asset(self.tiny_asset_id, params=dict())
        # self.create_app()

    def create_locking_app(self, app_id, app_creator_address):
        if app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(app_creator_address, 1_000_000)

        self.ledger.create_app(
            app_id=app_id,
            approval_program=locking_approval_program,
            creator=app_creator_address,
            local_ints=0,
            local_bytes=0,
            global_ints=16,
            global_bytes=0
        )

        # 100_000 for basic min balance requirement
        self.ledger.set_account_balance(get_application_address(app_id), 0)
        # Opt-in
        self.ledger.set_account_balance(get_application_address(app_id), 0, asset_id=self.tiny_asset_id)
        self.ledger.set_global_state(
            app_id,
            {
                b'tiny_asset_id': self.tiny_asset_id,
                b'total_locked_amount': 0,
                b'total_power_count': 0,
            }
        )

    def create_voting_app(self, app_id, app_creator_address, locking_app_id):
        if app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(app_creator_address, 1_000_000)

        self.ledger.create_app(
            app_id=app_id,
            approval_program=voting_approval_program,
            creator=app_creator_address,
            local_ints=0,
            local_bytes=0,
            global_ints=16,
            global_bytes=16
        )

        # 100_000 for basic min balance requirement
        # self.ledger.set_account_balance(get_application_address(app_id), 1_000_000)
        self.ledger.set_global_state(
            app_id,
            {
                b'tiny_asset_id': self.tiny_asset_id,
                b'locking_app_id': locking_app_id,
                b'proposal_id_counter': 0,
                b'proposal_min_locked_amount': 0,
                b'proposal_min_lock_time': 0,
                b'proposal_threshold': 10,
                b'voting_delay': 0,
                b'voting_period': 0,
            }
        )

    def init_app_boxes(self, app_id):
        if app_id not in self.ledger.boxes:
            self.ledger.boxes[app_id] = {}

    def set_box_account_state(self, app_id, address, locked_amount, lock_end_time, first_index, last_index):
        assert (lock_end_time % WEEK) == 0
        self.init_app_boxes(app_id)
        self.ledger.boxes[app_id][decode_address(address)] = itob(locked_amount) + itob(lock_end_time) + itob(first_index) + itob(last_index)

    def set_box_account_power(self, app_id, address, index, locked_amount, locked_round, start_time, end_time, valid_until=0, delegatee=ZERO_ADDRESS):
        assert (start_time % DAY) == 0
        assert (end_time % WEEK) == 0
        self.init_app_boxes(app_id)
        self.ledger.boxes[app_id][decode_address(address) + itob(index)] = itob(locked_amount) + itob(locked_round) + itob(start_time) + itob(end_time) + itob(valid_until) + decode_address(delegatee)

    def set_box_total_power(self, app_id, timestamp, bias, slope, cumulative_power):
        assert (timestamp % DAY) == 0
        self.init_app_boxes(app_id)
        self.ledger.boxes[app_id][TOTAL_POWERS + itob(timestamp)] = itob(bias) + itob(slope, 16) + itob(cumulative_power, 16)

    def set_box_slope_change(self, app_id, timestamp, d_slope):
        assert (timestamp % WEEK) == 0
        self.init_app_boxes(app_id)
        self.ledger.boxes[app_id][SLOPE_CHANGES + itob(timestamp)] = itob(d_slope, 16)

    def init_global_indexes(self, app_id, index):
        self.ledger.global_states[app_id][b'first_index'] = index
        self.ledger.global_states[app_id][b'last_index'] = index

    def set_box_proposal(self, app_id, proposal_id, creation_time, voting_start_time, voting_end_time, option_count, vote_count=0, is_cancelled=False, is_executed=False, proposer=ZERO_ADDRESS, votes=None):
        self.init_app_boxes(app_id)

        if votes is None:
            votes = [0] * MAX_OPTION_COUNT

        box = [
            itob(creation_time),
            itob(voting_start_time),
            itob(voting_end_time),
            itob(option_count),
            itob(vote_count),
            itob(bool(is_cancelled)),
            itob(bool(is_executed)),
            decode_address(proposer),
            b"".join([itob(vote) for vote in votes])
        ]
        value = b"".join(box)

        self.ledger.boxes[app_id][PROPOSALS + proposal_id] = value
