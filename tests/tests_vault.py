import unittest.mock
from datetime import timedelta, datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import WEEK, DAY
from tinyman.governance.event import decode_logs
from tinyman.governance.constants import TINY_ASSET_ID_KEY
from tinyman.governance.vault.constants import TOTAL_LOCKED_AMOUNT_KEY, TOTAL_POWER_COUNT_KEY, CREATION_TIMESTAMP_KEY, TOTAL_POWERS, SLOPE_CHANGES, TWO_TO_THE_64, MAX_LOCK_TIME, LAST_TOTAL_POWER_TIMESTAMP_KEY
from tinyman.governance.vault.events import vault_events
from tinyman.governance.vault.storage import parse_box_total_power, parse_box_account_state, parse_box_account_power, parse_box_slope_change, TotalPower, AccountState, AccountPower, SlopeChange
from tinyman.governance.vault.transactions import prepare_init_transactions, prepare_create_lock_transactions, prepare_withdraw_transactions, prepare_get_tiny_power_of_transactions, prepare_get_total_tiny_power_of_at_transactions, prepare_extend_lock_end_time_transactions, prepare_increase_lock_amount_transactions, prepare_get_tiny_power_of_at_transactions, prepare_get_total_tiny_power_transactions
from tinyman.governance.vault.utils import get_start_timestamp_of_week, get_slope, get_bias
from tinyman.utils import int_to_bytes, bytes_to_int

from common.constants import vault_approval_program, vault_clear_state_program, TINY_ASSET_ID, VAULT_APP_ID
from common.utils import sign_txns
from tests.common import BaseTestCase, VaultAppMixin
from vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at, get_all_total_powers, get_account_powers


class VaultTestCase(VaultAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_address = generate_account()
        cls.user_2_sk, cls.user_2_address = generate_account()
        cls.user_3_sk, cls.user_3_address = generate_account()

        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.ledger.set_account_balance(self.user_address, 100_000_000)
        self.ledger.set_account_balance(self.user_2_address, 100_000_000)
        self.ledger.set_account_balance(self.user_3_address, 100_000_000)

    def test_create_and_init_app(self):
        block_datetime = datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        txn_group = [
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=vault_approval_program.bytecode,
                clear_program=vault_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=5, num_byte_slices=0),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=1,
                app_args=[TINY_ASSET_ID],
            )
        ]

        transaction.assign_group_id(txn_group)
        signed_txns = sign_txns(txn_group, self.app_creator_sk)

        block = self.ledger.eval_transactions(signed_txns, block_timestamp=int(block_datetime.timestamp()))
        app_id = block[b"txns"][0][b"apid"]

        self.assertDictEqual(
            self.ledger.global_states[app_id],
            {
                TINY_ASSET_ID_KEY: TINY_ASSET_ID,
                TOTAL_LOCKED_AMOUNT_KEY: 0,
                TOTAL_POWER_COUNT_KEY: 0,
                CREATION_TIMESTAMP_KEY: ANY,
                LAST_TOTAL_POWER_TIMESTAMP_KEY: 0
            }
        )

        total_powers_box_name = TOTAL_POWERS + int_to_bytes(0)
        txn_group = prepare_init_transactions(
            vault_app_id=app_id,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            suggested_params=self.sp
        )

        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=int(block_datetime.timestamp()))
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
                b'xaid': TINY_ASSET_ID
            }
        )

        logs = block[b'txns'][1][b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'total_power',
                'index': 0,
                'bias': 0,
                'timestamp': block_timestamp,
                'slope': 0,
                'cumulative_power': 0
            }
        )

        total_powers = parse_box_total_power(self.ledger.boxes[app_id][total_powers_box_name])
        self.assertEqual(len(total_powers), 1)
        total_power = total_powers[0]
        self.assertEqual(
            total_power,
            TotalPower(
                bias=0,
                slope=0,
                cumulative_power=0,
                timestamp=block_timestamp
            )
        )

    def test_create_lock(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        amount = 20_000_000
        self.ledger.move(
            amount * 5,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))

        account_state_box_name = decode_address(self.user_address)
        total_power_box_name = TOTAL_POWERS + int_to_bytes(0)
        account_power_box_name = decode_address(self.user_address) + int_to_bytes(0)
        slope_change_box_name = SLOPE_CHANGES + int_to_bytes(lock_end_timestamp)

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # logs = block[b'txns'][2][b'dt'][b'lg']
        # events = decode_logs(logs, vault_events)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        self.assertEqual(
            parse_box_account_state(self.ledger.boxes[VAULT_APP_ID][account_state_box_name]),
            AccountState(
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                power_count=1,
            )
        )
        self.assertEqual(
            parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][account_power_box_name])[0],
            AccountPower(
                bias=bias,
                timestamp=block_timestamp,
                slope=amount * TWO_TO_THE_64 // MAX_LOCK_TIME,
                cumulative_power=0,
            )
        )
        self.assertEqual(
            parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][total_power_box_name])[1],
            TotalPower(
                bias=bias,
                timestamp=block_timestamp,
                slope=slope,
                cumulative_power=0
            )
        )
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][slope_change_box_name]),
            SlopeChange(
                slope_delta=slope
            )
        )
        self.assertDictEqual(
            self.ledger.global_states[VAULT_APP_ID],
            {
                TOTAL_POWER_COUNT_KEY: 2,
                TINY_ASSET_ID_KEY: TINY_ASSET_ID,
                TOTAL_LOCKED_AMOUNT_KEY: amount,
                CREATION_TIMESTAMP_KEY: ANY,
                LAST_TOTAL_POWER_TIMESTAMP_KEY: block_timestamp
            }
        )

    def test_create_lock_multiple(self):
        # 1. User 1 create lock, end datetime A
        # 2. User 2 create lock, end datetime A
        # 3. User 3 create lock, end datetime B

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # User 1
        amount_1 = 200_000_000
        self.ledger.move(
            amount_1,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        # User 2
        amount_2 = 10_000_000
        self.ledger.move(
            amount_2,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_2_address
        )

        # User 3
        amount_3 = 500_000_000
        self.ledger.move(
            amount_3,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
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

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount_1,
                lock_end_time=lock_end_timestamp_1,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp_1),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_2_address,
                locked_amount=amount_2,
                lock_end_time=lock_end_timestamp_1,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_2_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp_1),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_2_address, self.user_2_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_3_address,
                locked_amount=amount_3,
                lock_end_time=lock_end_timestamp_2,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_3_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp_2),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_3_address, self.user_3_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        total_power_box_name = TOTAL_POWERS + int_to_bytes(0)
        slope_change_box_name_1 = SLOPE_CHANGES + int_to_bytes(lock_end_timestamp_1)
        slope_change_box_name_2 = SLOPE_CHANGES + int_to_bytes(lock_end_timestamp_2)

        total_powers = parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][total_power_box_name])
        self.assertEqual(len(total_powers), 4)
        self.assertEqual(
            parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][total_power_box_name])[3],
            TotalPower(
                bias=bias_1 + bias_2 + bias_3,
                timestamp=block_timestamp,
                slope=slope_1 + slope_2 + slope_3,
                cumulative_power=0
            )
        )
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][slope_change_box_name_1]),
            SlopeChange(slope_delta=slope_1 + slope_2)
        )
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][slope_change_box_name_2]),
            SlopeChange(slope_delta=slope_3)
        )
        self.assertDictEqual(
            self.ledger.global_states[VAULT_APP_ID],
            {
                TOTAL_POWER_COUNT_KEY: 4,
                TINY_ASSET_ID_KEY: TINY_ASSET_ID,
                TOTAL_LOCKED_AMOUNT_KEY: amount_1 + amount_2 + amount_3,
                CREATION_TIMESTAMP_KEY: ANY,
                LAST_TOTAL_POWER_TIMESTAMP_KEY: block_timestamp
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

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        # Create checkpoints
        block_timestamp = lock_end_timestamp + 1
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        # Withdraw
        txn_group = prepare_withdraw_transactions(
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            account_state=get_account_state(self.ledger, self.user_address),
            suggested_params=self.sp,
            app_call_note=None,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], 0)

        # Create lock
        lock_end_timestamp = lock_end_timestamp + 5 * WEEK
        amount = 10_000_000

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )

        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

    def test_withdraw(self):
        # 1. Try to withdraw at lock end time
        # 2. Withdraw after the lock end time
        # 3. Try to withdraw second time

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)
        # print_boxes(self.ledger.boxes[VAULT_APP_ID])

        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=45)).timestamp()))
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )

        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)
        # print_boxes(self.ledger.boxes[VAULT_APP_ID])

        # Withdraw
        txn_group = prepare_withdraw_transactions(
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            account_state=get_account_state(self.ledger, self.user_address),
            suggested_params=self.sp,
            app_call_note=None,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_end_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(account_state.lock_end_time < Global.LatestTimestamp)')

        block_timestamp = lock_end_timestamp + 1
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

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
                b'snd': decode_address(get_application_address(VAULT_APP_ID)),
                b'type': b'axfer',
                b'xaid': TINY_ASSET_ID
            }
        )

        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], 0)
        self.assertEqual(
            parse_box_account_state(self.ledger.boxes[VAULT_APP_ID][decode_address(self.user_address)]),
            AccountState(
                locked_amount=0,
                lock_end_time=0,
                power_count=2,
            )
        )

        # Try to withdraw again
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(e.exception.source['line'], 'assert(locked_amount)')

        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

    def test_increase_lock_amount(self):
        # 1. Create lock
        # 2. Increase lock amount
        # 3. Create checkpoints
        # 4. Increase lock amount

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        # Increase
        block_timestamp = block_timestamp + DAY // 2
        amount = 30_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_increase_lock_amount_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                suggested_params=self.sp,
                app_call_note=None,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create checkpoints
        block_timestamp = block_timestamp + 3 * DAY
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        # Increase
        block_timestamp = block_timestamp + DAY // 2
        amount = 40_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_increase_lock_amount_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                suggested_params=self.sp,
                app_call_note=None,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

    def test_extend_lock_end_time(self):
        # 1. Create lock
        # 2. Extend 2 weeks
        # 3. Create checkpoints
        # 4. Extend 4 weeks

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        slope = get_slope(amount)
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][SLOPE_CHANGES + int_to_bytes(lock_end_timestamp)]),
            SlopeChange(slope_delta=slope)
        )

        # Extend 2 weeks
        block_timestamp = block_timestamp + DAY // 2
        old_lock_end_timestamp = lock_end_timestamp
        new_lock_end_timestamp = lock_end_timestamp + 5 * WEEK
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_extend_lock_end_time_transactions(
                vault_app_id=VAULT_APP_ID,
                sender=self.user_address,
                new_lock_end_time=new_lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_new_lock_end_time=get_slope_change_at(self.ledger, new_lock_end_timestamp),
                suggested_params=self.sp,
                app_call_note=None,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][SLOPE_CHANGES + int_to_bytes(old_lock_end_timestamp)]),
            SlopeChange(slope_delta=0)
        )
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][SLOPE_CHANGES + int_to_bytes(new_lock_end_timestamp)]),
            SlopeChange(slope_delta=slope)
        )

        lock_end_timestamp = new_lock_end_timestamp

        # Create checkpoints
        block_timestamp = block_timestamp + 3 * DAY
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        # Extend 4 weeks
        block_timestamp = block_timestamp + DAY // 2
        old_lock_end_timestamp = lock_end_timestamp
        new_lock_end_timestamp = lock_end_timestamp + 4 * WEEK
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_extend_lock_end_time_transactions(
                vault_app_id=VAULT_APP_ID,
                sender=self.user_address,
                new_lock_end_time=new_lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_new_lock_end_time=get_slope_change_at(self.ledger, new_lock_end_timestamp),
                suggested_params=self.sp,
                app_call_note=None,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][SLOPE_CHANGES + int_to_bytes(old_lock_end_timestamp)]),
            SlopeChange(slope_delta=0)
        )
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][SLOPE_CHANGES + int_to_bytes(new_lock_end_timestamp)]),
            SlopeChange(slope_delta=slope)
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

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        txn_group = prepare_get_tiny_power_of_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        txn_group = prepare_get_tiny_power_of_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)

        block_timestamp += DAY
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)

        block_timestamp += WEEK
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)

        block_timestamp = lock_end_timestamp
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        block_timestamp = lock_end_timestamp + 1
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        txn_group = prepare_withdraw_transactions(
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            account_state=get_account_state(self.ledger, self.user_address),
            suggested_params=self.sp,
            app_call_note=None,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        txn_group = prepare_get_tiny_power_of_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

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

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        power_at_timestamp = block_timestamp - DAY
        txn_group = prepare_get_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=power_at_timestamp,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        power_at_timestamp = block_timestamp
        txn_group = prepare_get_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=power_at_timestamp,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        power_at_timestamp = block_timestamp
        block_timestamp = lock_end_timestamp + DAY

        txn_group = prepare_get_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=power_at_timestamp,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)

        txn_group = prepare_get_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=power_at_timestamp,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)

        power_at_timestamp += DAY
        bias_delta = get_bias(slope, power_at_timestamp - lock_start_timestamp)
        txn_group = prepare_get_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=power_at_timestamp,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias - bias_delta)

        power_at_timestamp += WEEK
        bias_delta = get_bias(slope, power_at_timestamp - lock_start_timestamp)
        txn_group = prepare_get_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=power_at_timestamp,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias - bias_delta)

        power_at_timestamp = lock_end_timestamp
        txn_group = prepare_get_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=power_at_timestamp,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        block_timestamp = lock_end_timestamp + 1
        txn_group = prepare_get_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=power_at_timestamp,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

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

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        txn_group = prepare_get_total_tiny_power_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        txn_group = prepare_get_total_tiny_power_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)

        block_timestamp += DAY
        bias_delta = get_bias(slope, block_timestamp - lock_start_timestamp)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertAlmostEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias - bias_delta, delta=(block_timestamp - lock_start_timestamp) // DAY)

        block_timestamp += WEEK
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)
        bias_delta = get_bias(slope, block_timestamp - lock_start_timestamp)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertAlmostEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias - bias_delta, delta=(block_timestamp - lock_start_timestamp) // DAY)

        block_timestamp = lock_end_timestamp
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        block_timestamp = lock_end_timestamp + 1
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        # Withdraw
        txn_group = prepare_withdraw_transactions(
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            account_state=get_account_state(self.ledger, self.user_address),
            suggested_params=self.sp,
            app_call_note=None,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        txn_group = prepare_get_total_tiny_power_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

    def test_get_total_tiny_power_at(self):
        # 1. User 1 create lock, end datetime A
        # 2. User 2 create lock, end datetime A
        # 3. User 3 create lock, end datetime B

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        txn_group = prepare_get_total_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            timestamp=block_timestamp - DAY,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        # User 1
        amount_1 = 200_000_000
        self.ledger.move(
            amount_1,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        # User 2
        amount_2 = 10_000_000
        self.ledger.move(
            amount_2,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_2_address
        )

        # User 3
        amount_3 = 500_000_000
        self.ledger.move(
            amount_3,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_3_address
        )

        lock_end_timestamp_1 = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))
        lock_end_timestamp_2 = lock_end_timestamp_1 + WEEK

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount_1,
                lock_end_time=lock_end_timestamp_1,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp_1),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_2_address,
                locked_amount=amount_2,
                lock_end_time=lock_end_timestamp_1,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_2_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp_1),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_2_address, self.user_2_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_3_address,
                locked_amount=amount_3,
                lock_end_time=lock_end_timestamp_2,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_3_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp_2),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_3_address, self.user_3_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        block_timestamp = lock_end_timestamp_2 + DAY
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        power_at = lock_end_timestamp_1
        txn_group = prepare_get_total_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            timestamp=power_at,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 2397263)

        power_at += DAY
        txn_group = prepare_get_total_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            timestamp=power_at,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 2054798)

        power_at = lock_end_timestamp_2
        txn_group = prepare_get_total_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            timestamp=power_at,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        power_at += 1
        txn_group = prepare_get_total_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            timestamp=power_at,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

    def test_multiple_increase_lock_amount(self):
        # 1. Create lock
        # 2. Increase lock amount 50x

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        increase_count = 50
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount * (increase_count + 1),
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        # Increase
        for i in range(increase_count):
            block_timestamp = block_timestamp + DAY // 2
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                txn_group = prepare_increase_lock_amount_transactions(
                    vault_app_id=VAULT_APP_ID,
                    tiny_asset_id=TINY_ASSET_ID,
                    sender=self.user_address,
                    locked_amount=amount,
                    vault_app_global_state=get_vault_app_global_state(self.ledger),
                    account_state=get_account_state(self.ledger, self.user_address),
                    suggested_params=self.sp,
                    app_call_note=None,
                )
            txn_group.sign_with_private_key(self.user_address, self.user_sk)
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount * (i + 2))

    def test_multiple_extend_lock_end_time(self):
        # 1. Create lock
        # 2. Extend 50x

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        increase_count = 50
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        # Extend
        for i in range(increase_count):
            block_timestamp = block_timestamp + DAY // 2
            new_lock_end_timestamp = lock_end_timestamp + 4 * WEEK
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                txn_group = prepare_extend_lock_end_time_transactions(
                    vault_app_id=VAULT_APP_ID,
                    sender=self.user_address,
                    new_lock_end_time=new_lock_end_timestamp,
                    vault_app_global_state=get_vault_app_global_state(self.ledger),
                    account_state=get_account_state(self.ledger, self.user_address),
                    slope_change_at_new_lock_end_time=get_slope_change_at(self.ledger, new_lock_end_timestamp),
                    suggested_params=self.sp,
                    app_call_note=None,
                )

            txn_group.sign_with_private_key(self.user_address, self.user_sk)
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            lock_end_timestamp = new_lock_end_timestamp

    @unittest.skip
    def test_create_checkpoints(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address, self.vault_app_creation_timestamp)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 200 * WEEK
        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        # Create checkpoints
        # block_timestamp += 10 * WEEK

        for i in range(1, 10):
            print("w", i)
            block_timestamp = get_start_timestamp_of_week(block_timestamp) + i * WEEK
            self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        # block_timestamp = get_start_timestamp_of_week(block_timestamp) + 2 * WEEK
        # self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)
        #
        # block_timestamp = get_start_timestamp_of_week(block_timestamp) + 3 * WEEK
        # self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)
        #
        # block_timestamp = get_start_timestamp_of_week(block_timestamp) + 4 * WEEK
        # self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)
        #
        # block_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK
        # self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)
        #
        # block_timestamp = get_start_timestamp_of_week(block_timestamp) + 6 * WEEK
        # self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)
