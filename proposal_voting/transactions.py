from algosdk import transaction
from algosdk.encoding import decode_address
from algosdk.logic import get_application_address
from tinyman.utils import int_to_bytes

from common.utils import get_latest_total_powers_indexes, parse_box_staking_proposal, get_account_power_index_at, get_required_minimum_balance_of_box
from tinyman.governance.vault.constants import TOTAL_POWERS, ACCOUNT_POWER_BOX_ARRAY_LEN
from proposal_voting.constants import PROPOSAL_BOX_PREFIX, ATTENDANCE_BOX_PREFIX
from common.constants import PROPOSAL_VOTING_APP_ID, VAULT_APP_ID


def prepare_create_proposal_transactions(ledger, user_address, proposal_id, sp):
    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
    account_state_box_name = decode_address(user_address)
    latest_total_powers_box_index, _, _ = get_latest_total_powers_indexes(ledger, VAULT_APP_ID)
    latest_total_powers_box_name = TOTAL_POWERS + int_to_bytes(latest_total_powers_box_index)

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=PROPOSAL_VOTING_APP_ID,
            app_args=["create_proposal", proposal_id],
            foreign_apps=[VAULT_APP_ID],
            boxes=[
                (PROPOSAL_VOTING_APP_ID, proposal_box_name),
                (VAULT_APP_ID, account_state_box_name),
                (VAULT_APP_ID, latest_total_powers_box_name)
            ]
        )
    ]
    # 2 inner txns
    txn_group[0].fee *= 3
    return txn_group


def prepare_cast_vote_transactions(ledger, user_address, proposal_id, vote, proposal_creation_timestamp, sp):
    assert vote in [0, 1, 2]

    account_power_index = get_account_power_index_at(ledger, VAULT_APP_ID, user_address, proposal_creation_timestamp)
    # assert account_power_index is not None
    account_power_box_index = account_power_index // ACCOUNT_POWER_BOX_ARRAY_LEN

    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id
    proposal_index = parse_box_staking_proposal(ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])["index"]
    account_attendance_box_index = proposal_index // (1024 * 8)
    account_attendance_box_name = ATTENDANCE_BOX_PREFIX + decode_address(user_address) + int_to_bytes(account_attendance_box_index)

    boxes = [
        (PROPOSAL_VOTING_APP_ID, proposal_box_name),
        (PROPOSAL_VOTING_APP_ID, account_attendance_box_name),
        (VAULT_APP_ID, decode_address(user_address)),
        (VAULT_APP_ID, decode_address(user_address) + int_to_bytes(account_power_box_index)),
    ]
    if not (account_power_index + 1) % ACCOUNT_POWER_BOX_ARRAY_LEN:
        boxes.append(
            (VAULT_APP_ID, decode_address(user_address) + int_to_bytes(account_power_box_index + 1)),
        )

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=PROPOSAL_VOTING_APP_ID,
            app_args=["cast_vote", proposal_id, vote, account_power_index],
            foreign_apps=[VAULT_APP_ID],
            boxes=boxes
        ),
    ]
    txn_group[0].fee *= 2

    payment_amount = 0
    if account_attendance_box_name not in ledger.boxes[PROPOSAL_VOTING_APP_ID]:
        payment_amount += get_required_minimum_balance_of_box(account_attendance_box_name, 24)

    if payment_amount:
        txn_group = [
            transaction.PaymentTxn(
                sender=user_address,
                sp=sp,
                receiver=get_application_address(PROPOSAL_VOTING_APP_ID),
                amt=payment_amount,
            )
        ] + txn_group
    return txn_group


def prepare_get_proposal_transactions(user_address, proposal_id, sp):
    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id

    boxes = [
        (PROPOSAL_VOTING_APP_ID, proposal_box_name),
    ]

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=PROPOSAL_VOTING_APP_ID,
            app_args=["get_proposal", proposal_id],
            boxes=boxes
        ),
    ]

    return txn_group


def prepare_has_voted_transactions(ledger, user_address, proposal_id, sp):
    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id

    proposal_index = parse_box_staking_proposal(ledger.boxes[PROPOSAL_VOTING_APP_ID][proposal_box_name])["index"]
    account_attendance_box_index = proposal_index // (1024 * 8)
    account_attendance_box_name = ATTENDANCE_BOX_PREFIX + decode_address(user_address) + int_to_bytes(account_attendance_box_index)

    boxes = [
        (PROPOSAL_VOTING_APP_ID, proposal_box_name),
        (PROPOSAL_VOTING_APP_ID, account_attendance_box_name),
    ]

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=PROPOSAL_VOTING_APP_ID,
            app_args=["has_voted", proposal_id, decode_address(user_address)],
            boxes=boxes
        ),
    ]

    return txn_group


def prepare_cancel_proposal_transactions(user_address, proposal_id, sp):
    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id

    boxes = [
        (PROPOSAL_VOTING_APP_ID, proposal_box_name),
    ]

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=PROPOSAL_VOTING_APP_ID,
            app_args=["cancel_proposal", proposal_id],
            boxes=boxes
        ),
    ]

    return txn_group


def prepare_execute_proposal_transactions(user_address, proposal_id, sp):
    proposal_box_name = PROPOSAL_BOX_PREFIX + proposal_id

    boxes = [
        (PROPOSAL_VOTING_APP_ID, proposal_box_name),
    ]

    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=PROPOSAL_VOTING_APP_ID,
            app_args=["execute_proposal", proposal_id],
            boxes=boxes
        ),
    ]

    return txn_group


def prepare_set_manager_transactions(user_address, new_manager_address, sp):
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=PROPOSAL_VOTING_APP_ID,
            app_args=["set_manager", decode_address(new_manager_address)],
        ),
    ]

    return txn_group


def prepare_set_proposal_manager_transactions(user_address, new_manager_address, sp):
    txn_group = [
        transaction.ApplicationNoOpTxn(
            sender=user_address,
            sp=sp,
            index=PROPOSAL_VOTING_APP_ID,
            app_args=["set_proposal_manager", decode_address(new_manager_address)],
        ),
    ]

    return txn_group
