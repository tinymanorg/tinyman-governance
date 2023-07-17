import uuid

from algosdk import transaction
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address

from common.constants import WEEK, TINY_ASSET_ID, VAULT_APP_ID
from common.utils import itob, get_latest_total_powers_indexes, get_required_minimum_balance_of_box, get_latest_account_power_indexes, get_latest_checkpoint_timestamp, get_start_timestamp_of_week, get_account_power_index_at, get_total_power_index_at, parse_box_account_state
from vault.constants import ACCOUNT_POWER_BOX_ARRAY_LEN, TOTAL_POWERS, SLOPE_CHANGES, ACCOUNT_STATE_SIZE, ACCOUNT_POWER_BOX_SIZE, SLOPE_CHANGE_SIZE, TOTAL_POWER_BOX_ARRAY_LEN, TOTAL_POWER_BOX_SIZE, VAULT_APP_MINIMUM_BALANCE_REQUIREMENT


def prepare_budget_increase_txn(sender, sp, index, foreign_apps=None, boxes=None):
    if foreign_apps is None:
        foreign_apps = []

    if boxes is None:
        boxes = []
    boxes = boxes + ([(0, "")] * ((8 - len(foreign_apps)) - len(boxes)))

    return transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=sp,
        index=index,
        app_args=["increase_budget"],
        foreign_apps=foreign_apps,
        boxes=boxes,
        # Make transactions unique to avoid "transaction already in ledger" error
        note=uuid.uuid4().bytes
    )


def prepare_init_txn_group(app_id, user_address, sp):
    total_powers_box_name = TOTAL_POWERS + itob(0)
    txn_group = [
        transaction.PaymentTxn(
            sender=user_address,
            sp=sp,
            receiver=get_application_address(app_id),
            amt=VAULT_APP_MINIMUM_BALANCE_REQUIREMENT,
        ),
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=app_id,
            app_args=[
                "init",
            ],
            foreign_assets=[
                TINY_ASSET_ID
            ],
            boxes=[
                (0, total_powers_box_name),
            ]
        ),
        prepare_budget_increase_txn(user_address, sp=sp, index=app_id),
    ]
    txn_group[1].fee *= 2
    return txn_group

def prepare_create_lock_txn_group(ledger, user_address, locked_amount, lock_end_timestamp, sp):
    min_balance_increase = 0

    # Account State
    account_state_box_name = decode_address(user_address)
    boxes = [
        (VAULT_APP_ID, account_state_box_name),
    ]
    
    # Account Power
    if account_state_box_name not in ledger.boxes[VAULT_APP_ID]:
        min_balance_increase += get_required_minimum_balance_of_box(account_state_box_name, ACCOUNT_STATE_SIZE)
        account_power_box_name = decode_address(user_address) + itob(0)
        min_balance_increase += get_required_minimum_balance_of_box(account_power_box_name, ACCOUNT_POWER_BOX_SIZE)

        boxes += [
            (VAULT_APP_ID, account_power_box_name),
        ]
    else:
        latest_account_power_box_index, latest_account_power_array_index, is_full = get_latest_account_power_indexes(ledger, VAULT_APP_ID, user_address)
        latest_account_power_box_name = decode_address(user_address) + itob(latest_account_power_box_index)
        boxes += [
            (VAULT_APP_ID, latest_account_power_box_name),
        ]

        if is_full:
            next_account_power_box_name = decode_address(user_address) + itob(latest_account_power_box_index + 1)
            min_balance_increase += get_required_minimum_balance_of_box(next_account_power_box_name, ACCOUNT_POWER_BOX_SIZE)
            boxes += [
                (VAULT_APP_ID, next_account_power_box_name),
            ]

    # Slope
    slope_change_box_name = SLOPE_CHANGES + itob(lock_end_timestamp)
    if slope_change_box_name not in ledger.boxes[VAULT_APP_ID]:
        min_balance_increase += get_required_minimum_balance_of_box(slope_change_box_name, SLOPE_CHANGE_SIZE)

    # Total Powers
    latest_total_power_box_index, total_power_array_index, is_full = get_latest_total_powers_indexes(ledger, VAULT_APP_ID)
    latest_total_power_box_name = TOTAL_POWERS + itob(latest_total_power_box_index)
    next_total_power_box_name = TOTAL_POWERS + itob(latest_total_power_box_index + 1)
    if is_full:
        min_balance_increase += get_required_minimum_balance_of_box(next_total_power_box_name, TOTAL_POWER_BOX_SIZE)

    boxes += [
        (VAULT_APP_ID, slope_change_box_name),
        (VAULT_APP_ID, latest_total_power_box_name),
        (VAULT_APP_ID, next_total_power_box_name),
    ]
    txn_group = [
        transaction.AssetTransferTxn(
            index=TINY_ASSET_ID,
            sender=user_address,
            receiver=get_application_address(VAULT_APP_ID),
            amt=locked_amount,
            sp=sp,
        ),
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=[
                "create_lock",
                lock_end_timestamp,
            ],
            boxes=boxes
        ),
    ]

    if min_balance_increase:
        txn_group = [
            transaction.PaymentTxn(
                sender=user_address,
                sp=sp,
                receiver=get_application_address(VAULT_APP_ID),
                amt=min_balance_increase,
            )
        ] + txn_group

    if account_state_box := ledger.boxes[VAULT_APP_ID].get(decode_address(user_address)):
        account_state = parse_box_account_state(account_state_box)
        power_count = account_state["power_count"]

        if power_count:
            txn_group.append(
                prepare_budget_increase_txn(user_address, sp=sp, index=VAULT_APP_ID)
            )
    return txn_group

def prepare_create_checkpoints_txn_group(ledger, user_address, block_timestamp, sp):
    box_index, array_index, _ = get_latest_total_powers_indexes(ledger, VAULT_APP_ID)
    latest_checkpoint_timestamp = get_latest_checkpoint_timestamp(ledger, VAULT_APP_ID)
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
                sp=sp,
                receiver=get_application_address(VAULT_APP_ID),
                amt=get_required_minimum_balance_of_box(new_total_powers_box_name, TOTAL_POWER_BOX_SIZE)
            )
        )

    if new_checkpoint_count:
        txn_group += [
            transaction.ApplicationNoOpTxn(
                sender=user_address,
                sp=sp,
                index=VAULT_APP_ID,
                app_args=[
                    "create_checkpoints",
                ],
                boxes=[
                    (0, TOTAL_POWERS + itob(box_index)),
                    (0, TOTAL_POWERS + itob(box_index + 1)),
                    *slope_change_boxes,
                ]
            ),
            *[prepare_budget_increase_txn(user_address, sp=sp, index=VAULT_APP_ID) for _ in range(increase_txn_count)],
        ]
    return txn_group

def prepare_increase_lock_amount_txn_group(ledger, user_address, locked_amount, lock_end_timestamp, block_timestamp, sp):
    total_powers_box_index, total_powers_array_index, _ = get_latest_total_powers_indexes(ledger, VAULT_APP_ID)
    account_power_box_index, account_power_array_index, account_power_is_full = get_latest_account_power_indexes(ledger, VAULT_APP_ID, user_address)

    payment_amount = 0
    new_total_power_count = 1
    account_state_box_name = decode_address(user_address)
    account_power_box_name = decode_address(user_address) + itob(account_power_box_index)
    total_powers_box_name = TOTAL_POWERS + itob(total_powers_box_index)
    next_total_powers_box_name = TOTAL_POWERS + itob(total_powers_box_index + 1)
    account_slope_change_box_name = SLOPE_CHANGES + itob(lock_end_timestamp)
    boxes = [
        (0, account_state_box_name),
        (0, account_power_box_name),
        (0, total_powers_box_name),
        (0, next_total_powers_box_name),
        (0, account_slope_change_box_name),
    ]

    # TODO: This logic assumes that the checkpoint/total_power is created at least week.
    latest_checkpoint_timestamp = get_latest_checkpoint_timestamp(ledger, VAULT_APP_ID)
    latest_checkpoint_week_timestamp = get_start_timestamp_of_week(latest_checkpoint_timestamp)
    start_timestamp_of_this_week = get_start_timestamp_of_week(block_timestamp)
    if latest_checkpoint_week_timestamp != start_timestamp_of_this_week:
        new_total_power_count += 1
        weekly_slope_change_box_name = SLOPE_CHANGES + itob(start_timestamp_of_this_week)
        boxes.append((0, weekly_slope_change_box_name))

    if account_power_is_full:
        new_account_power_box_name = decode_address(user_address) + itob(account_power_box_index + 1)
        boxes.append((0, new_account_power_box_name))
        payment_amount += get_required_minimum_balance_of_box(new_account_power_box_name, ACCOUNT_POWER_BOX_SIZE)

    if total_powers_array_index + new_total_power_count >= TOTAL_POWER_BOX_ARRAY_LEN:
        payment_amount += get_required_minimum_balance_of_box(next_total_powers_box_name, TOTAL_POWER_BOX_SIZE)

    txn_group = [
        transaction.AssetTransferTxn(
            index=TINY_ASSET_ID,
            sender=user_address,
            receiver=get_application_address(VAULT_APP_ID),
            amt=locked_amount,
            sp=sp,
        ),
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=[
                "increase_lock_amount",
            ],
            boxes=boxes
        ),
        prepare_budget_increase_txn(user_address, sp=sp, index=VAULT_APP_ID),
    ]

    if payment_amount:
        txn_group = [
            transaction.PaymentTxn(
                sender=user_address,
                sp=sp,
                receiver=get_application_address(VAULT_APP_ID),
                amt=payment_amount
            )
        ] + txn_group

    return txn_group

def prepare_extend_lock_end_time_txn_group(ledger, user_address, old_lock_end_timestamp, new_lock_end_timestamp, block_timestamp, sp):
    total_powers_box_index, total_powers_array_index, _ = get_latest_total_powers_indexes(ledger, VAULT_APP_ID)
    account_power_box_index, account_power_array_index, account_power_is_full = get_latest_account_power_indexes(ledger, VAULT_APP_ID, user_address)

    payment_amount = 0
    new_total_power_count = 1
    account_state_box_name = decode_address(user_address)
    account_power_box_name = decode_address(user_address) + itob(account_power_box_index)
    total_powers_box_name = TOTAL_POWERS + itob(total_powers_box_index)
    next_total_powers_box_name = TOTAL_POWERS + itob(total_powers_box_index + 1)

    old_account_slope_change_box_name = SLOPE_CHANGES + itob(old_lock_end_timestamp)
    new_account_slope_change_box_name = SLOPE_CHANGES + itob(new_lock_end_timestamp)
    boxes = [
        (0, account_state_box_name),
        (0, account_power_box_name),
        (0, total_powers_box_name),
        (0, next_total_powers_box_name),
        (0, old_account_slope_change_box_name),
        (0, new_account_slope_change_box_name),
    ]

    latest_checkpoint_timestamp = get_latest_checkpoint_timestamp(ledger, VAULT_APP_ID)
    latest_checkpoint_week_timestamp = get_start_timestamp_of_week(latest_checkpoint_timestamp)
    start_timestamp_of_this_week = get_start_timestamp_of_week(block_timestamp)
    if latest_checkpoint_week_timestamp != start_timestamp_of_this_week:
        new_total_power_count += 1
        weekly_slope_change_box_name = SLOPE_CHANGES + itob(start_timestamp_of_this_week)
        boxes.append((0, weekly_slope_change_box_name))

    if new_account_slope_change_box_name not in ledger.boxes[VAULT_APP_ID]:
        payment_amount += get_required_minimum_balance_of_box(new_account_slope_change_box_name, SLOPE_CHANGE_SIZE)

    if account_power_is_full:
        new_account_power_box_name = decode_address(user_address) + itob(account_power_box_index + 1)
        boxes.append((0, new_account_power_box_name))
        payment_amount += get_required_minimum_balance_of_box(new_account_power_box_name, ACCOUNT_POWER_BOX_SIZE)

    if total_powers_array_index + new_total_power_count >= TOTAL_POWER_BOX_ARRAY_LEN:
        payment_amount += get_required_minimum_balance_of_box(next_total_powers_box_name, TOTAL_POWER_BOX_SIZE)

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=[
                "extend_lock_end_time",
                new_lock_end_timestamp
            ],
            boxes=boxes,
        ),
        prepare_budget_increase_txn(user_address, sp=sp, index=VAULT_APP_ID),
    ]

    if payment_amount:
        txn_group = [
            transaction.PaymentTxn(
                sender=user_address,
                sp=sp,
                receiver=get_application_address(VAULT_APP_ID),
                amt=payment_amount
            )
        ] + txn_group

    return txn_group

def prepare_withdraw_txn_group(ledger, user_address, sp):
    account_power_box_index, account_power_array_index, account_power_is_full = get_latest_account_power_indexes(ledger, VAULT_APP_ID, user_address)

    account_state_box_name = decode_address(user_address)
    account_power_box_name = decode_address(user_address) + itob(account_power_box_index)
    boxes = [
        (0, account_state_box_name),
        (0, account_power_box_name),
    ]

    if account_power_is_full:
        next_account_power_box_name = decode_address(user_address) + itob(account_power_box_index + 1)
        boxes += [
            (0, next_account_power_box_name)
        ]

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["withdraw"],
            foreign_assets=[TINY_ASSET_ID],
            boxes=boxes
        )
    ]
    txn_group[0].fee *= 2
    return txn_group

def prepare_get_tiny_power_of_txn_group(user_address, sp):
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["get_tiny_power_of", decode_address(user_address)],
            boxes=[
                (0, decode_address(user_address)),
            ]
        )
    ]
    return txn_group

def prepare_get_tiny_power_of_at_txn_group(ledger, user_address, timestamp, sp):
    account_power_index = get_account_power_index_at(ledger, VAULT_APP_ID, user_address, timestamp)
    if account_power_index is None:
        account_power_index = 0
        account_power_box_index = 0
    else:
        account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["get_tiny_power_of_at", decode_address(user_address), timestamp, account_power_index],
            boxes=[
                (0, decode_address(user_address)),
                (0, decode_address(user_address) + itob(account_power_box_index)),
                (0, decode_address(user_address) + itob(account_power_box_index + 1)),
            ]
        )
    ]
    return txn_group

def prepare_get_total_tiny_power_txn_group(ledger, user_address, sp):
    total_powers_box_index, _, _ = get_latest_total_powers_indexes(ledger, VAULT_APP_ID)
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["get_total_tiny_power"],
            boxes=[
                (0, TOTAL_POWERS + itob(total_powers_box_index)),
            ]
        )
    ]
    return txn_group

def prepare_get_total_tiny_power_of_at_txn_group(ledger, user_address, timestamp, sp):
    total_power_index = get_total_power_index_at(ledger, VAULT_APP_ID, timestamp)
    if total_power_index is None:
        total_power_index = 0
        total_power_box_index = 0
    else:
        total_power_box_index = total_power_index // TOTAL_POWER_BOX_ARRAY_LEN

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["get_total_tiny_power_at", timestamp, total_power_index],
            boxes=[
                (0, TOTAL_POWERS + itob(total_power_box_index)),
                (0, TOTAL_POWERS + itob(total_power_box_index + 1)),
            ]
        )
    ]
    return txn_group

def prepare_get_total_cumulative_power_at_txn_group(ledger, user_address, timestamp, sp):
    total_power_index = get_total_power_index_at(ledger, VAULT_APP_ID, timestamp)
    if total_power_index is None:
        total_power_index = 0
        total_power_box_index = 0
    else:
        total_power_box_index = total_power_index // TOTAL_POWER_BOX_ARRAY_LEN

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["get_total_cumulative_power_at", timestamp, total_power_index],
            boxes=[
                (0, TOTAL_POWERS + itob(total_power_box_index)),
                (0, TOTAL_POWERS + itob(total_power_box_index + 1)),
            ]
        )
    ]
    return txn_group

def prepare_get_cumulative_power_of_at_txn_group(ledger, user_address, timestamp, sp):
    account_power_index = get_account_power_index_at(ledger, VAULT_APP_ID, user_address, timestamp)
    if account_power_index is None:
        account_power_index = 0
        account_power_box_index = 0
    else:
        account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=VAULT_APP_ID,
            app_args=["get_cumulative_power_of_at", decode_address(user_address), timestamp, account_power_index],
            boxes=[
                (0, decode_address(user_address)),
                (0, decode_address(user_address) + itob(account_power_box_index)),
                (0, decode_address(user_address) + itob(account_power_box_index + 1)),
            ]
        )
    ]
    return txn_group