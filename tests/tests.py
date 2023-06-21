import uuid

from algosdk import transaction
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from tests.common import BaseTestCase
from tests.constants import TOTAL_POWERS, DAY, SLOPE_CHANGES, WEEK, ACCOUNT_STATE_SIZE, ACCOUNT_POWER_BOX_SIZE, SLOPE_CHANGE_SIZE, ACCOUNT_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_SIZE
from tests.utils import itob, get_start_timestamp_of_week, parse_box_account_state, get_latest_total_powers_indexes, get_latest_checkpoint_timestamp, get_required_minimum_balance_of_box, get_latest_account_power_indexes, get_account_power_index_at, get_total_power_index_at


def get_budget_increase_txn(sender, sp, index, boxes):
    return transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=sp,
        index=index,
        app_args=["increase_budget"],
        boxes=boxes + ([(0, "")] * (8 - len(boxes))),
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

    def get_create_lock_txn_group(self, user_address, locked_amount, lock_end_timestamp, app_id):
        latest_total_power_box_index, total_power_array_index = get_latest_total_powers_indexes(self.ledger, app_id)

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
        box_index, array_index = get_latest_total_powers_indexes(self.ledger, app_id)
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

    def get_increase_lock_amount_txn_group(self, user_address, locked_amount, lock_end_timestamp, block_timestamp, app_id):
        total_powers_box_index, total_powers_array_index = get_latest_total_powers_indexes(self.ledger, app_id)
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
            get_budget_increase_txn(self.user_address, sp=self.sp, index=self.app_id),
        ]
        return txn_group

    def get_extend_lock_end_time_txn_group(self, user_address, old_lock_end_timestamp, new_lock_end_timestamp, block_timestamp, app_id):
        total_powers_box_index, total_powers_array_index = get_latest_total_powers_indexes(self.ledger, app_id)
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
            get_budget_increase_txn(self.user_address, sp=self.sp, index=self.app_id),
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
        total_powers_box_index, _ = get_latest_total_powers_indexes(self.ledger, app_id)
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