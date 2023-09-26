import unittest.mock
from datetime import datetime
from unittest.mock import ANY
from zoneinfo import ZoneInfo
from algojig import LogicEvalError
from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.governance.constants import WEEK
from tinyman.governance.event import decode_logs
from tinyman.governance.rewards.events import rewards_events
from tinyman.governance.rewards.storage import RewardsAppGlobalState, RewardHistory, RewardClaimSheet, get_reward_history_box_name, parse_box_reward_history, get_account_reward_claim_sheet_box_name
from tinyman.governance.rewards.transactions import prepare_claim_reward_transactions, prepare_init_transactions, prepare_create_reward_period_transactions, prepare_set_reward_amount_transactions, prepare_set_manager_transactions, prepare_set_rewards_manager_transactions, prepare_set_reward_amount_transactions
from tinyman.governance.rewards.constants import REWARDS_MANAGER_KEY
from tinyman.governance.vault.constants import TOTAL_LOCKED_AMOUNT_KEY
from tinyman.governance.vault.storage import get_power_index_at
from tinyman.governance.vault.transactions import prepare_create_lock_transactions, prepare_increase_lock_amount_transactions
from tinyman.governance.vault.utils import get_start_timestamp_of_week
from tinyman.utils import TransactionGroup
from tinyman.utils import int_to_bytes, bytes_to_int
from tinyman.governance.transactions import _prepare_budget_increase_transaction

from tests.common import BaseTestCase, VaultAppMixin, RewardsAppMixin
from tests.constants import TINY_ASSET_ID, rewards_approval_program, rewards_clear_state_program, VAULT_APP_ID, REWARDS_APP_ID
from tests.rewards.utils import get_rewards_app_global_state, get_reward_histories
from tests.utils import get_first_app_call_txn
from tests.utils import get_total_power_index_at, get_reward_history_index_at
from tests.vault.utils import get_vault_app_global_state, get_account_state, get_slope_change_at, get_account_powers


class RewardsTestCase(VaultAppMixin, RewardsAppMixin, BaseTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_address = generate_account()
        cls.vault_app_creation_timestamp = int(datetime(year=2022, month=3, day=1, tzinfo=ZoneInfo("UTC")).timestamp())

    def setUp(self):
        super().setUp()
        self.ledger.set_account_balance(self.app_creator_address, 50_000_000)

        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

        self.ledger.set_account_balance(self.user_address, 120_000_000)

    def test_create_app(self):
        block_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        txn_group = TransactionGroup([
            transaction.ApplicationCreateTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                on_complete=transaction.OnComplete.NoOpOC,
                approval_program=rewards_approval_program.bytecode,
                clear_program=rewards_clear_state_program.bytecode,
                global_schema=transaction.StateSchema(num_uints=5, num_byte_slices=2),
                local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                extra_pages=3,
                app_args=[TINY_ASSET_ID, VAULT_APP_ID],
            )
        ])
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_id = block[b"txns"][0][b"apid"]

        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, app_id)
        self.assertEqual(
            rewards_app_global_state,
            RewardsAppGlobalState(
                first_period_timestamp=0,
                vault_app_id=VAULT_APP_ID,
                manager=decode_address(self.app_creator_address),
                rewards_manager=decode_address(self.app_creator_address),
                reward_history_count=0,
                reward_period_count=0,
                tiny_asset_id=TINY_ASSET_ID
            )
        )

    def test_init_app(self):
        block_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        self.create_rewards_app(self.app_creator_address)

        reward_amount = 1_000_000
        reward_histories_box_name = get_reward_history_box_name(box_index=0)
        txn_group = prepare_init_transactions(
            rewards_app_id=REWARDS_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            reward_amount=reward_amount,
            sender=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        
        # Opt-in Inner txn
        opt_in_itx = app_call_txn[b'dt'][b'itx'][0][b'txn']
        self.assertDictEqual(
            opt_in_itx,
            {
                b'arcv': decode_address(get_application_address(REWARDS_APP_ID)),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(get_application_address(REWARDS_APP_ID)),
                b'type': b'axfer',
                b'xaid': TINY_ASSET_ID
            }
        )
        
        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(
            rewards_app_global_state,
            RewardsAppGlobalState(
                first_period_timestamp=get_start_timestamp_of_week(block_timestamp) + WEEK,
                vault_app_id=VAULT_APP_ID,
                manager=decode_address(self.app_creator_address),
                rewards_manager=decode_address(self.app_creator_address),
                reward_history_count=1,
                reward_period_count=0,
                tiny_asset_id=TINY_ASSET_ID
            )
        )
        
        # Boxes
        reward_histories = parse_box_reward_history(self.ledger.boxes[REWARDS_APP_ID][reward_histories_box_name])
        self.assertEqual(len(reward_histories), 1)
        reward_history = reward_histories[0]
        next_week_timestamp = get_start_timestamp_of_week(block_timestamp) + WEEK
        self.assertEqual(
            reward_history,
            RewardHistory(
                timestamp=next_week_timestamp,
                reward_amount=reward_amount
            )
        )

        # Logs
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(
            events[0],
            {'event_name': 'reward_history', 'index': 0, 'timestamp': next_week_timestamp, 'reward_amount': reward_amount}
        )
        self.assertEqual(
            events[1],
            {'event_name': 'init', 'first_period_timestamp': get_start_timestamp_of_week(block_timestamp) + WEEK, 'reward_amount': reward_amount}
        )

    def test_claim_rewards_one_by_one(self):
        block_timestamp = self.vault_app_creation_timestamp + WEEK // 2
        first_period_start_timestamp = get_start_timestamp_of_week(block_timestamp) + WEEK
        
        reward_amount = 40_000_000
        self.create_rewards_app(self.app_creator_address)
        self.init_rewards_app(first_period_start_timestamp, reward_amount)

        user_1_sk, user_1_address = generate_account()
        self.ledger.set_account_balance(user_1_address, 10_000_000)
        
        user_2_sk, user_2_address = generate_account()
        self.ledger.set_account_balance(user_2_address, 10_000_000)
        
        self.ledger.move(
            20_000_000,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_1_address
        )
        
        self.ledger.move(
            10_000_000,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=user_2_address
        )
        
        self.ledger.move(
            reward_amount * 3,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=get_application_address(REWARDS_APP_ID)
        )
        
        lock_end_timestamp_1 = get_start_timestamp_of_week(block_timestamp) + 10 * WEEK
        lock_end_timestamp_2 = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK

        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_1_address,
                locked_amount=20_000_000,
                lock_end_time=lock_end_timestamp_1,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_1_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp_1),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_1_address, user_1_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], 20_000_000)
        
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            txn_group = prepare_create_lock_transactions(
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_2_address,
                locked_amount=10_000_000,
                lock_end_time=lock_end_timestamp_2,
                vault_app_global_state=get_vault_app_global_state(self.ledger),
                account_state=get_account_state(self.ledger, user_2_address),
                slope_change_at_lock_end_time=get_slope_change_at(self.ledger, lock_end_timestamp_2),
                suggested_params=self.sp,
            )
        txn_group.sign_with_private_key(user_2_address, user_2_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        self.assertEqual(self.ledger.global_states[VAULT_APP_ID][TOTAL_LOCKED_AMOUNT_KEY], 30_000_000)

        for period_index in range(3):
            # Create checkpoints
            block_timestamp = first_period_start_timestamp + (WEEK * (period_index + 1))
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

                txn_group = prepare_create_reward_period_transactions(
                    rewards_app_id=REWARDS_APP_ID,
                    vault_app_id=VAULT_APP_ID,
                    sender=self.user_address,
                    rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                    reward_history_index=get_reward_history_index_at(self.ledger, REWARDS_APP_ID, first_period_start_timestamp + (WEEK * period_index)),
                    total_power_period_start_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, first_period_start_timestamp + (WEEK * period_index)) or 0,
                    total_power_period_end_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, first_period_start_timestamp + (WEEK * (period_index + 1))),
                    suggested_params=self.sp,
                )
                txn_group.sign_with_private_key(self.user_address, self.user_sk)
                block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
                app_call_txn = get_first_app_call_txn(block[b'txns'])
                logs = app_call_txn[b'dt'][b'lg']
                events = decode_logs(logs, events=rewards_events)
                self.assertEqual(len(events), 2)
                self.assertEqual(
                    events[0],
                    {'event_name': 'reward_period', 'index': period_index, 'total_reward_amount': reward_amount, 'total_cumulative_power_delta': ANY}
                )
                self.assertEqual(
                    events[1],
                    {'event_name': 'create_reward_period', 'index': period_index, 'total_reward_amount': reward_amount, 'total_cumulative_power_delta': ANY}
                )
                
        account_powers = get_account_powers(self.ledger, user_1_address)
        for period_index in range(3):
            period_start_timestamp = first_period_start_timestamp + (WEEK * period_index)
            period_end_timestamp = period_start_timestamp + WEEK
            account_power_index_start = get_power_index_at(account_powers, period_start_timestamp) or 0
            account_power_index_end = get_power_index_at(account_powers, period_end_timestamp)
            if raw_box := self.ledger.boxes[REWARDS_APP_ID].get(get_account_reward_claim_sheet_box_name(user_1_address, 0)):
                sheet = RewardClaimSheet(value=raw_box)
                self.assertEqual(sheet.is_reward_claimed_for_period(period_index), False)
            
            txn_group = prepare_claim_reward_transactions(
                rewards_app_id=REWARDS_APP_ID,
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_1_address,
                period_index_start=period_index,
                period_count=1,
                account_power_indexes=[account_power_index_start, account_power_index_end],
                create_reward_claim_sheet=not period_index,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(user_1_address, user_1_sk)
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            app_call_txn = get_first_app_call_txn(block[b'txns'])
            logs = app_call_txn[b'dt'][b'lg']
            events = decode_logs(logs, events=rewards_events)
            self.assertEqual(len(events), 1)
            self.assertEqual(
                events[0],
                {
                    'event_name': 'claim_rewards',
                    'user_address': user_1_address,
                    'total_reward_amount': ANY,
                    'period_index_start': period_index,
                    'period_count': 1,
                    'reward_amounts': [ANY]
                }
            )
            inner_txns = app_call_txn[b'dt'][b'itx']
            self.assertEqual(len(inner_txns), 2)
            self.assertEqual(
                inner_txns[0][b'txn'],
                {
                    b'apaa': [
                        b'get_account_cumulative_power_delta',
                        decode_address(user_1_address),
                        int_to_bytes(period_start_timestamp),
                        int_to_bytes(period_end_timestamp),
                        int_to_bytes(account_power_index_start),
                        int_to_bytes(account_power_index_end)
                    ],
                    b'apid': VAULT_APP_ID,
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(get_application_address(REWARDS_APP_ID)),
                    b'type': b'appl'
                },
                {
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(get_application_address(REWARDS_APP_ID)),
                    b'type': b'appl',
                }
            )
            self.assertEqual(
                inner_txns[1][b'txn'],
                {
                    b'aamt': events[0]['total_reward_amount'],
                    b'arcv': decode_address(user_1_address),
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(get_application_address(REWARDS_APP_ID)),
                    b'type': b'axfer',
                    b'xaid': TINY_ASSET_ID
                }
            )
            sheet = RewardClaimSheet(value=self.ledger.boxes[REWARDS_APP_ID][get_account_reward_claim_sheet_box_name(user_1_address, 0)])
            self.assertEqual(sheet.is_reward_claimed_for_period(period_index), True)

        account_powers = get_account_powers(self.ledger, user_2_address)
        for period_index in range(3):
            account_power_index_start = get_power_index_at(account_powers, first_period_start_timestamp + (WEEK * period_index)) or 0
            account_power_index_end = get_power_index_at(account_powers, first_period_start_timestamp + (WEEK * (period_index + 1)))
            if raw_box := self.ledger.boxes[REWARDS_APP_ID].get(get_account_reward_claim_sheet_box_name(user_2_address, 0)):
                sheet = RewardClaimSheet(value=raw_box)
                self.assertEqual(sheet.is_reward_claimed_for_period(period_index), False)

            txn_group = prepare_claim_reward_transactions(
                rewards_app_id=REWARDS_APP_ID,
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=user_2_address,
                period_index_start=period_index,
                period_count=1,
                account_power_indexes=[account_power_index_start, account_power_index_end],
                create_reward_claim_sheet=not period_index,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(user_2_address, user_2_sk)
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            app_call_txn = get_first_app_call_txn(block[b'txns'])
            logs = app_call_txn[b'dt'][b'lg']
            events = decode_logs(logs, events=rewards_events)
            self.assertEqual(len(events), 1)
            self.assertEqual(
                events[0],
                {
                    'event_name': 'claim_rewards',
                    'user_address': user_2_address,
                    'total_reward_amount': ANY,
                    'period_index_start': period_index,
                    'period_count': 1,
                    'reward_amounts': [ANY]
                }
            )
            inner_txns = app_call_txn[b'dt'][b'itx']
            self.assertEqual(len(inner_txns), 2)
            self.assertEqual(
                inner_txns[1][b'txn'],
                {
                    b'aamt': events[0]['total_reward_amount'],
                    b'arcv': decode_address(user_2_address),
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(get_application_address(REWARDS_APP_ID)),
                    b'type': b'axfer',
                    b'xaid': TINY_ASSET_ID
                }
            )
            sheet = RewardClaimSheet(value=self.ledger.boxes[REWARDS_APP_ID][get_account_reward_claim_sheet_box_name(user_2_address, 0)])
            self.assertEqual(sheet.is_reward_claimed_for_period(period_index), True)


    def test_claim_rewards_two_years(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())

        reward_amount = 1_000_000
        self.create_rewards_app(self.app_creator_address)
        first_period_start_timestamp = get_start_timestamp_of_week(self.vault_app_creation_timestamp) + WEEK
        self.init_rewards_app(first_period_start_timestamp, reward_amount)
        self.ledger.move(
            reward_amount * 1_000,
            asset_id=TINY_ASSET_ID,
            sender=self.ledger.assets[TINY_ASSET_ID]["creator"],
            receiver=get_application_address(REWARDS_APP_ID)
        )

        # Create lock
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 200 * WEEK
        amount = 10_000_000
        self.ledger.move(
            amount * 100,
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

        for period_index in range(120):

            # Create checkpoints
            block_timestamp = first_period_start_timestamp + (WEEK * (period_index + 1))
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

                txn_group = prepare_create_reward_period_transactions(
                    rewards_app_id=REWARDS_APP_ID,
                    vault_app_id=VAULT_APP_ID,
                    sender=self.user_address,
                    rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                    reward_history_index=get_reward_history_index_at(self.ledger, REWARDS_APP_ID, first_period_start_timestamp + (WEEK * period_index)),
                    total_power_period_start_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, first_period_start_timestamp + (WEEK * period_index)) or 0,
                    total_power_period_end_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, first_period_start_timestamp + (WEEK * (period_index + 1))),
                    suggested_params=self.sp,
                )
                txn_group.sign_with_private_key(self.user_address, self.user_sk)
                self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

                if period_index % 2:
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

                if period_index % 4 == 0:
                    txn_group = prepare_set_reward_amount_transactions(
                        rewards_app_id=REWARDS_APP_ID,
                        rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                        reward_amount=reward_amount + period_index * 1_000,
                        sender=self.app_creator_address,
                        suggested_params=self.sp,
                    )
                    txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
                    self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        account_powers = get_account_powers(self.ledger, self.user_address)        
        period_index_start = 0
        period_count = 3
        account_power_indexes = [get_power_index_at(account_powers, first_period_start_timestamp + (WEEK * (period_index_start + i))) or 0 for i in range(period_count + 1)]

        txn_group = prepare_claim_reward_transactions(
            rewards_app_id=REWARDS_APP_ID,
            vault_app_id=VAULT_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            sender=self.user_address,
            period_index_start=period_index_start,
            period_count=period_count,
            account_power_indexes=account_power_indexes,
            create_reward_claim_sheet=True,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b'txns'])
        logs = app_call_txn[b'dt'][b'lg']
        events = decode_logs(logs, events=rewards_events)
        
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0],
            {
                'event_name': 'claim_rewards',
                'user_address': self.user_address,
                'total_reward_amount': ANY,
                'period_index_start': period_index_start,
                'period_count': period_count,
                'reward_amounts': ANY
            }
        )
        self.assertEqual(len(events[0]["reward_amounts"]), 104)
        inner_txns = app_call_txn[b'dt'][b'itx']
        self.assertEqual(len(inner_txns), 104 + 1)
        self.assertEqual(
            inner_txns[-1][b'txn'],
            {
                b'aamt': events[0]['total_reward_amount'],
                b'arcv': decode_address(self.user_address),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(get_application_address(REWARDS_APP_ID)),
                b'type': b'axfer',
                b'xaid': TINY_ASSET_ID
            }
        )

        sheet = RewardClaimSheet(value=self.ledger.boxes[REWARDS_APP_ID][get_account_reward_claim_sheet_box_name(self.user_address, 0)])
        self.assertEqual(all(sheet.claim_sheet[:104]), True)
        self.assertEqual(sum(sheet.claim_sheet[104:]), 0)


    def test_budget_increase(self):
        self.create_rewards_app(self.app_creator_address)

        txn = _prepare_budget_increase_transaction(
            sender=self.user_address,
            sp=self.sp,
            index=REWARDS_APP_ID,
        )
        txn_group = TransactionGroup([txn])
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions)
        
        txn = _prepare_budget_increase_transaction(
            sender=self.user_address,
            sp=self.sp,
            index=REWARDS_APP_ID,
            foreign_apps=[VAULT_APP_ID],
            extra_app_args=[2],
        )
        txn.fee *= 3
        txn_group = TransactionGroup([txn])
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        app_call_txn = get_first_app_call_txn(block[b'txns'], ignore_budget_increase=False)
        inner_txns = app_call_txn[b'dt'][b'itx']
        self.assertEqual(len(inner_txns), 2)

    def test_set_reward_amount(self):
        block_datetime = datetime(year=2022, month=3, day=1, hour=1, tzinfo=ZoneInfo("UTC"))
        block_timestamp = int(block_datetime.timestamp())
        first_period_start_timestamp = get_start_timestamp_of_week(block_timestamp) + WEEK

        reward_amount = 1_000_000
        self.create_rewards_app(self.app_creator_address)
        self.init_rewards_app(first_period_start_timestamp, reward_amount)
        
        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)
        self.ledger.global_states[REWARDS_APP_ID][REWARDS_MANAGER_KEY] = decode_address(user_address)

        txn_group = prepare_set_reward_amount_transactions(
            rewards_app_id=REWARDS_APP_ID,
            rewards_app_global_state=get_rewards_app_global_state(self.ledger),
            reward_amount=10_000,
            sender=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(REWARDS_MANAGER_KEY))')
        
        for i in range(20):
            new_reward_amount = 2_000_000 + i
            block_timestamp = block_timestamp + i
            txn_group = prepare_set_reward_amount_transactions(
                rewards_app_id=REWARDS_APP_ID,
                rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                reward_amount=new_reward_amount,
                sender=user_address,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(user_address, user_sk)
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            app_call_txn = get_first_app_call_txn(block[b'txns'])
            logs = app_call_txn[b'dt'][b'lg']
            events = decode_logs(logs, events=rewards_events)
            self.assertEqual(len(events), 2)
            self.assertEqual(
                events[0],
                {'event_name': 'reward_history', 'index': i + 1, 'timestamp': block_timestamp, 'reward_amount': new_reward_amount}
            )
            self.assertEqual(
                events[1],
                {'event_name': 'set_reward_amount', 'timestamp': block_timestamp, 'reward_amount': new_reward_amount}
            )
            reward_history = get_reward_histories(self.ledger)[-1]
            self.assertEqual(
                reward_history,
                RewardHistory(
                    timestamp=block_timestamp,
                    reward_amount=new_reward_amount,
                )
            )

    def test_set_manager(self):
        self.create_rewards_app(self.app_creator_address)

        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        # Test address validation
        txn_group = prepare_set_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
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
            rewards_app_id=REWARDS_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_manager', 'manager': user_address}
        )
        
        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(rewards_app_global_state.manager, decode_address(user_address))

        # Set back app creator as manager
        txn_group = prepare_set_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=user_address,
            new_manager_address=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_manager', 'manager': self.app_creator_address}
        )
        
        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(rewards_app_global_state.manager, decode_address(self.app_creator_address))

    def test_set_rewards_manager(self):
        self.create_rewards_app(self.app_creator_address)

        user_sk, user_address = generate_account()
        self.ledger.set_account_balance(user_address, 10_000_000)

        # Test address validation
        txn_group = prepare_set_rewards_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=user_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(user_address, user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get(MANAGER_KEY))')

        # Set user as manager
        txn_group = prepare_set_rewards_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_rewards_manager', 'rewards_manager': user_address}
        )

        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(rewards_app_global_state.rewards_manager, decode_address(user_address))

        # Set back app creator as manager
        txn_group = prepare_set_rewards_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b'txns'][0][b'dt'][b'lg']
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(
            events[0],
            {'event_name': 'set_rewards_manager', 'rewards_manager': self.app_creator_address}
        )

        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(rewards_app_global_state.rewards_manager, decode_address(self.app_creator_address))
