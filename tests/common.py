import unittest
import uuid

from algojig import get_suggested_params, JigLedger
from algojig.gojig import run
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.constants import ZERO_ADDRESS
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.constants import TOTAL_POWERS, DAY, SLOPE_CHANGES, locking_approval_program, WEEK, LOCKING_APP_MINIMUM_BALANCE_REQUIREMENT, ACCOUNT_STATE_SIZE, ACCOUNT_POWER_BOX_SIZE, SLOPE_CHANGE_SIZE, ACCOUNT_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_SIZE, rewards_approval_program, REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT, REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_HISTORY, REWARD_HISTORY_SIZE, REWARD_HISTORY_BOX_SIZE
from tests.constants import voting_approval_program, PROPOSALS, MAX_OPTION_COUNT, TOTAL_POWER_SIZE
from tests.utils import itob, sign_txns, get_start_timestamp_of_week, parse_box_account_state, get_latest_checkpoint_indexes, get_latest_checkpoint_timestamp, get_required_minimum_balance_of_box, get_latest_account_power_indexes, get_account_power_index_at, get_total_power_index_at


def get_budget_increase_txn(sender, sp, index, boxes=None):
    if boxes is None:
        boxes = []
    boxes = boxes + ([(0, "")] * (8 - len(boxes)))

    return transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=sp,
        index=index,
        app_args=["increase_budget"],
        boxes=boxes,
        # Make transactions unique to avoid "transaction already in ledger" error
        note=uuid.uuid4().bytes
    )

class BaseTestCase(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.sp = get_suggested_params()

        cls.tiny_asset_creator_sk, cls.tiny_asset_creator_address = generate_account()
        cls.tiny_asset_id = 12345
        # 1_000_000_000 - 000_000
        # 100_000_000_000_000_000_000_000 - 000_000
        cls.tiny_params = dict(
            total=100_000_000_000_000_000_000_000_000_000,
            decimals=6,
            name="Tinyman",
            unit_name="TINY",
            creator=cls.tiny_asset_creator_address
        )

    def setUp(self):
        self.ledger = JigLedger()
        # self.ledger.set_account_balance(self.tiny_asset_creator_address, 1_000_000)
        self.ledger.create_asset(self.tiny_asset_id, params=dict())

    def low_level_eval(self, signed_txns, block_timestamp):
        self.ledger.init_ledger_db(block_timestamp)
        self.ledger.write()
        self.ledger.write_transactions(signed_txns)
        output = run("eval")
        return output

    def create_locking_app(self, app_id, app_creator_address, creation_timestamp):
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
                b'creation_timestamp': creation_timestamp,
            }
        )

    def init_locking_app(self, app_id, timestamp):
        # Min balance requirement
        self.ledger.set_account_balance(get_application_address(app_id), LOCKING_APP_MINIMUM_BALANCE_REQUIREMENT)
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

    def create_rewards_app(self, app_id, app_creator_address, locking_app_id, creation_timestamp):
        if app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(app_creator_address, 1_000_000)

        self.ledger.create_app(
            app_id=app_id,
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
            app_id,
            {
                b'tiny_asset_id': self.tiny_asset_id,
                b'locking_app_id': locking_app_id,
                b'creation_timestamp': creation_timestamp
            }
        )

    def init_rewards_app(self, app_id, timestamp, reward_amount=100_000_000):
        # Min balance requirement
        self.ledger.set_account_balance(get_application_address(app_id), REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT)
        # Opt-in
        self.ledger.set_account_balance(get_application_address(app_id), 0, asset_id=self.tiny_asset_id)
        self.set_box_reward_history(app_id, index=0, timestamp=timestamp, reward_amount=reward_amount)

        self.ledger.update_global_state(
            app_id,
            {
                b'reward_history_count': 1,
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

    def set_box_reward_history(self, app_id, index, timestamp, reward_amount):
        self.init_app_boxes(app_id)

        box_index = index // REWARD_HISTORY_BOX_ARRAY_LEN
        array_index = index % REWARD_HISTORY_BOX_ARRAY_LEN

        box_name = REWARD_HISTORY + itob(box_index)
        if box_name not in self.ledger.boxes[app_id]:
            self.ledger.boxes[app_id][box_name] = itob(0, 1) * REWARD_HISTORY_BOX_SIZE

        reward_history = itob(timestamp) + itob(reward_amount)
        start = array_index * REWARD_HISTORY_SIZE
        end = start + REWARD_HISTORY_SIZE
        data = bytearray(self.ledger.boxes[app_id][box_name])
        data[start:end] = reward_history
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

class LockingAppMixin:

    def create_checkpoints(self, block_timestamp, app_id):
        while True:
            txn_group = self.get_create_checkpoints_txn_group(self.user_address, block_timestamp, app_id)
            if not txn_group:
                break
            transaction.assign_group_id(txn_group)
            signed_txns = sign_txns(txn_group, self.user_sk)
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

    def get_create_lock_txn_group(self, user_address, locked_amount, lock_end_timestamp, app_id):
        latest_total_power_box_index, total_power_array_index = get_latest_checkpoint_indexes(self.ledger, app_id)

        account_state_box_name = decode_address(user_address)
        total_power_box_name = TOTAL_POWERS + itob(latest_total_power_box_index)
        account_power_box_name = decode_address(user_address) + itob(0)
        slope_change_box_name = SLOPE_CHANGES + itob(lock_end_timestamp)
        minimum_balance_increases = [
            get_required_minimum_balance_of_box(account_state_box_name, ACCOUNT_STATE_SIZE),
            get_required_minimum_balance_of_box(account_power_box_name, ACCOUNT_POWER_BOX_SIZE),
            get_required_minimum_balance_of_box(slope_change_box_name, SLOPE_CHANGE_SIZE)
        ]
        min_balance_increase = sum(minimum_balance_increases)

        txn_group = [
            transaction.PaymentTxn(
                sender=user_address,
                sp=self.sp,
                receiver=get_application_address(app_id),
                amt=min_balance_increase,
            ),
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=user_address,
                receiver=get_application_address(app_id),
                amt=locked_amount,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=[
                    "create_lock",
                    lock_end_timestamp,
                ],
                boxes=[
                    # Account State
                    (0, account_state_box_name),
                    # Account Power
                    (0, account_power_box_name),
                    # Total Power
                    (0, total_power_box_name),
                    # Total Power
                    (0, TOTAL_POWERS + itob(latest_total_power_box_index + 1)),
                    # Slope Change
                    (0, slope_change_box_name)
                ]
            ),
        ]

        if account_state_box := self.ledger.boxes[app_id].get(decode_address(user_address)):
            account_state = parse_box_account_state(account_state_box)
            power_count = account_state["power_count"]

            if power_count:
                txn_group.append(
                    get_budget_increase_txn(user_address, sp=self.sp, index=app_id)
                )
        return txn_group

    def get_create_checkpoints_txn_group(self, user_address, block_timestamp, app_id):
        # while True:
        box_index, array_index = get_latest_checkpoint_indexes(self.ledger, app_id)
        latest_checkpoint_timestamp = get_latest_checkpoint_timestamp(self.ledger, app_id)
        latest_checkpoint_week_timestamp = get_start_timestamp_of_week(latest_checkpoint_timestamp)
        this_week_timestamp = get_start_timestamp_of_week(block_timestamp)

        new_checkpoint_count = (this_week_timestamp  - latest_checkpoint_week_timestamp) // WEEK
        new_checkpoint_count = min(new_checkpoint_count, 6)

        slope_change_boxes = []
        for i in range(new_checkpoint_count):
            ts = latest_checkpoint_week_timestamp + ((i + 1) * WEEK)
            slope_change_boxes.append(
                (0, SLOPE_CHANGES + itob(ts))
            )

        # TODO: find the right formula
        # op_budget = 360 + (new_checkpoint_count - 1) * 270
        op_budget = 360 + new_checkpoint_count * 270
        increase_txn_count = (op_budget // 700)

        txn_group = []
        if (array_index + new_checkpoint_count) >= TOTAL_POWER_BOX_ARRAY_LEN:
            new_total_powers_box_name = TOTAL_POWERS + itob(box_index + 1)
            txn_group.append(
                transaction.PaymentTxn(
                    sender=user_address,
                    sp=self.sp,
                    receiver=get_application_address(app_id),
                    amt=2_500 + 400 * (len(new_total_powers_box_name) + TOTAL_POWER_BOX_SIZE)
                )
            )

        if new_checkpoint_count:
            txn_group += [
                transaction.ApplicationNoOpTxn(
                    sender=user_address,
                    sp=self.sp,
                    index=app_id,
                    app_args=[
                        "create_checkpoints",
                    ],
                    boxes=[
                        (0, TOTAL_POWERS + itob(box_index)),
                        (0, TOTAL_POWERS + itob(box_index + 1)),
                        *slope_change_boxes,
                    ]
                ),
                *[get_budget_increase_txn(user_address, sp=self.sp, index=app_id) for _ in range(increase_txn_count)],
            ]
        return txn_group

    def get_increase_lock_amount_txn_group(self, user_address, locked_amount, lock_end_timestamp, block_timestamp, app_id):
        total_powers_box_index, total_powers_array_index = get_latest_checkpoint_indexes(self.ledger, app_id)
        account_power_box_index, account_power_array_index = get_latest_account_power_indexes(self.ledger, app_id, user_address)
        start_timestamp_of_week = get_start_timestamp_of_week(block_timestamp)

        payment_amount = 0
        new_total_power_count = 1
        latest_checkpoint_timestamp = get_latest_checkpoint_timestamp(self.ledger, app_id)
        latest_checkpoint_week_timestamp = get_start_timestamp_of_week(latest_checkpoint_timestamp)
        this_week_timestamp = get_start_timestamp_of_week(block_timestamp)
        if latest_checkpoint_week_timestamp != this_week_timestamp:
            new_total_power_count += 1

        if account_power_array_index == ACCOUNT_POWER_BOX_ARRAY_LEN - 1:
            new_account_power_box_name = decode_address(user_address) + itob(total_powers_box_index + 1)
            payment_amount += 2_500 + 400 * (len(new_account_power_box_name) + ACCOUNT_POWER_BOX_SIZE)

        if total_powers_array_index + new_total_power_count >= TOTAL_POWER_BOX_ARRAY_LEN:
            new_total_powers_box_name = TOTAL_POWERS + itob(total_powers_box_index + 1)
            payment_amount += 2_500 + 400 * (len(new_total_powers_box_name) + TOTAL_POWER_BOX_SIZE)

        if payment_amount:
            txn_group = [
                transaction.PaymentTxn(
                    sender=user_address,
                    sp=self.sp,
                    receiver=get_application_address(app_id),
                    amt=payment_amount
                )
            ]
        else:
            txn_group = []

        txn_group += [
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=user_address,
                receiver=get_application_address(app_id),
                amt=locked_amount,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=[
                    "increase_lock_amount",
                ],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(account_power_box_index)),
                    (0, decode_address(self.user_address) + itob(account_power_box_index + 1)),
                    (0, TOTAL_POWERS + itob(total_powers_box_index)),
                    (0, TOTAL_POWERS + itob(total_powers_box_index + 1)),
                    (0, SLOPE_CHANGES + itob(lock_end_timestamp)),
                    (0, SLOPE_CHANGES + itob(start_timestamp_of_week))
                ]
            ),
            get_budget_increase_txn(self.user_address, sp=self.sp, index=app_id),
        ]
        return txn_group

    def get_extend_lock_end_time_txn_group(self, user_address, old_lock_end_timestamp, new_lock_end_timestamp, block_timestamp, app_id):
        total_powers_box_index, total_powers_array_index = get_latest_checkpoint_indexes(self.ledger, app_id)
        account_power_box_index, account_power_array_index = get_latest_account_power_indexes(self.ledger, app_id, user_address)
        start_timestamp_of_week = get_start_timestamp_of_week(block_timestamp)

        payment_amount = 0
        new_total_power_count = 1
        latest_checkpoint_timestamp = get_latest_checkpoint_timestamp(self.ledger, app_id)
        latest_checkpoint_week_timestamp = get_start_timestamp_of_week(latest_checkpoint_timestamp)
        this_week_timestamp = get_start_timestamp_of_week(block_timestamp)
        if latest_checkpoint_week_timestamp != this_week_timestamp:
            new_total_power_count += 1

        new_slope_change_box_name = SLOPE_CHANGES + itob(new_lock_end_timestamp)
        if new_slope_change_box_name not in self.ledger.boxes[app_id]:
            payment_amount += 2_500 + 400 * (len(new_slope_change_box_name) + SLOPE_CHANGE_SIZE)

        if account_power_array_index == ACCOUNT_POWER_BOX_ARRAY_LEN - 1:
            new_account_power_box_name = decode_address(user_address) + itob(total_powers_box_index + 1)
            payment_amount += 2_500 + 400 * (len(new_account_power_box_name) + ACCOUNT_POWER_BOX_SIZE)

        if total_powers_array_index + new_total_power_count >= TOTAL_POWER_BOX_ARRAY_LEN:
            new_total_powers_box_name = TOTAL_POWERS + itob(total_powers_box_index + 1)
            payment_amount += 2_500 + 400 * (len(new_total_powers_box_name) + TOTAL_POWER_BOX_SIZE)

        if payment_amount:
            txn_group = [
                transaction.PaymentTxn(
                    sender=user_address,
                    sp=self.sp,
                    receiver=get_application_address(app_id),
                    amt=payment_amount
                )
            ]
        else:
            txn_group = []

        txn_group += [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=[
                    "extend_lock_end_time",
                    new_lock_end_timestamp
                ],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(account_power_box_index)),
                    (0, decode_address(self.user_address) + itob(account_power_box_index + 1)),
                    (0, TOTAL_POWERS + itob(total_powers_box_index)),
                    (0, TOTAL_POWERS + itob(total_powers_box_index + 1)),
                    (0, SLOPE_CHANGES + itob(old_lock_end_timestamp)),
                    (0, new_slope_change_box_name),
                    (0, SLOPE_CHANGES + itob(start_timestamp_of_week)),
                ]
            ),
            get_budget_increase_txn(self.user_address, sp=self.sp, index=app_id),
        ]
        return txn_group

    def get_withdraw_txn_group(self, user_address, app_id):
        account_power_box_index, account_power_array_index = get_latest_account_power_indexes(self.ledger, app_id, user_address)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=app_id,
                app_args=["withdraw"],
                foreign_assets=[self.tiny_asset_id],
                boxes=[
                    (0, decode_address(user_address)),
                    (0, decode_address(self.user_address) + itob(account_power_box_index)),
                    (0, decode_address(self.user_address) + itob(account_power_box_index + 1)),
                ]
            )
        ]
        txn_group[0].fee *= 2
        return txn_group

    def get_get_tiny_power_of_txn_group(self, user_address, app_id):
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=["get_tiny_power_of"],
                accounts=[user_address],
                boxes=[
                    (0, decode_address(user_address)),
                ]
            )
        ]
        return txn_group

    def get_get_tiny_power_of_at_txn_group(self, user_address, timestamp, app_id):
        account_power_index = get_account_power_index_at(self.ledger, app_id, user_address, timestamp)
        if account_power_index is None:
            account_power_index = 0
            account_power_box_index = 0
        else:
            account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=["get_tiny_power_of_at", timestamp, account_power_index],
                accounts=[user_address],
                boxes=[
                    (0, decode_address(user_address)),
                    (0, decode_address(user_address) + itob(account_power_box_index)),
                    (0, decode_address(user_address) + itob(account_power_box_index + 1)),
                ]
            )
        ]
        return txn_group

    def get_get_total_tiny_power_txn_group(self, user_address, app_id):
        total_powers_box_index, _ = get_latest_checkpoint_indexes(self.ledger, app_id)
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=["get_total_tiny_power"],
                boxes=[
                    (0, TOTAL_POWERS + itob(total_powers_box_index)),
                ]
            )
        ]
        return txn_group

    def get_get_total_tiny_power_of_at_txn_group(self, user_address, timestamp, app_id):
        total_power_index = get_total_power_index_at(self.ledger, app_id, timestamp)
        if total_power_index is None:
            total_power_index = 0
            total_power_box_index = 0
        else:
            total_power_box_index = total_power_index // TOTAL_POWER_BOX_ARRAY_LEN

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=["get_total_tiny_power_at", timestamp, total_power_index],
                boxes=[
                    (0, TOTAL_POWERS + itob(total_power_box_index)),
                    (0, TOTAL_POWERS + itob(total_power_box_index + 1)),
                ]
            )
        ]
        return txn_group

    def get_get_total_cumulative_power_at_txn_group(self, user_address, timestamp, app_id):
        total_power_index = get_total_power_index_at(self.ledger, app_id, timestamp)
        if total_power_index is None:
            total_power_index = 0
            total_power_box_index = 0
        else:
            total_power_box_index = total_power_index // TOTAL_POWER_BOX_ARRAY_LEN

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=["get_total_cumulative_power_at", timestamp, total_power_index],
                boxes=[
                    (0, TOTAL_POWERS + itob(total_power_box_index)),
                    (0, TOTAL_POWERS + itob(total_power_box_index + 1)),
                ]
            )
        ]
        return txn_group

    def get_get_cumulative_power_of_at_txn_group(self, user_address, timestamp, app_id):
        account_power_index = get_account_power_index_at(self.ledger, app_id, user_address, timestamp)
        if account_power_index is None:
            account_power_index = 0
            account_power_box_index = 0
        else:
            account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=self.sp,
                index=app_id,
                app_args=["get_cumulative_power_of_at", timestamp, account_power_index],
                accounts=[user_address],
                boxes=[
                    (0, decode_address(user_address)),
                    (0, decode_address(user_address) + itob(account_power_box_index)),
                    (0, decode_address(user_address) + itob(account_power_box_index + 1)),
                ]
            )
        ]
        return txn_group