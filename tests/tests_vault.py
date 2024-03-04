import unittest.mock
from datetime import timedelta, datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import TINY_ASSET_ID_KEY
from tinyman.governance.constants import WEEK, DAY
from tinyman.governance.event import decode_logs
from tinyman.governance.transactions import _prepare_budget_increase_transaction
from tinyman.governance.vault.constants import TOTAL_LOCKED_AMOUNT_KEY, TOTAL_POWER_COUNT_KEY, TWO_TO_THE_64, MAX_LOCK_TIME, LAST_TOTAL_POWER_TIMESTAMP_KEY, ACCOUNT_POWER_BOX_COST, ACCOUNT_STATE_BOX_COST
from tinyman.governance.vault.events import vault_events
from tinyman.governance.vault.storage import VaultAppGlobalState, get_power_index_at
from tinyman.governance.vault.storage import parse_box_total_power, parse_box_account_state, parse_box_account_power, parse_box_slope_change, TotalPower, AccountState, AccountPower, SlopeChange, get_account_state_box_name, get_account_power_box_name, get_total_power_box_name, get_slope_change_box_name
from tinyman.governance.vault.utils import get_start_timestamp_of_week, get_slope, get_bias, get_cumulative_power_delta
from tinyman.governance.vault.transactions import prepare_init_transactions, prepare_create_lock_transactions, prepare_withdraw_transactions, prepare_get_cumulative_power_of_at_transactions, prepare_get_total_cumulative_power_at_transactions, prepare_get_tiny_power_of_transactions, prepare_get_total_tiny_power_of_at_transactions, prepare_extend_lock_end_time_transactions, prepare_increase_lock_amount_transactions, prepare_get_tiny_power_of_at_transactions, prepare_get_total_tiny_power_transactions, prepare_delete_account_state_transactions, prepare_delete_account_power_boxes_transactions, prepare_create_checkpoints_transactions, prepare_get_box_transaction
from tinyman.utils import bytes_to_int, TransactionGroup

from tests.common import BaseTestCase, VaultAppMixin
from tests.constants import vault_approval_program, vault_clear_state_program, TINY_ASSET_ID, VAULT_APP_ID
from tests.utils import get_first_app_call_txn
from tests.vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at, get_all_total_powers, get_account_powers


class VaultTestCase(VaultAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
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

    def test_create_app(self):
        block_datetime = datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        
        txn_group = TransactionGroup([
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=vault_approval_program.bytecode,
                clear_program=vault_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=4, num_byte_slices=0),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=3,
                app_args=["create_application", TINY_ASSET_ID],
            )
        ])
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_id = block[b"txns"][0][b"apid"]

        # Global state
        vault_app_global_state = get_vault_app_global_state(self.ledger, app_id)
        self.assertEqual(
            vault_app_global_state,
            VaultAppGlobalState(
                tiny_asset_id=TINY_ASSET_ID,
                total_locked_amount=0,
                total_power_count=0,
                last_total_power_timestamp=0
            )
        )

    def test_init_app(self):
        self.create_vault_app(self.app_creator_address)

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        
        # Init app
        txn_group = prepare_init_transactions(
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=int(block_datetime.timestamp()))
        
        # Global state
        vault_app_global_state = get_vault_app_global_state(self.ledger, VAULT_APP_ID)
        self.assertEqual(vault_app_global_state.total_power_count, 1)
        self.assertEqual(vault_app_global_state.last_total_power_timestamp, block_timestamp)
        
        # Opt-in TINY
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        opt_in_inner_txn = app_call_txn[b'dt'][b'itx'][0][b'txn']
        self.assertDictEqual(
            opt_in_inner_txn,
            {
                b'arcv': decode_address(get_application_address(VAULT_APP_ID)),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(get_application_address(VAULT_APP_ID)),
                b'type': b'axfer',
                b'xaid': TINY_ASSET_ID
            }
        )
        
        # Assert Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)

        self.assertEqual(len(events), 2)
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
        self.assertDictEqual(
            events[1],
            {
                'event_name': 'init',
            }
        )

        # Assert Boxes
        total_powers = parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=0)])
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

        # Calling init again more than one time should fail.
        txn_group = prepare_init_transactions(
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            suggested_params=self.sp
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        with self.assertRaises(Exception):
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=int(block_datetime.timestamp()))

    def test_budget_increase(self):
        self.create_vault_app(self.app_creator_address)

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        self.init_vault_app(timestamp=block_timestamp)
        block_timestamp =+ 10

        txn_group = TransactionGroup(
            [
                _prepare_budget_increase_transaction(
                    sender=self.user_address,
                    sp=self.sp,
                    index=VAULT_APP_ID,
                )
            ]
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

    def test_create_lock(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Move lock amount to user_address.
        amount = 20_000_000

        self.ledger.move(
            amount * 5,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))

        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - lock_start_timestamp))

        app_call_txn = get_first_app_call_txn(block[b'txns'])

        # Assert Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, vault_events)

        self.assertEqual(len(events), 4)
        self.assertDictEqual(
            events[0],
            {
                'event_name': 'account_power',
                'user_address': self.user_address,
                'index': 0,
                'bias': bias,
                'timestamp': lock_start_timestamp,
                'slope': slope,
                'cumulative_power': 0
            }
        )
        self.assertDictEqual(
            events[1],
            {
                'event_name': 'total_power',
                'index': 1,
                'bias': bias,
                'timestamp': lock_start_timestamp,
                'slope': slope,
                'cumulative_power': 0
            }
        )
        self.assertDictEqual(
            events[2],
            {
                'event_name': 'slope_change',
                'timestamp': lock_end_timestamp,
                'slope': slope
            }
        )
        self.assertDictEqual(
            events[3],
            {
                'event_name': 'create_lock',
                'user_address': self.user_address,
                'locked_amount': amount,
                'lock_end_time': lock_end_timestamp
            }
        )
        
        # Assert Boxes
        self.assertEqual(
            parse_box_account_state(self.ledger.boxes[VAULT_APP_ID][get_account_state_box_name(address=self.user_address)]),
            AccountState(
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                power_count=1,
                deleted_power_count=0,
            )
        )
        self.assertEqual(
            parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][get_account_power_box_name(address=self.user_address, box_index=0)])[0],
            AccountPower(
                bias=bias,
                timestamp=lock_start_timestamp,
                slope=slope,
                cumulative_power=0,
            )
        )
        self.assertEqual(
            parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=0)])[1],
            TotalPower(
                bias=bias,
                timestamp=lock_start_timestamp,
                slope=slope,
                cumulative_power=0
            )
        )
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=lock_end_timestamp)]),
            SlopeChange(
                slope_delta=slope
            )
        )
        
        # Global state
        vault_app_global_state = get_vault_app_global_state(self.ledger, VAULT_APP_ID)

        self.assertEqual(vault_app_global_state.total_power_count, 2)
        self.assertEqual(vault_app_global_state.total_locked_amount, amount)

        # Try to create lock before first one previous lock ends.
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp + 1):
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
        with self.assertRaises(LogicEvalError):
            self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp + 1)

    def test_create_lock_multiple(self):
        # 1. User 1 create lock, end datetime A
        # 2. User 2 create lock, end datetime A
        # 3. User 3 create lock, end datetime B

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
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

        lock_start_timestamp = block_timestamp
        lock_end_timestamp_1 = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))
        lock_end_timestamp_2 = lock_end_timestamp_1 + WEEK

        # User 1
        slope_1 = get_slope(amount_1)
        bias_1 = get_bias(slope_1, (lock_end_timestamp_1 - lock_start_timestamp))

        # User 2
        slope_2 = get_slope(amount_2)
        bias_2 = get_bias(slope_2, (lock_end_timestamp_1 - lock_start_timestamp))

        #  User 3
        slope_3 = get_slope(amount_3)
        bias_3 = get_bias(slope_3, (lock_end_timestamp_2 - lock_start_timestamp))

        # 1. User 1 create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        # 2. User 2 create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        # 3. User 3 create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        total_power_box_name = get_total_power_box_name(box_index=0)
        slope_change_box_name_1 = get_slope_change_box_name(timestamp=lock_end_timestamp_1)
        slope_change_box_name_2 = get_slope_change_box_name(timestamp=lock_end_timestamp_2)

        # Assert Boxes
        total_powers = parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][total_power_box_name])
        self.assertEqual(len(total_powers), 4)
        # Assert that total power bias and slope is the sum of all locks.
        self.assertEqual(
            parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][total_power_box_name])[3],
            TotalPower(
                bias=bias_1 + bias_2 + bias_3,
                timestamp=lock_start_timestamp,
                slope=slope_1 + slope_2 + slope_3,
                cumulative_power=0
            )
        )

        # Assert that at the end of locks for user 1 and user 2, the slope change is the sum of their slopes.
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][slope_change_box_name_1]),
            SlopeChange(slope_delta=slope_1 + slope_2)
        )

        # Assert that at the end of lock for user 3, the slope change is the slope of user 3.
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][slope_change_box_name_2]),
            SlopeChange(slope_delta=slope_3)
        )

        self.assertDictEqual(
            self.ledger.global_states[VAULT_APP_ID],
            {
                TOTAL_POWER_COUNT_KEY: 4,   # 1 for init, 1 for user 1, 1 for user 2, 1 for user 3
                TINY_ASSET_ID_KEY: TINY_ASSET_ID,
                TOTAL_LOCKED_AMOUNT_KEY: amount_1 + amount_2 + amount_3,    # Total locked amount is the sum of all locks.
                LAST_TOTAL_POWER_TIMESTAMP_KEY: lock_start_timestamp    # All user locks start with the same timestamp, thus the last total power timestamp is the same as the lock start timestamp.
            }
        )

    def test_create_lock_after_withdraw(self):
        # 1. Create lock
        # 2. Create checkpoints
        # 3. Withdraw
        # 4. Create lock again

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK

        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        # 1. Create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        # 2. Create checkpoints
        block_timestamp = lock_end_timestamp + 1
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        # 3. Withdraw
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

        # Set the second lock.
        lock_start_timestamp = block_timestamp
        lock_end_timestamp = lock_end_timestamp + 5 * WEEK
        amount = 10_000_000

        # 4. Create lock again
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        # Assert Boxes
        account_state_box_name = get_account_state_box_name(address=self.user_address)
        account_power_box_name = get_account_power_box_name(address=self.user_address, box_index=0)

        self.assertEqual(
            parse_box_account_state(self.ledger.boxes[VAULT_APP_ID][account_state_box_name]),
            AccountState(
                locked_amount=amount,
                lock_end_time=lock_end_timestamp,
                power_count=3,
                deleted_power_count=0,
            )
        )
        account_powers = parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][account_power_box_name])
        self.assertEqual(len(account_powers), 3)    # 1 for lock 1, 1 for withdraw, 1 for lock 2
        self.assertEqual(account_powers[1].cumulative_power, account_powers[2].cumulative_power)    # Assert that the cumulative power from the first lock is preserved in second lock.

    def test_revert_slope_change(self):
        # 1. Create lock
        # 2. Increase lock amount
        # 3. Extend lock end datetime

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)
        
        amount = 1_000_000_000
        self.ledger.move(
            amount * 2,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        # Make sure slope changes are handled properly.
        # bytes slope_delta = new_locked_amount_slope b- old_locked_amount_slope
        self.assertTrue(get_slope(amount) * 2 < get_slope(amount * 2))

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=50)).timestamp()))

        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        # Assert that slope delta is equal to user's slope.
        self.assertEqual(parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=lock_end_timestamp)]).slope_delta, get_slope(amount))
        
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        # Assert that slope delta is updated according to the new locked amount.
        self.assertEqual(parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=lock_end_timestamp)]).slope_delta, get_slope(2 * amount))

        new_lock_end_timestamp = lock_end_timestamp + WEEK * 4
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

        # Make sure slope changes are handled properly.
        # bytes slope_delta = new_locked_amount_slope b- old_locked_amount_slope
        self.assertEqual(parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=lock_end_timestamp)]).slope_delta, 0)
        self.assertEqual(parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=new_lock_end_timestamp)]).slope_delta, get_slope(2 * amount))

    def test_withdraw(self):
        # 1. Try to withdraw at lock end time
        # 2. Withdraw after the lock end time
        # 3. Try to withdraw second time

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(int((block_datetime + timedelta(days=45)).timestamp()))

        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        # Create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)

        # 1. Try to withdraw at lock end time
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
        
        time_delta = lock_end_timestamp - lock_start_timestamp
        cumulative_power = get_cumulative_power_delta(bias=get_bias(get_slope(amount), time_delta), slope=get_slope(amount), time_delta=time_delta)

        block_timestamp = lock_end_timestamp + 1
        # 2. Withdraw after the lock end time
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        app_call_txn = get_first_app_call_txn(block[b'txns'])

        # Global state
        vault_app_global_state = get_vault_app_global_state(self.ledger, VAULT_APP_ID)
        self.assertEqual(vault_app_global_state.total_locked_amount, 0)   # Locked amount is 0 after the withdraw.

        # Assert Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 2)
        # Assert that the cumulative power is preserved and bias and slope are 0.
        self.assertEqual(
            events[0],
            {
                'event_name': 'account_power',
                'user_address': self.user_address,
                'index': ANY,
                'bias': 0,
                'timestamp': lock_end_timestamp,
                'slope': 0,
                'cumulative_power': cumulative_power
            }
        )
        self.assertEqual(
            events[1],
            {
                'event_name': 'withdraw',
                'user_address': self.user_address,
                'amount': amount
            }
        )

        # Inner Txn
        # Assert that the locked tiny asset is transferred back to user.
        inner_txns = app_call_txn[b'dt'][b'itx']
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
                power_count=2,  # 1 for lock, 1 for withdraw
                deleted_power_count=0,
            )
        )
        account_powers = get_account_powers(self.ledger, self.user_address)
        account_power = account_powers[-1]
        self.assertEqual(
            account_power,
            AccountPower(
                bias=0,
                timestamp=lock_end_timestamp,
                slope=0,
                cumulative_power=cumulative_power,
            )
        )

        # Withdraw again should fail.
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

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK

        amount_1 = 20_000_000
        self.ledger.move(
            amount_1,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        # 1. Create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount_1,
                lock_end_time=lock_end_timestamp,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount_1)

        # 2. Increase lock amount
        increase_lock_timestamp = lock_start_timestamp + DAY // 2
        amount_2 = 30_000_000
        self.ledger.move(
            amount_2,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )
        with unittest.mock.patch("time.time", return_value=increase_lock_timestamp):
            txn_group = prepare_increase_lock_amount_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_address,
                locked_amount=amount_2,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, self.user_address),
                suggested_params=self.sp,
                app_call_note=None,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=increase_lock_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])

        account_state_after_increase = parse_box_account_state(self.ledger.boxes[VAULT_APP_ID][get_account_state_box_name(address=self.user_address)])
        account_powers = parse_box_account_power(self.ledger.boxes[VAULT_APP_ID][get_account_power_box_name(address=self.user_address, box_index=0)])
        total_powers = parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=0)])

        # Assert Boxes
        slope = get_slope(amount_1 + amount_2)
        bias = get_bias(slope, lock_end_timestamp - increase_lock_timestamp)

        # Assert that the account state is updated properly.
        self.assertEqual(amount_1 + amount_2, account_state_after_increase.locked_amount)

        slope_at_lock = get_slope(amount_1)
        bias_at_lock = get_bias(slope_at_lock, lock_end_timestamp - lock_start_timestamp)
        bias_just_before_increase = get_bias(slope_at_lock, lock_end_timestamp - increase_lock_timestamp)

        # Assert that the account power is created properly.
        account_power_after_increase = account_powers[1]

        self.assertEqual(account_power_after_increase.bias, bias)
        self.assertEqual(account_power_after_increase.slope, slope)
        # self.assertEqual(account_power_after_increase.cumulative_power, get_cumulative_power_delta(bias_at_lock, slope_at_lock, account_power_after_increase.timestamp - lock_start_timestamp))
        self.assertEqual(account_power_after_increase.cumulative_power, ((bias_at_lock + bias_just_before_increase) * (increase_lock_timestamp - lock_start_timestamp) // 2))

        # Assert that the total power is created properly.
        total_power_after_increase = total_powers[2]
        self.assertEqual(total_power_after_increase.bias, account_power_after_increase.bias)
        self.assertEqual(total_power_after_increase.slope, slope)
        self.assertEqual(total_power_after_increase.cumulative_power, account_power_after_increase.cumulative_power)

        # Assert Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 4)

        self.assertEqual(
            events[0],
            {
                'event_name': 'account_power',
                'user_address': self.user_address,
                'index': 1,
                'bias': bias,
                'timestamp': increase_lock_timestamp,
                'slope': slope,
                'cumulative_power': ANY
            }
        )
        self.assertEqual(
            events[1],
            {
                'event_name': 'total_power',
                'index': 2,
                'bias': bias + 1,
                'timestamp': increase_lock_timestamp,
                'slope': slope,
                'cumulative_power': ANY
            }
        )
        self.assertEqual(
            events[2],
            {
                'event_name': 'slope_change',
                'timestamp': lock_end_timestamp,
                'slope': slope
            }
        )
        self.assertEqual(
            events[3],
            {
                'event_name': 'increase_lock_amount',
                'user_address': self.user_address,
                'locked_amount': amount_1 + amount_2,
                'lock_end_time': lock_end_timestamp,
                'amount_delta': amount_2
            }
        )

        # Global state
        vault_app_global_state = get_vault_app_global_state(self.ledger, VAULT_APP_ID)
        self.assertEqual(vault_app_global_state.total_locked_amount, amount_1 + amount_2)

        # 3. Create checkpoints
        block_timestamp = increase_lock_timestamp + 3 * DAY
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        amount = 40_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        # 4. Increase lock amount
        increase_lock_timestamp = block_timestamp + DAY // 2

        with unittest.mock.patch("time.time", return_value=increase_lock_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=increase_lock_timestamp)

    def test_extend_lock_end_time(self):
        # 1. Create lock
        # 2. Extend 2 weeks
        # 3. Create checkpoints
        # 4. Extend 4 weeks

        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK

        amount = 20_000_000
        slope = get_slope(amount)
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        # 1. Create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=lock_end_timestamp)]),
            SlopeChange(slope_delta=slope)
        )

        # 2. Extend 2 weeks
        extend_lock_txn_timestamp = lock_start_timestamp + DAY // 2

        old_lock_end_timestamp = lock_end_timestamp
        new_lock_end_timestamp = lock_end_timestamp + 5 * WEEK

        with unittest.mock.patch("time.time", return_value=extend_lock_txn_timestamp):
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
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=extend_lock_txn_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])

        # Assert that the slope delta is removed from the old lock end time.
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=old_lock_end_timestamp)]),
            SlopeChange(slope_delta=0)
        )
        # Assert that the slope delta is added to the new lock end time.
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=new_lock_end_timestamp)]),
            SlopeChange(slope_delta=slope)
        )

        # Assert Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 5)

        slope = get_slope(amount)
        bias = get_bias(slope, new_lock_end_timestamp - extend_lock_txn_timestamp)

        self.assertEqual(
            events[0],
            {
                'event_name': 'account_power',
                'user_address': self.user_address,
                'index': 1,
                'bias': bias,
                'timestamp': extend_lock_txn_timestamp,
                'slope': slope,
                'cumulative_power': ANY
            }
        )
        self.assertEqual(
            events[1],
            {
                'event_name': 'total_power',
                'index': 2,
                'bias': bias + 1,
                'timestamp': extend_lock_txn_timestamp,
                'slope': slope,
                'cumulative_power': ANY
            }
        )
        self.assertEqual(
            events[2],
            {
                'event_name': 'slope_change',
                'timestamp': old_lock_end_timestamp,
                'slope': 0
            }
        )
        self.assertEqual(
            events[3],
            {
                'event_name': 'slope_change',
                 'timestamp': new_lock_end_timestamp,
                 'slope': slope
            }
        )
        self.assertEqual(
            events[4],
            {
                'event_name': 'extend_lock_end_time',
                'user_address': self.user_address,
                'locked_amount': amount,
                'lock_end_time': new_lock_end_timestamp,
                'time_delta': new_lock_end_timestamp - lock_end_timestamp
            }
        )

        lock_end_timestamp = new_lock_end_timestamp

        # 3. Create checkpoints
        block_timestamp = extend_lock_txn_timestamp + 3 * DAY
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        # 4. Extend 4 weeks
        extend_lock_txn_timestamp = block_timestamp + DAY // 2

        old_lock_end_timestamp = lock_end_timestamp
        new_lock_end_timestamp = lock_end_timestamp + 4 * WEEK

        with unittest.mock.patch("time.time", return_value=extend_lock_txn_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=extend_lock_txn_timestamp)

        # Assert that the slope delta is removed from the old lock end time.
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=old_lock_end_timestamp)]),
            SlopeChange(slope_delta=0)
        )
        # Assert that the slope delta is added to the new lock end time.
        self.assertEqual(
            parse_box_slope_change(self.ledger.boxes[VAULT_APP_ID][get_slope_change_box_name(timestamp=new_lock_end_timestamp)]),
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

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # 1. Get Power, there is no lock
        txn_group = prepare_get_tiny_power_of_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)  # There is no lock, thus the power is 0.

        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK

        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        # 2. Create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - lock_start_timestamp))

        # 3. Get Power
        txn_group = prepare_get_tiny_power_of_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)   # Assert that the power is equal to the bias.

        # 4. Get Power (after 1 day)
        block_timestamp += DAY
        bias = get_bias(slope, (lock_end_timestamp - block_timestamp))

        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)

        # 5. Get Power (after 1 week)
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

        # 7. Withdraw
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

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # 1. Get Power, there is no lock
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

        # 2. Create lock
        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(lock_start_timestamp) + 5 * WEEK

        amount = 20_000_000
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - lock_start_timestamp))

        power_at_timestamp = lock_start_timestamp
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
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)   # Assert that the power at lock start is equal to the bias.

        # 4. Get Power (after 1 day)
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

        # 5. Get Power (after 1 week)
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

        # 6. Get Power - Expired
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
        # 7. Withdraw
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

        # 8. Get Power
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

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # 1. Get total power, there is no lock
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

        # 2. Create lock
        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        slope = get_slope(amount)
        bias = get_bias(slope, (lock_end_timestamp - lock_start_timestamp))

        # 3. Get total power
        txn_group = prepare_get_total_tiny_power_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            vault_app_global_state=get_vault_app_global_state(self.ledger),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias)

        # 4. Get total power (after 1 day)
        block_timestamp += DAY
        bias_delta = get_bias(slope, block_timestamp - lock_start_timestamp)

        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertAlmostEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias - bias_delta, delta=(block_timestamp - lock_start_timestamp) // DAY)

        # 5. Get total power (after 1 week)
        block_timestamp += WEEK
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)
        bias_delta = get_bias(slope, block_timestamp - lock_start_timestamp)

        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertAlmostEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias - bias_delta, delta=(block_timestamp - lock_start_timestamp) // DAY)

        # 6. Get total power - Expired
        block_timestamp = lock_end_timestamp
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        block_timestamp = lock_end_timestamp + 1
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), 0)

        # 7. Withdraw
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

        # 8. Get total power
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

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Get total power, there is no lock
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

        # Create locks
        lock_start_timestamp = block_timestamp
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

        slope_3 = get_slope(amount_3)
        bias_3 = get_bias(slope_3, (lock_end_timestamp_2 - lock_start_timestamp))

        block_timestamp = lock_end_timestamp_2 + DAY
        self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

        # Assert that after lock_end_timestamp_1, the total power is equal to the bias of the user 3.
        power_at = lock_end_timestamp_1
        bias_delta = get_bias(slope_3, power_at - lock_start_timestamp)

        txn_group = prepare_get_total_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            timestamp=power_at,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias_3 - bias_delta)

        # Assert that after lock_end_timestamp_1, the total power is equal to the bias of the user 3.
        power_at += DAY
        bias_delta = get_bias(slope_3, power_at - lock_start_timestamp)

        txn_group = prepare_get_total_tiny_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            timestamp=power_at,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:]), bias_3 - bias_delta)

        # Assert that total tiny power is 0 after lock_end_timestamp_2, all locks are expired.
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

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # 1. Create lock
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

        # 2. Increase lock amount 50x
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

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # 1. Create lock
        extend_count = 50

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

        # 2. Extend 50x
        for i in range(extend_count):
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

    def test_create_checkpoints(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 200 * WEEK

        amount = 20_000_000
        slope = get_slope(amount)
        self.ledger.move(
            amount,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], amount)
        
        # Global state
        self.assertEqual(get_vault_app_global_state(self.ledger, VAULT_APP_ID).total_power_count, 2)
        self.assertEqual(len(parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=0)])), 2)  # 1 for init, 1 for create lock

        # Create checkpoints
        block_timestamp += DAY // 2

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_checkpoints_transactions(
                vault_app_id=VAULT_APP_ID,
                sender=self.user_address,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])

        # Assert Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(
            events[0],
            {'event_name': 'total_power', 'index': 2, 'bias': ANY, 'timestamp': block_timestamp, 'slope': slope, 'cumulative_power': ANY}
        )
        self.assertEqual(
            events[1],
            {'event_name': 'create_checkpoints'}
        )

        # Global state
        self.assertEqual(get_vault_app_global_state(self.ledger, VAULT_APP_ID).total_power_count, 3)
        self.assertEqual(len(parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=0)])), 3)

        # 2 checkpoints: for the start of the week and the current time
        block_timestamp += WEEK
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_checkpoints_transactions(
                vault_app_id=VAULT_APP_ID,
                sender=self.user_address,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])

        # Assert Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 3)
        self.assertEqual(
            events[0],
            {'event_name': 'total_power', 'index': 3, 'bias': ANY, 'timestamp': get_start_timestamp_of_week(block_timestamp), 'slope': slope, 'cumulative_power': ANY}
        )
        self.assertEqual(
            events[1],
            {'event_name': 'total_power', 'index': 4, 'bias': ANY, 'timestamp': block_timestamp, 'slope': slope, 'cumulative_power': ANY}
        )
        self.assertEqual(
            events[2],
            {'event_name': 'create_checkpoints'}
        )

        # Global state
        self.assertEqual(get_vault_app_global_state(self.ledger, VAULT_APP_ID).total_power_count, 5)
        self.assertEqual(len(parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=0)])), 5)

        # Max 9 Weeks
        block_timestamp += 9 * WEEK
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_checkpoints_transactions(
                vault_app_id=VAULT_APP_ID,
                sender=self.user_address,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])

        # Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 10)
        
        # Global state
        self.assertEqual(get_vault_app_global_state(self.ledger, VAULT_APP_ID).total_power_count, 14)
        self.assertEqual(len(parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=0)])), 14)

        block_timestamp += 20 * WEEK
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_checkpoints_transactions(
                vault_app_id=VAULT_APP_ID,
                sender=self.user_address,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])

        # Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 10)

        # Global state
        self.assertEqual(get_vault_app_global_state(self.ledger, VAULT_APP_ID).total_power_count, 23)
        self.assertEqual(len(parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=0)])), 21)
        self.assertEqual(len(parse_box_total_power(self.ledger.boxes[VAULT_APP_ID][get_total_power_box_name(box_index=1)])), 2)

    def test_delete_boxes(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 20 * WEEK

        amount = 10_000_000
        self.ledger.move(
            amount * 200,
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

        while True:
            block_timestamp += DAY
            if block_timestamp > lock_end_timestamp:
                break

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

        txn_group = prepare_delete_account_power_boxes_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            account_state=get_account_state(self.ledger, self.user_address),
            box_count=1,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        app_call_txn = get_first_app_call_txn(block[b'txns'])
        inner_txns = app_call_txn[b'dt'][b'itx']

        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(
            events[0],
            {'event_name': 'box_del', 'box_name': list(get_account_power_box_name(address=self.user_address, box_index=0))}
        )
        self.assertEqual(
            events[1],
            {'event_name': 'delete_account_power_boxes', 'user_address': self.user_address, 'box_index_start': 0, 'box_count': 1}
        )
        
        self.assertEqual(len(inner_txns), 1)
        self.assertEqual(inner_txns[0][b'txn'][b'amt'], ACCOUNT_POWER_BOX_COST)  # Assert that the account power box cost is sent back to user.

        txn_group = prepare_delete_account_power_boxes_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            account_state=get_account_state(self.ledger, self.user_address),
            box_count=2,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        app_call_txn = get_first_app_call_txn(block[b'txns'])
        inner_txns = app_call_txn[b'dt'][b'itx']

        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 3)
        self.assertEqual(
            events[0],
            {'event_name': 'box_del', 'box_name': list(get_account_power_box_name(address=self.user_address, box_index=1))}
        )
        self.assertEqual(
            events[1],
            {'event_name': 'box_del', 'box_name': list(get_account_power_box_name(address=self.user_address, box_index=2))}
        )
        self.assertEqual(
            events[2],
            {'event_name': 'delete_account_power_boxes', 'user_address': self.user_address, 'box_index_start': 1, 'box_count': 2}
        )
        
        self.assertEqual(len(inner_txns), 1)
        self.assertEqual(inner_txns[0][b'txn'][b'amt'], 2 * ACCOUNT_POWER_BOX_COST)

        # Delete all
        txn_group = prepare_delete_account_state_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            account_state=get_account_state(self.ledger, self.user_address),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        app_call_txn = get_first_app_call_txn(block[b'txns'])
        inner_txns = app_call_txn[b'dt'][b'itx']

        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=vault_events)
        self.assertEqual(len(events), 6)
        self.assertEqual(
            events[0],
            {'event_name': 'box_del', 'box_name': list(get_account_power_box_name(address=self.user_address, box_index=3))}
        )
        self.assertEqual(
            events[1],
            {'event_name': 'box_del', 'box_name': list(get_account_power_box_name(address=self.user_address, box_index=4))}
        )
        self.assertEqual(
            events[2],
            {'event_name': 'box_del', 'box_name': list(get_account_power_box_name(address=self.user_address, box_index=5))}
        )
        self.assertEqual(
            events[3],
            {'event_name': 'box_del', 'box_name': list(get_account_power_box_name(address=self.user_address, box_index=6))}
        )
        self.assertEqual(
            events[4],
            {'event_name': 'box_del', 'box_name': list(get_account_state_box_name(address=self.user_address))}
        )
        self.assertEqual(
            events[5],
            {'event_name': 'delete_account_state', 'user_address': self.user_address, 'box_index_start': 4, 'box_count': 4}
        )
        self.assertEqual(len(inner_txns), 1)
        self.assertEqual(inner_txns[0][b'txn'][b'amt'], 4 * ACCOUNT_POWER_BOX_COST + ACCOUNT_STATE_BOX_COST)


    def test_get_box(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 6 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount * 200,
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

        account_state_box_name = get_account_state_box_name(address=self.user_address)
        txn_group = prepare_get_box_transaction(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            box_name=account_state_box_name,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        return_value = block[b'txns'][0][b'dt'][b'lg'][-1]

        exists = bytes_to_int(return_value[4:12])
        self.assertTrue(exists)
        size = return_value[12:14]
        box_data = return_value[14:]
        
        expected = get_account_state(self.ledger, self.user_address)
        retrieved = parse_box_account_state(box_data)
        self.assertEqual(expected, retrieved)

    def test_get_cumulative_power_of_at(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp + 6 * WEEK)
        lock_duration = int(lock_end_timestamp - lock_start_timestamp)
        amount = 10_000_000
        self.ledger.move(
            amount * 200,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        # Get the cumulative power before the lock
        txn_group = prepare_get_cumulative_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=lock_start_timestamp - WEEK,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp + WEEK)

        user_cumulative_power = bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:])

        assert(user_cumulative_power == 0)

        # Get cumulative power at the end of the lock
        txn_group = prepare_get_cumulative_power_of_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            user_address=self.user_address,
            user_account_powers=get_account_powers(self.ledger, self.user_address),
            timestamp=lock_end_timestamp + 1,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_end_timestamp + WEEK)

        user_cumulative_power = bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:])

        # There is a difference between the cumulative power calculated by the contract and the one calculated by the test.
        # Same difference happens between get_cumulative_power_1 and get_cumulative_power_2 results.
        #assert(user_cumulative_power == get_cumulative_power_delta(bias=get_bias(get_slope(amount), lock_duration), slope=get_slope(amount), time_delta=lock_duration))
        bias = get_bias(get_slope(amount), lock_duration)
        slope = get_slope(amount)
        assert(user_cumulative_power == int(((bias * bias) * TWO_TO_THE_64) / (slope * 2)))

    def test_get_total_cumulative_power_at(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        last_checkpoint_timestamp = block_timestamp - 10

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(timestamp=last_checkpoint_timestamp)

        # Create lock
        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp + 6 * WEEK)
        lock_duration = int(lock_end_timestamp - lock_start_timestamp)
        amount = 10_000_000
        self.ledger.move(
            amount * 200,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=self.user_address
        )

        with unittest.mock.patch("time.time", return_value=lock_start_timestamp):
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
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp)

        # Get the cumulative power before the lock
        txn_group = prepare_get_total_cumulative_power_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            timestamp=lock_start_timestamp - WEEK,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_start_timestamp + WEEK)

        total_cumulative_power = bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:])

        assert(total_cumulative_power == 0)

        # Get total cumulative power at the end of the lock
        self.create_checkpoints(self.user_address, self.user_sk, lock_end_timestamp + WEEK)

        txn_group = prepare_get_total_cumulative_power_at_transactions(
            vault_app_id=VAULT_APP_ID,
            sender=self.user_address,
            total_powers=get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count),
            timestamp=lock_end_timestamp + WEEK,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=lock_end_timestamp + WEEK)

        total_cumulative_power = bytes_to_int(block[b'txns'][0][b'dt'][b'lg'][-1][4:])

        total_power_index = get_power_index_at(get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count), lock_end_timestamp)
        total_power_at_end = get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count)[total_power_index]

        _total_cumulative_power = total_power_at_end.cumulative_power + get_cumulative_power_delta(bias=total_power_at_end.bias, slope=total_power_at_end.slope, time_delta=lock_end_timestamp - total_power_at_end.timestamp)

        assert(total_cumulative_power == _total_cumulative_power)

        # Assert the total cumulative power from start of lock to end of lock
        total_power_at_start = get_all_total_powers(self.ledger, get_vault_app_global_state(self.ledger).total_power_count)[1]

        # In this case our total cumulative power graph is a perpendicular triangle where the height is the bias and the base is the lock duration. total_power_at_lock.bias * lock_duration / 2. Which is _total_cumulative_power
        _total_cumulative_power = get_cumulative_power_delta(bias=total_power_at_start.bias, slope=total_power_at_start.slope, time_delta=total_power_at_end.timestamp - total_power_at_start.timestamp)

        assert(total_power_at_end.cumulative_power == _total_cumulative_power), f"{total_power_at_end.cumulative_power} != {_total_cumulative_power}"
