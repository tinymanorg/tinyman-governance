import unittest
from datetime import datetime, timedelta
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import get_suggested_params, JigLedger, TealishProgram, LogicEvalError
from algosdk.account import generate_account
from algosdk.encoding import decode_address, encode_address
from algosdk.future import transaction
from algosdk.logic import get_application_address

from tests.utils import itob, btoi, sign_txns

WEEK = 604800
TOTAL_POWERS = b"total_powers"
SLOPE_CHANGES = b"slope_changes"

locking_approval_program = TealishProgram('contracts/locking/locking_approval.tl')
locking_clear_state_program = TealishProgram('contracts/locking/locking_clear_state.tl')


def parse_account_state_box(raw_box):
    return dict(
        locked_amount=btoi(raw_box[:8]),
        lock_end_time=btoi(raw_box[8:16]),
        first_index=btoi(raw_box[16:24]),
        last_index=btoi(raw_box[24:32]),
    )


def parse_account_power_box(raw_box):
    return dict(
        locked_amount=btoi(raw_box[:8]),
        locked_round=btoi(raw_box[8:16]),
        start_time=btoi(raw_box[16:24]),
        end_time=btoi(raw_box[24:32]),
        valid_until=btoi(raw_box[32:40]),
        delegatee=encode_address(raw_box[40:72]),
    )


def parse_total_power_box(raw_box):
    return dict(
        bias=btoi(raw_box[:8]),
        slope=btoi(raw_box[8:24]),
        cumulative_power=btoi(raw_box[24:40]),
    )


def parse_slope_change(raw_box):
    return dict(
        d_slope=btoi(raw_box[:16]),
    )


def print_boxes(boxes):
    for key, value in sorted(list(boxes.items()), key=lambda box: box[0]):
        if TOTAL_POWERS in key:
            print("TotalPower" + f"_{btoi(key[len(TOTAL_POWERS):])}", parse_total_power_box(value))
        elif SLOPE_CHANGES in key:
            print("SlopeChange" + f"_{btoi(key[len(SLOPE_CHANGES):])}", parse_slope_change(value))
        elif len(value) == 72:
            print(encode_address(key[:32]) + f"_{btoi(key[32:])}", parse_account_power_box(value))
        elif len(value) == 32:
            print(encode_address(key), parse_account_state_box(value))


def get_lock_end_time(value):
    return (value // WEEK) * WEEK


def get_lock_start_time(value):
    return ((value // WEEK) * WEEK) + WEEK


class DummyAlgod:
    def suggested_params(self):
        return get_suggested_params()


class BaseTestCase(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.sp = get_suggested_params()
        cls.tiny_asset_id = 12345
        cls.tiny_params = dict(
            total=100_000_000_000_000_000_000_000_000_000,
            decimals=6,
            name="Tinyman",
            unit_name="TINY",
        )
        cls.app_id = 9000
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_address = generate_account()
        cls.user_2_sk, cls.user_2_address = generate_account()

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.ledger.set_account_balance(self.user_address, 10_000_000)
        self.ledger.set_account_balance(self.user_2_address, 10_000_000)
        self.ledger.create_asset(self.tiny_asset_id, params=dict())
        self.create_app()

    def create_app(self):
        if self.app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

        self.ledger.create_app(
            app_id=self.app_id,
            approval_program=locking_approval_program,
            creator=self.app_creator_address,
            local_ints=0,
            local_bytes=0,
            global_ints=4,
            global_bytes=0
        )

        # 100_000 for basic min balance requirement
        self.ledger.set_account_balance(get_application_address(self.app_id), 1_000_000)
        # Opt-in
        self.ledger.set_account_balance(get_application_address(self.app_id), 0, asset_id=self.tiny_asset_id)
        self.ledger.set_global_state(
            self.app_id,
            {
                b'tiny_asset_id': self.tiny_asset_id,
                b'total_locked_amount': 0,
                b'first_index': 0,
                b'last_index': 0,
            }
        )

    def test_create_app(self):
        block_datetime = datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC"))
        lock_start_time = get_lock_start_time(int(block_datetime.timestamp()))

        txn_group = [
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=locking_approval_program.bytecode,
                clear_program=locking_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=4, num_byte_slices=0),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=1,
                foreign_assets=[self.tiny_asset_id],
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)

        # with self.assertRaises(LogicEvalError) as e:
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
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
            parse_total_power_box(self.ledger.boxes[app_id][TOTAL_POWERS + itob(lock_start_time)]),
            {'bias': 0, 'slope': 0, 'cumulative_power': 0}
        )

    def test_create_first_lock(self):
        amount = 10_000_000

        self.ledger.move(
            amount * 5,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        block_datetime = datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC"))
        lock_expiration_datetime = block_datetime + timedelta(days=50)
        lock_start_time = get_lock_start_time(int(block_datetime.timestamp()))
        lock_end_time = get_lock_end_time(int(lock_expiration_datetime.timestamp()))

        txn_group = [
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
            ),
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

        self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        # print_boxes(self.ledger.boxes[self.app_id])

        self.assertDictEqual(
            parse_account_state_box(self.ledger.boxes[self.app_id][decode_address(self.user_address)]),
            {'locked_amount': 10000000, 'lock_end_time': 1649894400, 'first_index': 0, 'last_index': 0}
        )
        self.assertDictEqual(
            parse_account_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(0)]),
            {'locked_amount': 10000000, 'locked_round': 2, 'start_time': 1646265600, 'end_time': 1649894400, 'valid_until': 0, 'delegatee': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ'}
        )
        self.assertDictEqual(
            parse_total_power_box(self.ledger.boxes[self.app_id][TOTAL_POWERS + itob(lock_start_time)]),
            {'bias': 287671, 'slope': 1462356043387680081, 'cumulative_power': 0}
        )
        self.assertDictEqual(
            parse_slope_change(self.ledger.boxes[self.app_id][SLOPE_CHANGES + itob(lock_end_time)]),
            {'d_slope': 1462356043387680081}
        )
        self.assertDictEqual(
            self.ledger.global_states[self.app_id],
            {b'first_index': 1646265600, b'last_index': 1646265600, b'tiny_asset_id': 12345, b'total_locked_amount': 10000000}
        )

        # create_lock fails if the user has a lock
        txn_group = txn_group[1:]
        for txn in txn_group:
            txn.group = None
        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        self.assertEqual(e.exception.source['line'], 'box<AccountState> account_state = CreateBox(user_address)')

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
        lock_start_time = get_lock_start_time(int(block_datetime.timestamp()))
        lock_end_time = get_lock_end_time(int(lock_end_datetime.timestamp()))

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
        block_datetime = block_datetime + timedelta(weeks=1)
        # lock_end_datetime = block_datetime + timedelta(days=100)
        lock_start_time = get_lock_start_time(int(block_datetime.timestamp()))
        lock_end_time = get_lock_end_time(int(lock_end_datetime.timestamp()))

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_2_address,
                sp=self.sp,
                index=self.app_id,
                app_args=[
                    "create_checkpoint",
                ],
                boxes=[
                    (0, TOTAL_POWERS + itob(lock_start_time - WEEK)),
                    (0, TOTAL_POWERS + itob(lock_start_time)),
                    (0, SLOPE_CHANGES + itob(lock_start_time))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_2_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))

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
        lock_start_time = get_lock_start_time(block_time)
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
        lock_start_time = get_lock_start_time(block_time)
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
        old_lock_end_time = get_lock_end_time(int(lock_end_datetime.timestamp()))
        lock_end_datetime = lock_end_datetime + timedelta(days=20)
        new_lock_end_time = get_lock_end_time(int(lock_end_datetime.timestamp()))
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
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_tiny_power_of"],
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
        print("---TXN 6---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int((lock_end_datetime - timedelta(days=7)).timestamp()))
        print("---TXN 7---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(lock_end_datetime.timestamp()))
        print("---TXN 8---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))

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

        this_week = get_lock_end_time(int(block_datetime.timestamp()))
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_total_tiny_power"],
                boxes=[
                    (0, TOTAL_POWERS + itob(this_week)),
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 11---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]))

        time = int((block_datetime + timedelta(days=7)).timestamp())
        box_index = get_lock_end_time(time)
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

        account_state = parse_account_state_box(self.ledger.boxes[self.app_id][decode_address(self.user_address)])
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
            parse_account_state_box(self.ledger.boxes[self.app_id][decode_address(self.user_address)]),
            {'locked_amount': 0, 'lock_end_time': 0, 'first_index': ANY, 'last_index': ANY}
        )

        for i in range(1, 10):
            block_datetime += timedelta(weeks=1)
            lock_start_time = get_lock_start_time(int(block_datetime.timestamp()))
            txn_group = [
                transaction.ApplicationNoOpTxn(
                    sender=self.user_2_address,
                    sp=self.sp,
                    index=self.app_id,
                    app_args=[
                        "create_checkpoint",
                    ],
                    boxes=[
                        (0, TOTAL_POWERS + itob(lock_start_time - WEEK)),
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