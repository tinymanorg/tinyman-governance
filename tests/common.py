import unittest

from algojig import get_suggested_params, JigLedger
from algosdk.account import generate_account
from algosdk.constants import ZERO_ADDRESS
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.constants import TOTAL_POWERS, SLOPE_CHANGES, DAY, WEEK, locking_approval_program, voting_approval_program, PROPOSALS, MAX_OPTION_COUNT, INITIAL_MINIMUM_BALANCE_REQUIREMENT, TOTAL_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_SIZE, TOTAL_POWER_SIZE
from tests.utils import itob

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

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_asset(self.tiny_asset_id, params=dict())

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

        self.ledger.set_global_state(
            app_id,
            {
                b'tiny_asset_id': self.tiny_asset_id,
                b'total_locked_amount': 0,
                b'total_power_count': 0,
            }
        )

    def init_locking_app(self, app_id, timestamp):
        # Min balance requirement
        self.ledger.set_account_balance(get_application_address(app_id), INITIAL_MINIMUM_BALANCE_REQUIREMENT)
        # Opt-in
        self.ledger.set_account_balance(get_application_address(app_id), 0, asset_id=self.tiny_asset_id)
        self.set_box_total_power(app_id, index=0, bias=0, timestamp=timestamp, slope=0, cumulative_power=0)

        self.ledger.update_global_state(
            app_id,
            {
                b'total_power_count': 1,
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

    # TODO: Delete setters
    def set_box_account_state(self, app_id, address, locked_amount, lock_end_time, first_index, last_index):
        assert (lock_end_time % WEEK) == 0
        self.init_app_boxes(app_id)
        self.ledger.boxes[app_id][decode_address(address)] = itob(locked_amount) + itob(lock_end_time) + itob(first_index) + itob(last_index)

    def set_box_account_power(self, app_id, address, index, locked_amount, locked_round, start_time, end_time, valid_until=0):
        assert (start_time % DAY) == 0
        assert (end_time % WEEK) == 0
        self.init_app_boxes(app_id)
        self.ledger.boxes[app_id][decode_address(address) + itob(index)] = itob(locked_amount) + itob(locked_round) + itob(start_time) + itob(end_time) + itob(valid_until)

    def set_box_total_power(self, app_id, index, bias, timestamp, slope, cumulative_power):
        self.init_app_boxes(app_id)

        box_index = index // TOTAL_POWER_BOX_ARRAY_LEN
        array_index = index % TOTAL_POWER_BOX_ARRAY_LEN

        box_name = TOTAL_POWERS + itob(box_index)
        if box_name not in self.ledger.boxes[app_id]:
            self.ledger.boxes[app_id][box_name] = itob(0, 1) * TOTAL_POWER_BOX_SIZE

        total_power = itob(bias) + itob(timestamp) + itob(slope, 16) + itob(cumulative_power, 16)
        start = array_index * TOTAL_POWER_SIZE
        end = start + TOTAL_POWER_SIZE
        data = bytearray(self.ledger.boxes[app_id][box_name])
        data[start:end] = total_power
        self.ledger.boxes[app_id][box_name] = bytes(data)

    def set_box_slope_change(self, app_id, timestamp, slope_delta):
        assert (timestamp % WEEK) == 0
        self.init_app_boxes(app_id)
        self.ledger.boxes[app_id][SLOPE_CHANGES + itob(timestamp)] = itob(slope_delta, 16)

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
