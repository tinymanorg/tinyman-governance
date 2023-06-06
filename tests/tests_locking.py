import unittest
import uuid
from datetime import timedelta, datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.common import BaseTestCase
from tests.constants import TOTAL_POWERS, DAY, SLOPE_CHANGES, locking_approval_program, locking_clear_state_program, WEEK, MAX_LOCK_TIME, INITIAL_MINIMUM_BALANCE_REQUIREMENT, ACCOUNT_STATE_SIZE, ACCOUNT_POWER_BOX_SIZE, SLOPE_CHANGE_SIZE, TWO_TO_THE_64, ACCOUNT_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_SIZE
from tests.utils import itob, sign_txns, parse_box_total_power, get_start_timestamp_of_week, parse_box_account_power, parse_box_account_state, parse_box_slope_change, get_slope, print_boxes, btoi, get_latest_checkpoint_indexes, get_latest_checkpoint_timestamp, get_required_minimum_balance_of_box, get_bias, get_latest_account_power_indexes, get_account_power_index_at, get_total_power_index_at


def get_budget_increase_txn(sender, sp, index):
    return transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=sp,
        index=index,
        app_args=["increase_budget"],
        boxes=([(0, "")] * 8),
        # Make transactions unique to avoid "transaction already in ledger" error
        note=uuid.uuid4().bytes
    )


class LockingTestCase(BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app_id = 9000
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_address = generate_account()
        cls.user_2_sk, cls.user_2_address = generate_account()
        cls.user_3_sk, cls.user_3_address = generate_account()

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.ledger.set_account_balance(self.user_address, 100_000_000)
        self.ledger.set_account_balance(self.user_2_address, 100_000_000)
        self.ledger.set_account_balance(self.user_3_address, 100_000_000)

    def create_checkpoints(self, block_timestamp):
        while True:
            txn_group = self.get_create_checkpoints_txn_group(self.user_address, block_timestamp)
            if not txn_group:
                break
            transaction.assign_group_id(txn_group)
            signed_txns = sign_txns(txn_group, self.user_sk)
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

    def get_create_lock_txn_group(self, user_address, locked_amount, lock_end_timestamp, app_id=None):
        if app_id is None:
            app_id = self.app_id

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
        return txn_group

    def get_create_checkpoints_txn_group(self, user_address, block_timestamp, app_id=None):
        if app_id is None:
            app_id = self.app_id

        # while True:
        box_index, array_index = get_latest_checkpoint_indexes(self.ledger, app_id)
        latest_checkpoint_timestamp = get_latest_checkpoint_timestamp(self.ledger, app_id)
        slope_change_timestamp = get_start_timestamp_of_week(latest_checkpoint_timestamp + WEEK)

        new_checkpoint_count = (max(block_timestamp, min(slope_change_timestamp, block_timestamp)) // DAY) - (latest_checkpoint_timestamp // DAY)
        # if latest_checkpoint_timestamp == block_timestamp:
        #     break
        new_checkpoint_count = min(new_checkpoint_count, 7)

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
                        (0, SLOPE_CHANGES + itob(slope_change_timestamp)),
                    ]
                ),
                *[get_budget_increase_txn(user_address, sp=self.sp, index=app_id) for _ in range(increase_txn_count)],
            ]
        return txn_group

    def get_increase_lock_amount_txn_group(self, user_address, locked_amount, lock_end_timestamp, block_timestamp, app_id=None):
        if app_id is None:
            app_id = self.app_id

        total_powers_box_index, total_powers_array_index = get_latest_checkpoint_indexes(self.ledger, app_id)
        account_power_box_index, account_power_array_index = get_latest_account_power_indexes(self.ledger, app_id, user_address)
        start_timestamp_of_week = get_start_timestamp_of_week(block_timestamp)

        payment_amount = 0
        if account_power_array_index == ACCOUNT_POWER_BOX_ARRAY_LEN - 1:
            new_account_power_box_name = decode_address(user_address) + itob(total_powers_box_index + 1)
            payment_amount += 2_500 + 400 * (len(new_account_power_box_name) + ACCOUNT_POWER_BOX_SIZE)

        if total_powers_array_index == TOTAL_POWER_BOX_ARRAY_LEN - 1:
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
            get_budget_increase_txn(self.user_address, sp=self.sp, index=self.app_id),
        ]
        return txn_group

    def get_extend_lock_end_time_txn_group(self, user_address, old_lock_end_timestamp, new_lock_end_timestamp, block_timestamp, app_id=None):
        if app_id is None:
            app_id = self.app_id

        total_powers_box_index, total_powers_array_index = get_latest_checkpoint_indexes(self.ledger, app_id)
        account_power_box_index, account_power_array_index = get_latest_account_power_indexes(self.ledger, app_id, user_address)
        start_timestamp_of_week = get_start_timestamp_of_week(block_timestamp)

        txn_group = []
        payment_amount = 0

        new_slope_change_box_name = SLOPE_CHANGES + itob(new_lock_end_timestamp)
        if new_slope_change_box_name not in self.ledger.boxes[app_id]:
            payment_amount += 2_500 + 400 * (len(new_slope_change_box_name) + SLOPE_CHANGE_SIZE)

        if account_power_array_index == ACCOUNT_POWER_BOX_ARRAY_LEN - 1:
            new_account_power_box_name = decode_address(user_address) + itob(total_powers_box_index + 1)
            payment_amount += 2_500 + 400 * (len(new_account_power_box_name) + ACCOUNT_POWER_BOX_SIZE)

        if total_powers_array_index == TOTAL_POWER_BOX_ARRAY_LEN - 1:
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
            get_budget_increase_txn(self.user_address, sp=self.sp, index=self.app_id),
        ]
        return txn_group

    def get_withdraw_txn_group(self, user_address, app_id=None):
        if app_id is None:
            app_id = self.app_id

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=app_id,
                app_args=["withdraw"],
                foreign_assets=[self.tiny_asset_id],
                boxes=[
                    (0, decode_address(user_address)),
                ]
            )
        ]
        txn_group[0].fee *= 2
        return txn_group

    def get_get_tiny_power_of_txn_group(self, user_address, app_id=None):
        if app_id is None:
            app_id = self.app_id

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

    def get_get_tiny_power_of_at_txn_group(self, user_address, timestamp, app_id=None):
        if app_id is None:
            app_id = self.app_id

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

    def get_get_total_tiny_power_txn_group(self, user_address, app_id=None):
        if app_id is None:
            app_id = self.app_id

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

    def get_get_total_tiny_power_of_at_txn_group(self, user_address, timestamp, app_id=None):
        if app_id is None:
            app_id = self.app_id

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

    def test_create_and_init_app(self):
        block_datetime = datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        txn_group = [
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=locking_approval_program.bytecode,
                clear_program=locking_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=3, num_byte_slices=0),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=1,
                foreign_assets=[self.tiny_asset_id],
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        app_id = block[b"txns"][0][b"apid"]

        self.assertDictEqual(
            self.ledger.global_states[app_id],
            {
                b'tiny_asset_id': self.tiny_asset_id,
                b'total_locked_amount': 0,
                b'total_power_count': 0
            }
        )

        total_powers_box_name = TOTAL_POWERS + itob(0)
        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                sp=self.sp,
                receiver=get_application_address(app_id),
                amt=INITIAL_MINIMUM_BALANCE_REQUIREMENT,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=app_id,
                app_args=[
                    "init",
                ],
                foreign_assets=[
                    self.tiny_asset_id
                ],
                boxes=[
                    (0, total_powers_box_name),
                ]
            ),
            get_budget_increase_txn(self.user_address, sp=self.sp, index=app_id),
        ]
        txn_group[1].fee *= 2

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        app_call_txn = block[b'txns'][1]
        opt_in_itx = app_call_txn[b'dt'][b'itx'][0][b'txn']
        self.assertDictEqual(
            opt_in_itx,
            {
                b'arcv': decode_address(get_application_address(app_id)),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(get_application_address(app_id)),
                b'type': b'axfer',
                b'xaid': self.tiny_asset_id
            }
        )

        total_powers = parse_box_total_power(self.ledger.boxes[app_id][total_powers_box_name])
        self.assertEqual(len(total_powers), 1)
        total_power = total_powers[0]
        self.assertDictEqual(
            total_power,
            {
                'bias': 0,
                'slope': 0,
                'cumulative_power': 0,
                'timestamp': block_timestamp
            }
        )

    def test_create_lock(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        amount = 20_000_000
        self.ledger.move(
            amount * 5,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))

        account_state_box_name = decode_address(self.user_address)
        total_power_box_name = TOTAL_POWERS + itob(0)
        account_power_box_name = decode_address(self.user_address) + itob(0)
        slope_change_box_name = SLOPE_CHANGES + itob(lock_end_timestamp)
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        self.assertDictEqual(
            parse_box_account_state(self.ledger.boxes[self.app_id][account_state_box_name]),
            {
                'locked_amount': amount,
                'lock_end_time': lock_end_timestamp,
                'lock_end_datetime': datetime.fromtimestamp(lock_end_timestamp, ZoneInfo("UTC")),
                'power_count': 1,
            }
        )
        self.assertDictEqual(
            parse_box_account_power(self.ledger.boxes[self.app_id][account_power_box_name])[0],
            {
                'bias': bias,
                'timestamp': block_timestamp,
                'datetime': block_datetime,
                'slope': amount * TWO_TO_THE_64 // MAX_LOCK_TIME
            }
        )
        self.assertDictEqual(
            parse_box_total_power(self.ledger.boxes[self.app_id][total_power_box_name])[1],
            {
                'bias': bias,
                'timestamp': block_timestamp,
                'slope': slope,
                'cumulative_power': 0
            }
        )
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][slope_change_box_name]),
            {
                'slope_delta': slope
            }
        )
        self.assertDictEqual(
            self.ledger.global_states[self.app_id],
            {
                b'total_power_count': 2,
                b'tiny_asset_id': self.tiny_asset_id,
                b'total_locked_amount': amount
            }
        )

    def test_create_lock_multiple(self):
        # 1. User 1 create lock, end datetime A
        # 2. User 2 create lock, end datetime A
        # 3. User 3 create lock, end datetime B

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        # User 1
        amount_1 = 200_000_000
        self.ledger.move(
            amount_1,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        # User 2
        amount_2 = 10_000_000
        self.ledger.move(
            amount_2,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_2_address
        )

        # User 3
        amount_3 = 500_000_000
        self.ledger.move(
            amount_3,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_3_address
        )

        lock_end_timestamp_1 = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))
        lock_end_timestamp_2 = lock_end_timestamp_1 + WEEK

        slope_1 = get_slope(amount_1)
        bias_1 = get_bias(slope_1, (lock_end_timestamp_1 - block_timestamp))

        slope_2 = get_slope(amount_2)
        bias_2 = get_bias(slope_2, (lock_end_timestamp_1 - block_timestamp))

        slope_3 = get_slope(amount_3)
        bias_3 = get_bias(slope_3, (lock_end_timestamp_2 - block_timestamp))

        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount_1, lock_end_timestamp=lock_end_timestamp_1)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        txn_group = self.get_create_lock_txn_group(user_address=self.user_2_address, locked_amount=amount_2, lock_end_timestamp=lock_end_timestamp_1)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        txn_group = self.get_create_lock_txn_group(user_address=self.user_3_address, locked_amount=amount_3, lock_end_timestamp=lock_end_timestamp_2)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_3_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        total_power_box_name = TOTAL_POWERS + itob(0)
        slope_change_box_name_1 = SLOPE_CHANGES + itob(lock_end_timestamp_1)
        slope_change_box_name_2 = SLOPE_CHANGES + itob(lock_end_timestamp_2)

        total_powers = parse_box_total_power(self.ledger.boxes[self.app_id][total_power_box_name])
        self.assertEqual(len(total_powers), 4)
        self.assertDictEqual(
            parse_box_total_power(self.ledger.boxes[self.app_id][total_power_box_name])[3],
            {
                'bias': bias_1 + bias_2 + bias_3,
                'timestamp': block_timestamp,
                'slope': slope_1 + slope_2 + slope_3,
                'cumulative_power': 0
            }
        )
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][slope_change_box_name_1]),
            {
                'slope_delta': slope_1 + slope_2
            }
        )
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][slope_change_box_name_2]),
            {
                'slope_delta': slope_3
            }
        )
        self.assertDictEqual(
            self.ledger.global_states[self.app_id],
            {
                b'total_power_count': 4,
                b'tiny_asset_id': self.tiny_asset_id,
                b'total_locked_amount': amount_1 + amount_2 + amount_3
            }
        )

    def test_create_lock_after_withdraw(self):
        # 1. Create lock
        # 2. Withdraw
        # 3. Create checkpoints
        # 4. Create lock again

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], amount)

        # Create checkpoints
        block_timestamp = lock_end_timestamp + 1
        self.create_checkpoints(block_timestamp)

        # Withdraw
        txn_group = self.get_withdraw_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], 0)

        # Create lock
        lock_end_timestamp = lock_end_timestamp + 2 * WEEK
        amount = 10_000_000
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], amount)

    def test_withdraw(self):
        # 1. Try to withdraw at lock end time
        # 2. Withdraw after the lock end time
        # 3. Try to withdraw second time

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], amount)

        # Withdraw
        txn_group = self.get_withdraw_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=lock_end_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(account_state.lock_end_time < Global.LatestTimestamp)')

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=lock_end_timestamp + 1)

        # Inner Txn
        inner_txns = block[b'txns'][0][b'dt'][b'itx']
        self.assertEqual(len(inner_txns), 1)
        self.assertDictEqual(
            inner_txns[0][b'txn'],
            {
                b'aamt': amount,
                b'arcv': decode_address(self.user_address),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(get_application_address(self.app_id)),
                b'type': b'axfer',
                b'xaid': self.tiny_asset_id
            }
        )

        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], 0)
        self.assertDictEqual(
            parse_box_account_state(self.ledger.boxes[self.app_id][decode_address(self.user_address)]),
            {
                'locked_amount': 0,
                'lock_end_time': 0,
                'lock_end_datetime': datetime.fromtimestamp(0, ZoneInfo("UTC")),
                'power_count': 1,
            }
        )

        # Try to withdraw again
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=lock_end_timestamp + 1)
        self.assertEqual(e.exception.source['line'], 'assert(locked_amount)')

    def test_increase_lock_amount(self):
        # 1. Create lock
        # 2. Increase lock amount
        # 3. Create checkpoints
        # 4. Increase lock amount

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], amount)

        # Increase
        block_timestamp = block_timestamp + DAY // 2
        amount = 30_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_increase_lock_amount_txn_group(self.user_address, amount, lock_end_timestamp, block_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        # Create checkpoints
        block_timestamp = block_timestamp + 3 * DAY
        self.create_checkpoints(block_timestamp)

        # Increase
        block_timestamp = block_timestamp + DAY // 2
        amount = 40_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_increase_lock_amount_txn_group(self.user_address, amount, lock_end_timestamp, block_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

    def test_extend_lock_end_time(self):
        # 1. Create lock
        # 2. Extend 2 weeks
        # 3. Create checkpoints
        # 4. Extend 4 weeks

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
        amount = 20_000_000
        slope = get_slope(amount)
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], amount)
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][SLOPE_CHANGES + itob(lock_end_timestamp)]),
            {
                'slope_delta': slope
            }
        )

        # Extend 2 weeks
        block_timestamp = block_timestamp + DAY // 2
        old_lock_end_timestamp = lock_end_timestamp
        new_lock_end_timestamp = lock_end_timestamp + 2 * WEEK
        txn_group =  self.get_extend_lock_end_time_txn_group(self.user_address, old_lock_end_timestamp, new_lock_end_timestamp, block_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][SLOPE_CHANGES + itob(old_lock_end_timestamp)]),
            {
                'slope_delta': 0
            }
        )
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][SLOPE_CHANGES + itob(new_lock_end_timestamp)]),
            {
                'slope_delta': slope
            }
        )

        lock_end_timestamp = new_lock_end_timestamp


        # Create checkpoints
        block_timestamp = block_timestamp + 3 * DAY
        self.create_checkpoints(block_timestamp)

        # Extend 4 weeks
        block_timestamp = block_timestamp + DAY // 2
        old_lock_end_timestamp = lock_end_timestamp
        new_lock_end_timestamp = lock_end_timestamp + 4 * WEEK
        txn_group =  self.get_extend_lock_end_time_txn_group(self.user_address, old_lock_end_timestamp, new_lock_end_timestamp, block_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][SLOPE_CHANGES + itob(old_lock_end_timestamp)]),
            {
                'slope_delta': 0
            }
        )
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][SLOPE_CHANGES + itob(new_lock_end_timestamp)]),
            {
                'slope_delta': slope
            }
        )

    def test_get_tiny_power_of(self):
        # 1. Get Power, there is no lock
        # 2. Create lock
        # 3. Get Power
        # 4. Get Power (after 1 day)
        # 5. Get Power (after 1 week)
        # 6. Get Power - Expired
        # 7. Withdraw
        # 8. Get Power

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        txn_group = self.get_get_tiny_power_of_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        txn_group = self.get_get_tiny_power_of_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias)

        block_timestamp += DAY
        bias_delta = get_bias(slope, block_timestamp - lock_start_timestamp)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias - bias_delta)

        block_timestamp += WEEK
        bias_delta = get_bias(slope, block_timestamp - lock_start_timestamp)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias - bias_delta)

        block_timestamp = lock_end_timestamp
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        block_timestamp = lock_end_timestamp + 1
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        txn_group = self.get_withdraw_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        txn_group = self.get_get_tiny_power_of_txn_group(self.user_address)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

    def test_get_tiny_power_of_at(self):
        # 1. Get Power, there is no lock
        # 2. Create lock
        # 3. Get Power
        # 4. Get Power (after 1 day)
        # 5. Get Power (after 1 week)
        # 6. Get Power - Expired
        # 7. Withdraw
        # 8. Get Power

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        power_at_timestamp = block_timestamp - DAY
        txn_group = self.get_get_tiny_power_of_at_txn_group(self.user_address, power_at_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        power_at_timestamp = block_timestamp
        txn_group = self.get_get_tiny_power_of_at_txn_group(self.user_address, power_at_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        power_at_timestamp = block_timestamp
        block_timestamp = lock_end_timestamp + DAY

        txn_group = self.get_get_tiny_power_of_at_txn_group(self.user_address, power_at_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias)

        txn_group = self.get_get_tiny_power_of_at_txn_group(self.user_address, power_at_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias)

        power_at_timestamp += DAY
        bias_delta = get_bias(slope, power_at_timestamp - lock_start_timestamp)
        txn_group = self.get_get_tiny_power_of_at_txn_group(self.user_address, power_at_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias - bias_delta)

        power_at_timestamp += WEEK
        bias_delta = get_bias(slope, power_at_timestamp - lock_start_timestamp)
        txn_group = self.get_get_tiny_power_of_at_txn_group(self.user_address, power_at_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias - bias_delta)

        power_at_timestamp = lock_end_timestamp
        txn_group = self.get_get_tiny_power_of_at_txn_group(self.user_address, power_at_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        block_timestamp = lock_end_timestamp + 1
        txn_group = self.get_get_tiny_power_of_at_txn_group(self.user_address, power_at_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

    def test_get_total_tiny_power(self):
        # 1. Get total power, there is no lock
        # 2. Create lock
        # 3. Get total power
        # 4. Get total power (after 1 day)
        # 5. Get total power (after 1 week)
        # 6. Get total power - Expired
        # 7. Withdraw
        # 8. Get total power

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        txn_group = self.get_get_total_tiny_power_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        txn_group = self.get_get_total_tiny_power_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias)

        block_timestamp += DAY
        bias_delta = get_bias(slope, block_timestamp - lock_start_timestamp)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertAlmostEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias - bias_delta, delta=(block_timestamp - lock_start_timestamp) // DAY)

        block_timestamp += WEEK
        self.create_checkpoints(block_timestamp)
        bias_delta = get_bias(slope, block_timestamp - lock_start_timestamp)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertAlmostEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), bias - bias_delta, delta=(block_timestamp - lock_start_timestamp) // DAY)

        block_timestamp = lock_end_timestamp
        self.create_checkpoints(block_timestamp)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        block_timestamp = lock_end_timestamp + 1
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        # Withdraw
        txn_group = self.get_withdraw_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        txn_group = self.get_get_total_tiny_power_txn_group(self.user_address)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

    def test_get_total_tiny_power_at(self):
        # 1. User 1 create lock, end datetime A
        # 2. User 2 create lock, end datetime A
        # 3. User 3 create lock, end datetime B

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        txn_group = self.get_get_total_tiny_power_of_at_txn_group(self.user_address, block_timestamp - DAY)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        # User 1
        amount_1 = 200_000_000
        self.ledger.move(
            amount_1,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        # User 2
        amount_2 = 10_000_000
        self.ledger.move(
            amount_2,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_2_address
        )

        # User 3
        amount_3 = 500_000_000
        self.ledger.move(
            amount_3,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_3_address
        )

        lock_end_timestamp_1 = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))
        lock_end_timestamp_2 = lock_end_timestamp_1 + WEEK

        slope_1 = get_slope(amount_1)
        bias_1 = get_bias(slope_1, (lock_end_timestamp_1 - block_timestamp))

        slope_2 = get_slope(amount_2)
        bias_2 = get_bias(slope_2, (lock_end_timestamp_1 - block_timestamp))

        slope_3 = get_slope(amount_3)
        bias_3 = get_bias(slope_3, (lock_end_timestamp_2 - block_timestamp))

        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount_1, lock_end_timestamp=lock_end_timestamp_1)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        txn_group = self.get_create_lock_txn_group(user_address=self.user_2_address, locked_amount=amount_2, lock_end_timestamp=lock_end_timestamp_1)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_2_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        txn_group = self.get_create_lock_txn_group(user_address=self.user_3_address, locked_amount=amount_3, lock_end_timestamp=lock_end_timestamp_2)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_3_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

        block_timestamp = lock_end_timestamp_2 + DAY
        self.create_checkpoints(block_timestamp)

        power_at = lock_end_timestamp_1

        txn_group = self.get_get_total_tiny_power_of_at_txn_group(self.user_address, power_at)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 2397276)

        power_at += DAY
        txn_group = self.get_get_total_tiny_power_of_at_txn_group(self.user_address, power_at)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 2054811)

        power_at = lock_end_timestamp_2
        txn_group = self.get_get_total_tiny_power_of_at_txn_group(self.user_address, power_at)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

        power_at += 1
        txn_group = self.get_get_total_tiny_power_of_at_txn_group(self.user_address, power_at)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(btoi(block[b'txns'][0][b'dt'][b'lg'][-1]), 0)

    def test_multiple_increase_lock_amount(self):
        # 1. Create lock
        # 2. Increase lock amount 200x

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        # Create lock
        increase_count = 200
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 30 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount * (increase_count + 1),
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], amount)

        # Increase
        for i in range(increase_count):
            block_timestamp = block_timestamp + DAY // 2
            txn_group = self.get_increase_lock_amount_txn_group(self.user_address, amount, lock_end_timestamp, block_timestamp)
            transaction.assign_group_id(txn_group)
            signed_txns = sign_txns(txn_group, self.user_sk)
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
            self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], amount * (i + 2))

    def test_multiple_extend_lock_end_time(self):
        # 1. Create lock
        # 2. Extend 200x

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_locking_app(self.app_id, self.app_creator_address)
        self.init_locking_app(self.app_id, timestamp=last_checkpoint_timestamp)

        # Create lock
        increase_count = 200
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        txn_group = self.get_create_lock_txn_group(user_address=self.user_address, locked_amount=amount, lock_end_timestamp=lock_end_timestamp)
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[self.app_id][b'total_locked_amount'], amount)

        # Extend
        for i in range(increase_count):
            block_timestamp = block_timestamp + DAY // 2
            new_lock_end_timestamp = lock_end_timestamp + WEEK
            txn_group = self.get_extend_lock_end_time_txn_group(self.user_address, lock_end_timestamp, new_lock_end_timestamp, block_timestamp)
            lock_end_timestamp = new_lock_end_timestamp

            transaction.assign_group_id(txn_group)
            signed_txns = sign_txns(txn_group, self.user_sk)
            self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)

    @unittest.skip
    def test_all(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=0, tzinfo=ZoneInfo("UTC"))
        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=13_304_900,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "init",
                ],
                foreign_assets=[self.tiny_asset_id],
                boxes=[
                    (0, TOTAL_POWERS + itob(0)),
                ]
            ),
        ]
        txn_group[1].fee *= 2
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        print("TXN Init")
        self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print_boxes(self.ledger.boxes[self.app_id])

        block_datetime += timedelta(days=2)
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "create_checkpoints",
                ],
                boxes=[
                    (0, TOTAL_POWERS + itob(0)),
                    (0, SLOPE_CHANGES + itob(get_start_timestamp_of_week(int(block_datetime.timestamp())))),
                ]
            ),
            get_budget_increase_txn(self.user_address, sp=self.sp, index=self.app_id),
        ]
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        print("TXN Create Checkpoints")
        self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print_boxes(self.ledger.boxes[self.app_id])

        amount = 10_000_000
        self.ledger.move(
            amount * 5,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        block_datetime += timedelta(hours=2)
        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))

        txn_group = [
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=amount,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "create_lock",
                    lock_end_timestamp,
                ],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0)),
                    (0, TOTAL_POWERS + itob(0)),
                    (0, SLOPE_CHANGES + itob(lock_end_timestamp))
                ]
            ),
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        print("TXN Create Lock")
        self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print_boxes(self.ledger.boxes[self.app_id])

        slope = amount * 2**64 // MAX_LOCK_TIME
        bias = (slope * (lock_end_timestamp - int(block_datetime.timestamp()))) // 2**64

        self.assertDictEqual(
            parse_box_account_state(self.ledger.boxes[self.app_id][decode_address(self.user_address)]),
            {
                'locked_amount': 10000000,
                'lock_end_time': lock_end_timestamp,
                'lock_end_datetime': datetime.fromtimestamp(lock_end_timestamp, ZoneInfo("UTC")),
                'power_count': 1,
            }
        )
        self.assertDictEqual(
            parse_box_account_power(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(0)])[0],
            {
                'bias': bias,
                'timestamp': int(block_datetime.timestamp()),
                'datetime': block_datetime,
                'slope': amount * 2**64 // MAX_LOCK_TIME
            }
        )
        self.assertDictEqual(
            parse_box_total_power(self.ledger.boxes[self.app_id][TOTAL_POWERS + itob(0)])[3],
            {
                'bias': bias,
                'timestamp': int(block_datetime.timestamp()),
                'slope': slope,
                'cumulative_power': 0
            }
        )
        self.assertDictEqual(
            parse_box_slope_change(self.ledger.boxes[self.app_id][SLOPE_CHANGES + itob(lock_end_timestamp)]),
            {
                'slope_delta': slope
            }
        )
        self.assertDictEqual(
            self.ledger.global_states[self.app_id],
            {
                b'total_power_count': 4,
                b'tiny_asset_id': self.tiny_asset_id,
                b'total_locked_amount': amount
            }
        )

        block_datetime += timedelta(hours=1)
        txn_group = [
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=amount,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "increase_lock_amount",
                ],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0)),
                    (0, TOTAL_POWERS + itob(0)),
                    (0, SLOPE_CHANGES + itob(lock_end_timestamp))
                ]
            ),
            get_budget_increase_txn(self.user_address, sp=self.sp, index=self.app_id),
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        print("TXN Increase amount")
        self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print_boxes(self.ledger.boxes[self.app_id])

        block_datetime += timedelta(hours=1)
        old_lock_end_timestamp = lock_end_timestamp
        new_lock_end_timestamp = old_lock_end_timestamp + WEEK
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "extend_lock_end_time",
                    new_lock_end_timestamp
                ],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0)),
                    (0, TOTAL_POWERS + itob(0)),
                    (0, SLOPE_CHANGES + itob(old_lock_end_timestamp)),
                    (0, SLOPE_CHANGES + itob(new_lock_end_timestamp))
                ]
            ),
            get_budget_increase_txn(self.user_address, sp=self.sp, index=self.app_id),
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        print("TXN Extend Lock End Time")
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print_boxes(self.ledger.boxes[self.app_id])

        # Get Tiny Power Of
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_tiny_power_of"],
                accounts=[self.user_address],
                boxes=[
                    (0, decode_address(self.user_address)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("TXN Get Tiny Power Of")
        print_boxes(self.ledger.boxes[self.app_id])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        # Get Tiny Power Of At
        point_at = int(block_datetime.timestamp())
        power_index = 2
        # block_datetime += timedelta(days=30)
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_tiny_power_of_at", point_at, power_index],
                accounts=[self.user_address],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("TXN Get Tiny Power Of At")
        print_boxes(self.ledger.boxes[self.app_id])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        # Get Total Tiny Power
        # block_datetime += timedelta(days=7)
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_total_tiny_power"],
                boxes=[
                    (0, TOTAL_POWERS + itob(0)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("TXN Get Total Tiny Power")
        print_boxes(self.ledger.boxes[self.app_id])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        # Get Total Tiny Power At
        # block_datetime += timedelta(days=7)
        point_at = int(block_datetime.timestamp()) + DAY * 5
        total_power_index = 5
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_total_tiny_power_at", point_at, total_power_index],
                boxes=[
                    (0, TOTAL_POWERS + itob(0)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("TXN Get Total Tiny Power At")
        print_boxes(self.ledger.boxes[self.app_id])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        # Withdraw
        block_datetime = datetime.fromtimestamp(new_lock_end_timestamp, ZoneInfo("UTC")) + timedelta(hours=1)
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["withdraw"],
                accounts=[self.user_address],
                foreign_assets=[self.tiny_asset_id],
                boxes=[
                    (0, decode_address(self.user_address)),
                ]
            )
        ]
        txn_group[0].fee *= 2
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("TXN Withdraw")
        print_boxes(self.ledger.boxes[self.app_id])

        # Get Tiny Power Of
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_tiny_power_of"],
                boxes=[
                    (0, decode_address(self.user_address)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("TXN Get Tiny Power Of")
        print_boxes(self.ledger.boxes[self.app_id])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][-1]))

        while True:
            box_index, _ = get_latest_checkpoint_indexes(self.ledger, self.app_id)
            latest_checkpoint_timestamp = get_latest_checkpoint_timestamp(self.ledger, self.app_id)
            slope_change_timestamp = get_start_timestamp_of_week(latest_checkpoint_timestamp + WEEK)

            new_checkpoint_count = (min(slope_change_timestamp, int(block_datetime.timestamp())) // DAY) - (latest_checkpoint_timestamp // DAY)
            if latest_checkpoint_timestamp == int(block_datetime.timestamp()):
                break

            op_budget = 360 + (new_checkpoint_count - 1) * 270
            increase_txn_count = (op_budget // 700)

            txn_group = [
                transaction.ApplicationNoOpTxn(
                    sender=self.user_address,
                    sp=self.sp,
                    index=self.app_id,
                    app_args=[
                        "create_checkpoints",
                    ],
                    boxes=[
                        (0, TOTAL_POWERS + itob(box_index)),
                        (0, TOTAL_POWERS + itob(box_index + 1)),
                        (0, SLOPE_CHANGES + itob(slope_change_timestamp)),
                    ]
                ),
                *[get_budget_increase_txn(self.user_address, sp=self.sp, index=self.app_id) for _ in range(increase_txn_count)],
            ]
            transaction.assign_group_id(txn_group)
            signed_txns = sign_txns(txn_group, self.user_sk)

            print("TXN Create Checkpoints")
            block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
            # print("Opb", new_checkpoint_count, new_checkpoint_count * 700, [btoi(l) for l in block[b'txns'][0][b'dt'][b'lg']])
            print_boxes(self.ledger.boxes[self.app_id])

        # Create lock again
        box_index, _ = get_latest_checkpoint_indexes(self.ledger, self.app_id)
        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=20)).timestamp()))
        txn_group = [
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=amount,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "create_lock",
                    lock_end_timestamp,
                ],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0)),
                    (0, TOTAL_POWERS + itob(box_index)),
                    (0, TOTAL_POWERS + itob(box_index + 1)),
                    (0, SLOPE_CHANGES + itob(lock_end_timestamp))
                ]
            ),
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        print("TXN Create Lock")
        self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print_boxes(self.ledger.boxes[self.app_id])
