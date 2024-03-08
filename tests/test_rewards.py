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
from tinyman.governance.rewards.transactions import (
    prepare_claim_reward_transactions,
    prepare_init_transactions,
    prepare_create_reward_period_transactions,
    prepare_get_box_transaction,
    prepare_set_reward_amount_transactions,
    prepare_set_manager_transactions,
    prepare_set_rewards_manager_transactions,
    prepare_set_reward_amount_transactions,
)
from tinyman.governance.rewards.utils import calculate_reward_amount
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
from tests.rewards.utils import get_rewards_app_global_state, get_reward_histories, get_reward_periods
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

        # Create and init vault app.
        self.create_vault_app(self.app_creator_address)
        self.init_vault_app(self.vault_app_creation_timestamp + 30)

        # Set the accounts.
        self.ledger.set_account_balance(self.user_address, 100_000_000)
        self.user_1_sk, self.user_1_address = generate_account()
        self.ledger.set_account_balance(self.user_1_address, 10_000_000)
        self.user_2_sk, self.user_2_address = generate_account()
        self.ledger.set_account_balance(self.user_2_address, 10_000_000)
        self.user_3_sk, self.user_3_address = generate_account()
        self.ledger.set_account_balance(self.user_3_address, 10_000_000)

        self.latest_block_datetime = datetime(year=2022, month=3, day=2, tzinfo=ZoneInfo("UTC"))
        self.latest_block_timestamp = int(self.latest_block_datetime.timestamp())
        self.first_period_start_timestamp = get_start_timestamp_of_week(self.latest_block_timestamp) + WEEK

    def create_and_init_rewards_app(self):
        self.total_reward_amount = 1_000_000
        self.create_rewards_app(self.app_creator_address)
        self.init_rewards_app(self.first_period_start_timestamp, self.total_reward_amount)

        self.ledger.move(self.total_reward_amount * 10, asset_id=TINY_ASSET_ID, sender=self.ledger.assets[TINY_ASSET_ID]["creator"], receiver=get_application_address(REWARDS_APP_ID))

    def test_create_app(self):
        block_timestamp = self.latest_block_timestamp

        txn_group = TransactionGroup(
            [
                transaction.ApplicationCreateTxn(
                    sender=self.app_creator_address,
                    sp=self.sp,
                    on_complete=transaction.OnComplete.NoOpOC,
                    approval_program=rewards_approval_program.bytecode,
                    clear_program=rewards_clear_state_program.bytecode,
                    global_schema=transaction.StateSchema(num_uints=5, num_byte_slices=2),
                    local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
                    extra_pages=3,
                    app_args=["create_application", TINY_ASSET_ID, VAULT_APP_ID],
                )
            ]
        )
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
                tiny_asset_id=TINY_ASSET_ID,
            ),
        )

    def test_init_app(self):
        block_timestamp = self.latest_block_timestamp

        self.create_rewards_app(self.app_creator_address)

        total_reward_amount = 1_000_000
        reward_histories_box_name = get_reward_history_box_name(box_index=0)
        txn_group = prepare_init_transactions(
            rewards_app_id=REWARDS_APP_ID,
            tiny_asset_id=TINY_ASSET_ID,
            reward_amount=total_reward_amount,
            sender=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
        app_call_txn = get_first_app_call_txn(block[b"txns"])

        # Opt-in Inner txn
        opt_in_itx = app_call_txn[b"dt"][b"itx"][0][b"txn"]
        self.assertDictEqual(
            opt_in_itx,
            {
                b"arcv": decode_address(get_application_address(REWARDS_APP_ID)),
                b"fv": ANY,
                b"lv": ANY,
                b"snd": decode_address(get_application_address(REWARDS_APP_ID)),
                b"type": b"axfer",
                b"xaid": TINY_ASSET_ID,
            },
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
                tiny_asset_id=TINY_ASSET_ID,
            ),
        )

        # Assert Boxes
        reward_histories = parse_box_reward_history(self.ledger.boxes[REWARDS_APP_ID][reward_histories_box_name])
        self.assertEqual(len(reward_histories), 1)

        reward_history = reward_histories[0]
        next_week_timestamp = get_start_timestamp_of_week(block_timestamp) + WEEK
        self.assertEqual(reward_history, RewardHistory(timestamp=next_week_timestamp, reward_amount=total_reward_amount))

        # Assert Logs
        logs = app_call_txn[b"dt"][b"lg"]
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], {"event_name": "reward_history", "index": 0, "timestamp": next_week_timestamp, "reward_amount": total_reward_amount})
        self.assertEqual(events[1], {"event_name": "init", "first_period_timestamp": get_start_timestamp_of_week(block_timestamp) + WEEK, "reward_amount": total_reward_amount})

    def test_claim_rewards_one_by_one(self):
        # 1. Create locks
        # 2. Create checkpoints
        # 3. Create reward periods
        # 4. Claim rewards for user_1
        # 5. Claim rewards for user_2

        self.create_and_init_rewards_app()
        block_timestamp = self.latest_block_timestamp

        # 1. Create locks
        lock_start_timestamp = block_timestamp
        lock_end_timestamp_1 = get_start_timestamp_of_week(block_timestamp) + 10 * WEEK
        lock_end_timestamp_2 = get_start_timestamp_of_week(block_timestamp) + 5 * WEEK

        self.create_lock(self.user_1_address, self.user_1_sk, 20_000_000, lock_start_timestamp, lock_end_timestamp_1)
        self.create_lock(self.user_2_address, self.user_2_sk, 10_000_000, lock_start_timestamp, lock_end_timestamp_2)

        for period_index in range(3):
            # 2. Create checkpoints
            block_timestamp = self.first_period_start_timestamp + (WEEK * (period_index + 1))
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

                # 3. Create reward periods
                txn_group = prepare_create_reward_period_transactions(
                    rewards_app_id=REWARDS_APP_ID,
                    vault_app_id=VAULT_APP_ID,
                    sender=self.user_address,
                    rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                    reward_history_index=get_reward_history_index_at(self.ledger, REWARDS_APP_ID, self.first_period_start_timestamp + (WEEK * period_index)),
                    total_power_period_start_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, self.first_period_start_timestamp + (WEEK * period_index)) or 0,
                    total_power_period_end_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, self.first_period_start_timestamp + (WEEK * (period_index + 1))),
                    suggested_params=self.sp,
                )
                txn_group.sign_with_private_key(self.user_address, self.user_sk)
                block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
                app_call_txn = get_first_app_call_txn(block[b"txns"])
                logs = app_call_txn[b"dt"][b"lg"]
                events = decode_logs(logs, events=rewards_events)

                self.assertEqual(len(events), 2)
                self.assertEqual(events[0], {"event_name": "reward_period", "index": period_index, "total_reward_amount": self.total_reward_amount, "total_cumulative_power_delta": ANY})
                self.assertEqual(events[1], {"event_name": "create_reward_period", "index": period_index, "total_reward_amount": self.total_reward_amount, "total_cumulative_power_delta": ANY})

        reward_periods = get_reward_periods(self.ledger)
        # 4. Claim rewards for user_1
        account_powers = get_account_powers(self.ledger, self.user_1_address)
        for period_index in range(3):
            period_start_timestamp = self.first_period_start_timestamp + (WEEK * period_index)
            period_end_timestamp = period_start_timestamp + WEEK
            account_power_index_start = get_power_index_at(account_powers, period_start_timestamp) or 0
            account_power_index_end = get_power_index_at(account_powers, period_end_timestamp)

            # Check that reward isn't claimed before the claim.
            if raw_box := self.ledger.boxes[REWARDS_APP_ID].get(get_account_reward_claim_sheet_box_name(self.user_1_address, 0)):
                sheet = RewardClaimSheet(value=raw_box)
                self.assertEqual(sheet.is_reward_claimed_for_period(period_index), False)

            txn_group = prepare_claim_reward_transactions(
                rewards_app_id=REWARDS_APP_ID,
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_1_address,
                period_index_start=period_index,
                period_count=1,
                account_power_indexes=[account_power_index_start, account_power_index_end],
                create_reward_claim_sheet=not period_index,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(self.user_1_address, self.user_1_sk)
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            app_call_txn = get_first_app_call_txn(block[b"txns"])
            logs = app_call_txn[b"dt"][b"lg"]
            events = decode_logs(logs, events=rewards_events)

            account_cumulative_power_delta = bytes_to_int(app_call_txn[b"dt"][b"itx"][0][b"dt"][b"lg"][0][4:])
            claimed_reward_amount = calculate_reward_amount(account_cumulative_power_delta, reward_periods[period_index])
            self.assertEqual(len(events), 1)
            self.assertEqual(
                events[0],
                {
                    "event_name": "claim_rewards",
                    "user_address": self.user_1_address,
                    "total_reward_amount": ANY,
                    "period_index_start": period_index,
                    "period_count": 1,
                    "reward_amounts": [claimed_reward_amount],
                },
            )

            # Assert inner transactions
            inner_txns = app_call_txn[b"dt"][b"itx"]
            self.assertEqual(len(inner_txns), 2)

            self.assertEqual(
                inner_txns[0][b"txn"],
                {
                    b"apaa": [
                        b"get_account_cumulative_power_delta",
                        decode_address(self.user_1_address),
                        int_to_bytes(period_start_timestamp),
                        int_to_bytes(period_end_timestamp),
                        int_to_bytes(account_power_index_start),
                        int_to_bytes(account_power_index_end),
                    ],
                    b"apid": VAULT_APP_ID,
                    b"fv": ANY,
                    b"lv": ANY,
                    b"snd": decode_address(get_application_address(REWARDS_APP_ID)),
                    b"type": b"appl",
                },
                {
                    b"fv": ANY,
                    b"lv": ANY,
                    b"snd": decode_address(get_application_address(REWARDS_APP_ID)),
                    b"type": b"appl",
                },
            )
            self.assertEqual(
                inner_txns[1][b"txn"],
                {
                    b"aamt": events[0]["total_reward_amount"],
                    b"arcv": decode_address(self.user_1_address),
                    b"fv": ANY,
                    b"lv": ANY,
                    b"snd": decode_address(get_application_address(REWARDS_APP_ID)),
                    b"type": b"axfer",
                    b"xaid": TINY_ASSET_ID,
                },
            )
            # Assert that claim sheet is marked so after the reward is claimed.
            sheet = RewardClaimSheet(value=self.ledger.boxes[REWARDS_APP_ID][get_account_reward_claim_sheet_box_name(self.user_1_address, 0)])
            self.assertEqual(sheet.is_reward_claimed_for_period(period_index), True)

        # 5. Claim rewards for user_2
        account_powers = get_account_powers(self.ledger, self.user_2_address)
        for period_index in range(3):
            account_power_index_start = get_power_index_at(account_powers, self.first_period_start_timestamp + (WEEK * period_index)) or 0
            account_power_index_end = get_power_index_at(account_powers, self.first_period_start_timestamp + (WEEK * (period_index + 1)))

            # Check that reward isn't claimed before the claim.
            if raw_box := self.ledger.boxes[REWARDS_APP_ID].get(get_account_reward_claim_sheet_box_name(self.user_2_address, 0)):
                sheet = RewardClaimSheet(value=raw_box)
                self.assertEqual(sheet.is_reward_claimed_for_period(period_index), False)

            txn_group = prepare_claim_reward_transactions(
                rewards_app_id=REWARDS_APP_ID,
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_2_address,
                period_index_start=period_index,
                period_count=1,
                account_power_indexes=[account_power_index_start, account_power_index_end],
                create_reward_claim_sheet=not period_index,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(self.user_2_address, self.user_2_sk)
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            app_call_txn = get_first_app_call_txn(block[b"txns"])
            logs = app_call_txn[b"dt"][b"lg"]
            events = decode_logs(logs, events=rewards_events)

            account_cumulative_power_delta = bytes_to_int(app_call_txn[b"dt"][b"itx"][0][b"dt"][b"lg"][0][4:])
            claimed_reward_amount = calculate_reward_amount(account_cumulative_power_delta, reward_periods[period_index])
            self.assertEqual(len(events), 1)
            self.assertEqual(
                events[0],
                {
                    "event_name": "claim_rewards",
                    "user_address": self.user_2_address,
                    "total_reward_amount": ANY,
                    "period_index_start": period_index,
                    "period_count": 1,
                    "reward_amounts": [claimed_reward_amount],
                },
            )
            inner_txns = app_call_txn[b"dt"][b"itx"]
            self.assertEqual(len(inner_txns), 2)
            self.assertEqual(
                inner_txns[1][b"txn"],
                {
                    b"aamt": events[0]["total_reward_amount"],
                    b"arcv": decode_address(self.user_2_address),
                    b"fv": ANY,
                    b"lv": ANY,
                    b"snd": decode_address(get_application_address(REWARDS_APP_ID)),
                    b"type": b"axfer",
                    b"xaid": TINY_ASSET_ID,
                },
            )
            sheet = RewardClaimSheet(value=self.ledger.boxes[REWARDS_APP_ID][get_account_reward_claim_sheet_box_name(self.user_2_address, 0)])
            self.assertEqual(sheet.is_reward_claimed_for_period(period_index), True)

    def test_claim_rewards_two_years(self):
        # 1. Create lock
        # 2. Create checkpoints
        # 3. Create reward periods
        # 4. Claim all rewards
        self.create_and_init_rewards_app()
        self.ledger.move(self.total_reward_amount * 120, asset_id=TINY_ASSET_ID, sender=self.ledger.assets[TINY_ASSET_ID]["creator"], receiver=get_application_address(REWARDS_APP_ID))
        block_timestamp = self.latest_block_timestamp

        # 1. Create locks
        locked_amount = 10_000_000
        lock_start_timestamp = block_timestamp
        lock_end_timestamp = get_start_timestamp_of_week(block_timestamp) + 200 * WEEK

        self.create_lock(self.user_address, self.user_sk, locked_amount, lock_start_timestamp, lock_end_timestamp)
        self.ledger.move(5_000_000_000, asset_id=TINY_ASSET_ID, sender=self.ledger.assets[TINY_ASSET_ID]["creator"], receiver=self.user_address)

        for period_index in range(120):

            # 2. Create checkpoints
            block_timestamp = self.first_period_start_timestamp + (WEEK * (period_index + 1))
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

                # 3. Create reward periods
                txn_group = prepare_create_reward_period_transactions(
                    rewards_app_id=REWARDS_APP_ID,
                    vault_app_id=VAULT_APP_ID,
                    sender=self.user_address,
                    rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                    reward_history_index=get_reward_history_index_at(self.ledger, REWARDS_APP_ID, self.first_period_start_timestamp + (WEEK * period_index)),
                    total_power_period_start_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, self.first_period_start_timestamp + (WEEK * period_index)) or 0,
                    total_power_period_end_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, self.first_period_start_timestamp + (WEEK * (period_index + 1))),
                    suggested_params=self.sp,
                )
                txn_group.sign_with_private_key(self.user_address, self.user_sk)
                self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

                if period_index % 2:
                    txn_group = prepare_increase_lock_amount_transactions(
                        vault_app_id=VAULT_APP_ID,
                        tiny_asset_id=TINY_ASSET_ID,
                        sender=self.user_address,
                        locked_amount=locked_amount,
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
                        reward_amount=self.total_reward_amount + period_index * 1_000,
                        sender=self.app_creator_address,
                        suggested_params=self.sp,
                    )
                    txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
                    self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        account_powers = get_account_powers(self.ledger, self.user_address)
        period_index_start = 0
        period_count = 104
        account_power_indexes = [get_power_index_at(account_powers, self.first_period_start_timestamp + (WEEK * (period_index_start + i))) or 0 for i in range(period_count + 1)]
        reward_periods = get_reward_periods(self.ledger)

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
        app_call_txn = get_first_app_call_txn(block[b"txns"])
        logs = app_call_txn[b"dt"][b"lg"]
        events = decode_logs(logs, events=rewards_events)

        # Calculate reward amount for each period.
        reward_amounts = []
        for period_index in range(period_count):
            account_cumulative_power_delta = bytes_to_int(app_call_txn[b"dt"][b"itx"][period_index][b"dt"][b"lg"][0][4:])
            claimed_reward_amount = calculate_reward_amount(account_cumulative_power_delta, reward_periods[period_index])
            reward_amounts.append(claimed_reward_amount)

        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0],
            {
                "event_name": "claim_rewards",
                "user_address": self.user_address,
                "total_reward_amount": ANY,
                "period_index_start": period_index_start,
                "period_count": period_count,
                "reward_amounts": reward_amounts,
            },
        )
        self.assertEqual(len(events[0]["reward_amounts"]), 104)
        inner_txns = app_call_txn[b"dt"][b"itx"]
        self.assertEqual(len(inner_txns), 104 + 1)
        self.assertEqual(
            inner_txns[-1][b"txn"],
            {
                b"aamt": events[0]["total_reward_amount"],
                b"arcv": decode_address(self.user_address),
                b"fv": ANY,
                b"lv": ANY,
                b"snd": decode_address(get_application_address(REWARDS_APP_ID)),
                b"type": b"axfer",
                b"xaid": TINY_ASSET_ID,
            },
        )

        sheet = RewardClaimSheet(value=self.ledger.boxes[REWARDS_APP_ID][get_account_reward_claim_sheet_box_name(self.user_address, 0)])
        self.assertEqual(all(sheet.claim_sheet[:104]), True)
        self.assertEqual(sum(sheet.claim_sheet[104:]), 0)

    def test_budget_increase(self):
        self.create_rewards_app(self.app_creator_address)

        txn = _prepare_budget_increase_transaction(sender=self.user_address, sp=self.sp, index=REWARDS_APP_ID, extra_app_args=[0])
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
        app_call_txn = get_first_app_call_txn(block[b"txns"], ignore_budget_increase=False)
        inner_txns = app_call_txn[b"dt"][b"itx"]
        self.assertEqual(len(inner_txns), 2)

    def test_set_reward_amount(self):
        self.create_and_init_rewards_app()
        block_timestamp = self.latest_block_timestamp

        self.ledger.global_states[REWARDS_APP_ID][REWARDS_MANAGER_KEY] = decode_address(self.user_address)

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
        self.assertEqual(e.exception.source["line"], "assert(user_address == app_global_get(REWARDS_MANAGER_KEY))")

        for i in range(20):
            new_reward_amount = 2_000_000 + i
            block_timestamp = block_timestamp + i
            txn_group = prepare_set_reward_amount_transactions(
                rewards_app_id=REWARDS_APP_ID,
                rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                reward_amount=new_reward_amount,
                sender=self.user_address,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(self.user_address, self.user_sk)
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            app_call_txn = get_first_app_call_txn(block[b"txns"])
            logs = app_call_txn[b"dt"][b"lg"]
            events = decode_logs(logs, events=rewards_events)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0], {"event_name": "reward_history", "index": i + 1, "timestamp": block_timestamp, "reward_amount": new_reward_amount})
            self.assertEqual(events[1], {"event_name": "set_reward_amount", "timestamp": block_timestamp, "reward_amount": new_reward_amount})
            reward_history = get_reward_histories(self.ledger)[-1]
            self.assertEqual(
                reward_history,
                RewardHistory(
                    timestamp=block_timestamp,
                    reward_amount=new_reward_amount,
                ),
            )

    def test_set_manager(self):
        self.create_and_init_rewards_app()

        # Test address validation
        txn_group = prepare_set_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.user_address,
            new_manager_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source["line"], "assert(user_address == app_global_get(MANAGER_KEY))")

        # Set user as manager
        txn_group = prepare_set_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b"txns"][0][b"dt"][b"lg"]
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(events[0], {"event_name": "set_manager", "manager": self.user_address})

        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(rewards_app_global_state.manager, decode_address(self.user_address))

        # Set back app creator as manager
        txn_group = prepare_set_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.user_address,
            new_manager_address=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b"txns"][0][b"dt"][b"lg"]
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(events[0], {"event_name": "set_manager", "manager": self.app_creator_address})

        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(rewards_app_global_state.manager, decode_address(self.app_creator_address))

    def test_set_rewards_manager(self):
        self.create_rewards_app(self.app_creator_address)

        # Test address validation
        txn_group = prepare_set_rewards_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.user_address,
            new_manager_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_address, self.user_sk)
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(txn_group.signed_transactions)
        self.assertEqual(e.exception.source["line"], "assert(user_address == app_global_get(MANAGER_KEY))")

        # Set user as manager
        txn_group = prepare_set_rewards_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=self.user_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b"txns"][0][b"dt"][b"lg"]
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(events[0], {"event_name": "set_rewards_manager", "rewards_manager": self.user_address})

        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(rewards_app_global_state.rewards_manager, decode_address(self.user_address))

        # Set back app creator as manager
        txn_group = prepare_set_rewards_manager_transactions(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.app_creator_address,
            new_manager_address=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)
        logs = block[b"txns"][0][b"dt"][b"lg"]
        events = decode_logs(logs, events=rewards_events)
        self.assertEqual(len(events), 1)
        self.assertDictEqual(events[0], {"event_name": "set_rewards_manager", "rewards_manager": self.app_creator_address})

        # Global state
        rewards_app_global_state = get_rewards_app_global_state(self.ledger, REWARDS_APP_ID)
        self.assertEqual(rewards_app_global_state.rewards_manager, decode_address(self.app_creator_address))

    def test_get_box(self):
        self.create_and_init_rewards_app()
        block_timestamp = self.latest_block_timestamp

        lock_start_timestamp = block_timestamp
        lock_end_timestamp_1 = get_start_timestamp_of_week(block_timestamp) + 10 * WEEK

        self.create_lock(self.user_1_address, self.user_1_sk, 20_000_000, lock_start_timestamp, lock_end_timestamp_1)

        # Create reward periods
        for period_index in range(3):
            # Create checkpoints
            block_timestamp = self.first_period_start_timestamp + (WEEK * (period_index + 1))
            with unittest.mock.patch("time.time", return_value=block_timestamp):
                self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

                txn_group = prepare_create_reward_period_transactions(
                    rewards_app_id=REWARDS_APP_ID,
                    vault_app_id=VAULT_APP_ID,
                    sender=self.user_address,
                    rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                    reward_history_index=get_reward_history_index_at(self.ledger, REWARDS_APP_ID, self.first_period_start_timestamp + (WEEK * period_index)),
                    total_power_period_start_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, self.first_period_start_timestamp + (WEEK * period_index)) or 0,
                    total_power_period_end_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, self.first_period_start_timestamp + (WEEK * (period_index + 1))),
                    suggested_params=self.sp,
                )
                txn_group.sign_with_private_key(self.user_address, self.user_sk)
                block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
                app_call_txn = get_first_app_call_txn(block[b"txns"])
                logs = app_call_txn[b"dt"][b"lg"]
                events = decode_logs(logs, events=rewards_events)
                self.assertEqual(len(events), 2)
                self.assertEqual(events[0], {"event_name": "reward_period", "index": period_index, "total_reward_amount": self.total_reward_amount, "total_cumulative_power_delta": ANY})
                self.assertEqual(events[1], {"event_name": "create_reward_period", "index": period_index, "total_reward_amount": self.total_reward_amount, "total_cumulative_power_delta": ANY})

        account_powers = get_account_powers(self.ledger, self.user_1_address)
        for period_index in range(3):
            period_start_timestamp = self.first_period_start_timestamp + (WEEK * period_index)
            period_end_timestamp = period_start_timestamp + WEEK
            account_power_index_start = get_power_index_at(account_powers, period_start_timestamp) or 0
            account_power_index_end = get_power_index_at(account_powers, period_end_timestamp)

            txn_group = prepare_claim_reward_transactions(
                rewards_app_id=REWARDS_APP_ID,
                vault_app_id=VAULT_APP_ID,
                tiny_asset_id=TINY_ASSET_ID,
                sender=self.user_1_address,
                period_index_start=period_index,
                period_count=1,
                account_power_indexes=[account_power_index_start, account_power_index_end],
                create_reward_claim_sheet=not period_index,
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(self.user_1_address, self.user_1_sk)
            block = self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Get box
        txn_group = prepare_get_box_transaction(
            rewards_app_id=REWARDS_APP_ID,
            sender=self.user_1_address,
            box_name=get_account_reward_claim_sheet_box_name(self.user_1_address, 0),
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.user_1_address, self.user_1_sk)
        block = self.ledger.eval_transactions(txn_group.signed_transactions)

        _sheet = RewardClaimSheet(value=block[b"txns"][0][b"dt"][b"lg"][-1])
        sheet = RewardClaimSheet(value=self.ledger.boxes[REWARDS_APP_ID][get_account_reward_claim_sheet_box_name(self.user_1_address, 0)])
        self.assertEqual(_sheet, sheet)

    def test_create_reward_period_with_invalid_reward_amount_index(self):
        self.create_and_init_rewards_app()
        block_timestamp = self.latest_block_timestamp

        lock_start_timestamp = block_timestamp
        lock_end_timestamp_1 = get_start_timestamp_of_week(block_timestamp) + 10 * WEEK

        self.create_lock(self.user_address, self.user_sk, 20_000_000, lock_start_timestamp, lock_end_timestamp_1)

        txn_group = prepare_set_reward_amount_transactions(
            rewards_app_id=REWARDS_APP_ID,
            rewards_app_global_state=get_rewards_app_global_state(self.ledger),
            reward_amount=self.total_reward_amount * 2,
            sender=self.app_creator_address,
            suggested_params=self.sp,
        )
        txn_group.sign_with_private_key(self.app_creator_address, self.app_creator_sk)
        self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)

        # Create reward period
        # Create checkpoints
        block_timestamp = self.first_period_start_timestamp + WEEK
        with unittest.mock.patch("time.time", return_value=block_timestamp):
            self.create_checkpoints(self.user_address, self.user_sk, block_timestamp)

            txn_group = prepare_create_reward_period_transactions(
                rewards_app_id=REWARDS_APP_ID,
                vault_app_id=VAULT_APP_ID,
                sender=self.user_address,
                rewards_app_global_state=get_rewards_app_global_state(self.ledger),
                reward_history_index=0,
                total_power_period_start_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, self.first_period_start_timestamp) or 0,
                total_power_period_end_index=get_total_power_index_at(self.ledger, VAULT_APP_ID, self.first_period_start_timestamp + WEEK),
                suggested_params=self.sp,
            )
            txn_group.sign_with_private_key(self.user_address, self.user_sk)
            with self.assertRaises(LogicEvalError) as e:
                self.ledger.eval_transactions(txn_group.signed_transactions, block_timestamp=block_timestamp)
            self.assertEqual(e.exception.source["line"], "assert(timestamp < next_reward_history.timestamp)")
