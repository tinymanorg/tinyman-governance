import uuid
from datetime import timedelta, datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError, gojig
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address, encode_address
from algosdk.logic import get_application_address

from tests.common import BaseTestCase
from tests.constants import TOTAL_POWERS, DAY, SLOPE_CHANGES, locking_approval_program, locking_clear_state_program, WEEK, MAX_LOCK_TIME
from tests.utils import get_start_time_of_next_day, itob, sign_txns, parse_box_total_power, get_start_time_of_week, parse_box_account_power, parse_box_account_state, parse_box_slope_change, get_slope, get_voting_power, print_boxes, btoi, get_start_time_of_day


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

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.ledger.set_account_balance(self.user_address, 100_000_000)
        self.ledger.set_account_balance(self.user_2_address, 100_000_000)
        self.create_locking_app(self.app_id, self.app_creator_address)

    def create_checkpoint(self, block_datetime):
        block_timestamp = int(block_datetime.timestamp())
        start_time = get_start_time_of_next_day(block_timestamp)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "create_checkpoint",
                ],
                boxes=[
                    (0, TOTAL_POWERS + itob(start_time - DAY)),
                    (0, TOTAL_POWERS + itob(start_time)),
                    (0, SLOPE_CHANGES + itob(start_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        return block

    def test_create_app(self):
        block_datetime = datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC"))
        lock_start_time = get_start_time_of_next_day(int(block_datetime.timestamp()))

        txn_group = [
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=locking_approval_program.bytecode,
                clear_program=locking_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=16, num_byte_slices=0),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=1,
                foreign_assets=[self.tiny_asset_id],
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)

        # with self.assertRaises(LogicEvalError) as e:
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print(block)
        breakpoint()
        app_id = block[b"txns"][0][b"apid"]

        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                sp=self.sp,
                receiver=get_application_address(app_id),
                amt=1_000_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=app_id,
                app_args=[
                    "init_checkpoint",
                ],
                boxes=[
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        self.assertDictEqual(
            parse_box_total_power(self.ledger.boxes[app_id][TOTAL_POWERS + itob(lock_start_time)]),
            {'bias': 0, 'slope': 0, 'cumulative_power': 0}
        )

    def test_create_first_lock(self):
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
                boxes=[
                    (0, TOTAL_POWERS + itob(0)),
                ]
            ),
        ]
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
                    (0, SLOPE_CHANGES + itob(get_start_time_of_week(int(block_datetime.timestamp())))),
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
        lock_end_timestamp = get_start_time_of_week(int((block_datetime + timedelta(days=50)).timestamp()))

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
                'index': 0,
            }
        )
        self.assertDictEqual(
            parse_box_account_power(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(0)]),
            {
                'bias': bias,
                'timestamp': int(block_datetime.timestamp()),
                'datetime': block_datetime,
                'slope': amount * 2**64 // MAX_LOCK_TIME,
                'delegatee': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ'
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
                'd_slope': slope
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
        for log in block[b'txns'][0][b'dt'][b'lg']:
            print(btoi(log))

    def test_increase_lock_amount(self):
        self.ledger.move(
            1_000_000_000,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_time = int(block_datetime.timestamp())
        next_day_timestamp = get_start_time_of_next_day(int(block_datetime.timestamp()))

        account_first_index = account_last_index = 0
        locked_amount = 100_000_000
        lock_start_time = next_day_timestamp
        lock_end_time = get_start_time_of_week(int((block_datetime + timedelta(days=50)).timestamp()))
        slope = get_slope(locked_amount)
        remaining_time = lock_end_time - lock_start_time
        voting_power = get_voting_power(slope, remaining_time)

        self.set_box_account_state(self.app_id, self.user_address, locked_amount, lock_end_time, account_first_index, account_last_index)
        self.set_box_account_power(self.app_id, self.user_address, index=0, locked_amount=locked_amount, locked_round=2, start_time=lock_start_time, end_time=lock_end_time)
        self.set_box_total_power(self.app_id, next_day_timestamp, bias=voting_power, slope=slope, cumulative_power=0)
        self.init_global_indexes(self.app_id, index=next_day_timestamp)
        self.set_box_slope_change(self.app_id, lock_end_time, slope)
        print_boxes(self.ledger.boxes[self.app_id])

        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=1_000_000,
                sp=self.sp,
            ),
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=10_000_000,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["increase_lock_amount"],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0)),
                    (0, decode_address(self.user_address) + itob(1)),
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                    (0, SLOPE_CHANGES + itob(lock_end_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_time)
        print("---TXN---")
        print_boxes(self.ledger.boxes[self.app_id])

    def test_extend_lock_end_time(self):
        self.ledger.move(
            1_000_000_000,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_time = int(block_datetime.timestamp())
        next_day_timestamp = get_start_time_of_next_day(int(block_datetime.timestamp()))

        account_first_index = account_last_index = 0
        locked_amount = 100_000_000
        lock_start_time = next_day_timestamp
        lock_end_time = get_start_time_of_week(int((block_datetime + timedelta(days=50)).timestamp()))
        slope = get_slope(locked_amount)
        remaining_time = lock_end_time - lock_start_time
        voting_power = get_voting_power(slope, remaining_time)

        self.set_box_account_state(self.app_id, self.user_address, locked_amount, lock_end_time, account_first_index, account_last_index)
        self.set_box_account_power(self.app_id, self.user_address, index=0, locked_amount=locked_amount, locked_round=2, start_time=lock_start_time, end_time=lock_end_time)
        self.set_box_total_power(self.app_id, next_day_timestamp, bias=voting_power, slope=slope, cumulative_power=0)
        self.init_global_indexes(self.app_id, index=next_day_timestamp)
        self.set_box_slope_change(self.app_id, lock_end_time, slope)
        print_boxes(self.ledger.boxes[self.app_id])

        # Extend
        self.create_checkpoint(block_datetime + timedelta(1))
        self.create_checkpoint(block_datetime + timedelta(2))
        block_datetime += timedelta(days=2)
        block_time = int(block_datetime.timestamp())
        next_day_timestamp = get_start_time_of_next_day(int(block_datetime.timestamp()))

        lock_start_time = next_day_timestamp
        old_lock_end_time = lock_end_time
        new_lock_end_time = old_lock_end_time + WEEK * 7
        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=1_000_000,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["extend_lock_end_time", new_lock_end_time],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(account_last_index)),
                    (0, decode_address(self.user_address) + itob(account_last_index + 1)),
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                    (0, SLOPE_CHANGES + itob(old_lock_end_time)),
                    (0, SLOPE_CHANGES + itob(new_lock_end_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_time)
        account_last_index += 1
        print("---TXN---")
        print_boxes(self.ledger.boxes[self.app_id])

    def test(self):
        self.ledger.move(
            1_000_000_000,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )
        self.ledger.move(
            1_000_000_000,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_2_address
        )

        block_datetime = datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC"))
        lock_end_datetime = block_datetime + timedelta(days=50)
        lock_start_time = get_start_time_of_next_day(int(block_datetime.timestamp()))
        lock_end_time = get_start_time_of_week(int(lock_end_datetime.timestamp()))

        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                sp=self.sp,
                receiver=get_application_address(self.app_id),
                amt=1_000_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "init_checkpoint",
                ],
                boxes=[
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 0---", "Round")
        print_boxes(self.ledger.boxes[self.app_id])

        txn_group = [
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=10_000_000,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "create_lock",
                    lock_start_time,
                    lock_end_time,
                ],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0)),
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                    (0, SLOPE_CHANGES + itob(lock_end_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))

        print("---TXN 1---", "Round", block[b'rnd'])
        print_boxes(self.ledger.boxes[self.app_id])

        # New lock
        block_datetime = block_datetime + timedelta(days=1)
        # lock_end_datetime = block_datetime + timedelta(days=100)
        lock_start_time = get_start_time_of_next_day(int(block_datetime.timestamp()))

        block = self.create_checkpoint(block_datetime)

        print("---TXN 1.01--- Create Checkpoint", "Round", block[b'rnd'])
        print_boxes(self.ledger.boxes[self.app_id])

        txn_group = [
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=self.user_2_address,
                receiver=get_application_address(self.app_id),
                amt=15_000_000,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_2_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "create_lock",
                    lock_start_time,
                    lock_end_time,
                ],
                boxes=[
                    (0, decode_address(self.user_2_address)),
                    (0, decode_address(self.user_2_address) + itob(0)),
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                    (0, SLOPE_CHANGES + itob(lock_end_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_2_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))

        print("---TXN 1.1---", "Round", block[b'rnd'])
        print_boxes(self.ledger.boxes[self.app_id])

        block_time = int(block_datetime.timestamp())
        lock_start_time = get_start_time_of_next_day(block_time)
        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=1_000_000,
                sp=self.sp,
            ),
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=10_000_000,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["increase_lock_amount"],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0)),
                    (0, decode_address(self.user_address) + itob(1)),
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                    (0, SLOPE_CHANGES + itob(lock_end_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_time)
        print("---TXN 2---", "Round", block[b'rnd'])
        print_boxes(self.ledger.boxes[self.app_id])

        # Increase again
        block_time = int(block_datetime.timestamp())
        lock_start_time = get_start_time_of_next_day(block_time)
        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=1_000_000,
                sp=self.sp,
            ),
            transaction.AssetTransferTxn(
                index=self.tiny_asset_id,
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=15_000_000,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["increase_lock_amount"],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(1)),
                    (0, decode_address(self.user_address) + itob(2)),
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                    (0, SLOPE_CHANGES + itob(lock_end_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_time)
        print("---TXN 3---", "Round", block[b'rnd'])
        print_boxes(self.ledger.boxes[self.app_id])

        # Extend time
        old_lock_end_time = get_start_time_of_week(int(lock_end_datetime.timestamp()))
        lock_end_datetime = lock_end_datetime + timedelta(days=20)
        new_lock_end_time = get_start_time_of_week(int(lock_end_datetime.timestamp()))
        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_address,
                receiver=get_application_address(self.app_id),
                amt=1_000_000,
                sp=self.sp,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["extend_lock_end_time", new_lock_end_time],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(2)),
                    (0, decode_address(self.user_address) + itob(3)),
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                    (0, SLOPE_CHANGES + itob(old_lock_end_time)),
                    (0, SLOPE_CHANGES + itob(new_lock_end_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 4---", "Round", block[b'rnd'])
        print_boxes(self.ledger.boxes[self.app_id])

        # Get Tiny Power Of
        # txn_group = [
        #     transaction.ApplicationNoOpTxn(
        #         sender=self.user_address,
        #         sp=self.sp,
        #         index=self.app_id,
        #         app_args=["get_tiny_power_of"],
        #         accounts=[self.user_address],
        #         boxes=[
        #             (0, decode_address(self.user_address)),
        #             (0, decode_address(self.user_address) + itob(3))
        #         ]
        #     )
        # ]
        #
        # transaction.assign_group_id(txn_group)
        # signed_txns = sign_txns(txn_group, self.user_sk)
        #
        # block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        # print("---TXN 6---", "Round", block[b'rnd'])
        # print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))
        #
        # block = self.ledger.eval_transactions(signed_txns, block_timestamp=int((lock_end_datetime - timedelta(days=7)).timestamp()))
        # print("---TXN 7---", "Round", block[b'rnd'])
        # print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))
        #
        # block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(lock_end_datetime.timestamp()))
        # print("---TXN 8---", "Round", block[b'rnd'])
        # print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))

        # Get Tiny Power Of At
        time = int((block_datetime + timedelta(days=7)).timestamp())
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_tiny_power_of_at", time, 3],
                accounts=[self.user_address],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(3))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 9---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_tiny_power_of_at", int((lock_end_datetime - timedelta(days=7)).timestamp()), 3],
                accounts=[self.user_address],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(3))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 10---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_tiny_power_of_at", int((lock_end_datetime - timedelta(days=7)).timestamp()), 2],
                accounts=[self.user_address],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(2))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        self.assertEqual(e.exception.source['line'], 'assert(account_power.end_time >= time)')

        today = get_start_time_of_day(int(block_datetime.timestamp()))
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_total_tiny_power"],
                boxes=[
                    (0, TOTAL_POWERS + itob(today)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 11---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]))

        time = int((block_datetime + timedelta(days=7)).timestamp())
        box_index = get_start_time_of_day(time)
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_total_tiny_power_at", time],
                boxes=[
                    (0, TOTAL_POWERS + itob(box_index)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 12---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]))

        account_state = parse_box_account_state(self.ledger.boxes[self.app_id][decode_address(self.user_address)])
        block_timestamp = account_state["lock_end_time"] + 10
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["withdraw"],
                foreign_assets=[self.tiny_asset_id],
                boxes=[
                    (0, decode_address(self.user_address)),
                ]
            )
        ]
        txn_group[0].fee *= 2

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=block_timestamp)
        print(block)
        print("---TXN 13---", "Round", block[b'rnd'])
        inner_txns = block[b'txns'][0][b'dt'][b'itx']
        self.assertEqual(len(inner_txns), 1)
        print(inner_txns[0][b'txn'])
        self.assertDictEqual(
            inner_txns[0][b'txn'],
            {
                b'aamt': 35000000,
                b'arcv': decode_address(self.user_address),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(get_application_address(self.app_id)),
                b'type': b'axfer',
                b'xaid': self.tiny_asset_id
            }
        )
        self.assertDictEqual(
            parse_box_account_state(self.ledger.boxes[self.app_id][decode_address(self.user_address)]),
            {'locked_amount': 0, 'lock_end_time': 0, 'first_index': ANY, 'last_index': ANY}
        )

        for i in range(1, 70):
            block_datetime += timedelta(days=1)
            lock_start_time = get_start_time_of_next_day(int(block_datetime.timestamp()))
            txn_group = [
                transaction.ApplicationNoOpTxn(
                    sender=self.user_2_address,
                    sp=self.sp,
                    index=self.app_id,
                    app_args=[
                        "create_checkpoint",
                    ],
                    boxes=[
                        (0, TOTAL_POWERS + itob(lock_start_time - DAY)),
                        (0, TOTAL_POWERS + itob(lock_start_time)),
                        (0, SLOPE_CHANGES + itob(lock_start_time))
                    ]
                )
            ]

            transaction.assign_group_id(txn_group)
            signed_txns = sign_txns(txn_group, self.user_2_sk)

            block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))

            print("---TXN End--- Checkpoint", "Index", i)
            print_boxes(self.ledger.boxes[self.app_id])
