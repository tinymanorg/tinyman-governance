import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from algojig import get_suggested_params, JigLedger, TealishProgram, LogicEvalError
from algosdk.account import generate_account
from algosdk.encoding import decode_address, encode_address
from algosdk.future import transaction
from algosdk.logic import get_application_address

from tests.utils import itob, btoi, sign_txns

amm_approval_program = TealishProgram('contracts/locking/locking_approval.tl')


def parse_user_state_box(raw_box):
    return dict(
        locked_amount=btoi(raw_box[:8]),
        lock_expiration_time=btoi(raw_box[8:16]),
        first_index=btoi(raw_box[16:24]),
        last_index=btoi(raw_box[24:32]),
    )


def parse_user_power_box(raw_box):
    return dict(
        locked_amount=btoi(raw_box[:8]),
        start_round=btoi(raw_box[8:16]),
        start_time=btoi(raw_box[16:24]),
        end_round=btoi(raw_box[24:32]),
        end_time=btoi(raw_box[32:40]),
        expiration_time=btoi(raw_box[40:48]),
        delegatee=encode_address(raw_box[48:80]),
    )

# struct UserPower:
#     locked_amount: int
#     start_round: int
#     start_time: int
#     end_round: int
#     end_time: int
#     expiration_time: int
#     delegatee: bytes[32]
# end


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

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.ledger.set_account_balance(self.user_address, 10_000_000)
        self.ledger.create_asset(self.tiny_asset_id, params=dict())
        self.create_app()

    def create_app(self):
        if self.app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

        self.ledger.create_app(
            app_id=self.app_id,
            approval_program=amm_approval_program,
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
            }
        )

    def test(self):
        self.ledger.move(
            1_000_000_000,
            asset_id=self.tiny_asset_id,
            sender=self.ledger.assets[self.tiny_asset_id]["creator"],
            receiver=self.user_address
        )

        block_datetime = datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC"))
        lock_expiration_time = block_datetime + timedelta(days=10)

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
                app_args=["create_lock", int(lock_expiration_time.timestamp())],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(0))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))

        print("---TXN 1---", "Round", block[b'rnd'])
        user_state_box = parse_user_state_box(self.ledger.boxes[self.app_id][decode_address(self.user_address)])
        print(user_state_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(0)])
        print(user_power_box)

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
                    (0, decode_address(self.user_address) + itob(1))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 2---", "Round", block[b'rnd'])
        user_state_box = parse_user_state_box(self.ledger.boxes[self.app_id][decode_address(self.user_address)])
        print(user_state_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(0)])
        print(user_power_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(1)])
        print(user_power_box)

        # Increase again
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
                    (0, decode_address(self.user_address) + itob(2))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 3---", "Round", block[b'rnd'])
        user_state_box = parse_user_state_box(self.ledger.boxes[self.app_id][decode_address(self.user_address)])
        print(user_state_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(0)])
        print(user_power_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(1)])
        print(user_power_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(2)])
        print(user_power_box)

        # Extend time
        lock_expiration_time = block_datetime + timedelta(days=20)
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
                app_args=["extend_lock_expiration_time", int(lock_expiration_time.timestamp())],
                boxes=[
                    (0, decode_address(self.user_address)),
                    (0, decode_address(self.user_address) + itob(2)),
                    (0, decode_address(self.user_address) + itob(3))
                ]
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        print("---TXN 4---", "Round", block[b'rnd'])
        user_state_box = parse_user_state_box(self.ledger.boxes[self.app_id][decode_address(self.user_address)])
        print(user_state_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(0)])
        print(user_power_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(1)])
        print(user_power_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(2)])
        print(user_power_box)
        user_power_box = parse_user_power_box(self.ledger.boxes[self.app_id][decode_address(self.user_address) + itob(3)])
        print(user_power_box)

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

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int((lock_expiration_time - timedelta(days=7)).timestamp()))
        print("---TXN 7---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(lock_expiration_time.timestamp()))
        print("---TXN 8---", "Round", block[b'rnd'])
        print("Power", btoi(block[b'txns'][0][b'dt'][b'lg'][0]), encode_address(block[b'txns'][0][b'dt'][b'lg'][1]))

        # Get Tiny Power Of At
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=self.sp,
                index=self.app_id,
                app_args=["get_tiny_power_of_at", int(block_datetime.timestamp()), 3],
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
                app_args=["get_tiny_power_of_at", int((lock_expiration_time - timedelta(days=7)).timestamp()), 3],
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
                app_args=["get_tiny_power_of_at", int((lock_expiration_time - timedelta(days=7)).timestamp()), 2],
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
        self.assertEqual(e.exception.source['line'], 'assert(user_power.expiration_time >= time)')
